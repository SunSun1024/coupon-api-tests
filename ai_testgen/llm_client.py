"""封装 OpenAI-compatible Chat Completions 调用、解析、错误和用量统计。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic import BaseModel, ValidationError

from ai_testgen.config import LLMSettings
from ai_testgen.models import TokenUsage


logger = logging.getLogger(__name__)


class LLMGenerationError(RuntimeError):
    """模型服务调用失败或拒绝生成；raw_text=None 表示没有收到可保存的正文。"""

    def __init__(
        self,
        message: str,
        *,
        raw_text: str | None = None,
        usage: TokenUsage | None = None,
    ):
        self.raw_text = raw_text
        self.usage = usage or TokenUsage()
        super().__init__(message)


class JSONParseError(ValueError):
    """响应正文不是合法 JSON，与“合法 JSON 但字段不符合模型”分开处理。"""

    def __init__(
        self,
        message: str,
        *,
        raw_text: str | None = None,
        usage: TokenUsage | None = None,
    ):
        self.raw_text = raw_text
        self.usage = usage or TokenUsage()
        super().__init__(message)


class OutputTruncatedError(JSONParseError):
    """模型以 finish_reason=length 结束；生成器可据此缩小批次后再试。"""


class OutputSchemaError(ValueError):
    """JSON 语法正确，但字段或类型不符合目标 Pydantic 模型。"""

    def __init__(
        self,
        message: str,
        *,
        raw_text: str | None = None,
        usage: TokenUsage | None = None,
    ):
        self.raw_text = raw_text
        self.usage = usage or TokenUsage()
        super().__init__(message)


@dataclass(frozen=True)
class LLMResult:
    """一次成功调用的原始正文、校验后对象、token 用量和模型类型。"""

    raw_text: str
    parsed: BaseModel
    usage: TokenUsage
    response_model: str


class OpenAICompatibleClient:
    """调用 Chat Completions，优先用模型原生结构化输出，也可退回 JSON。

    `auto` 模式只有在服务明确不支持 schema 参数时才降级；鉴权、超时和网络错误
    会原样归类为调用失败，避免第二次请求掩盖真正的问题。
    """

    def __init__(self, settings: LLMSettings, *, sdk_client: Any | None = None):
        self.settings = settings
        # 依赖注入让单元测试能传入 Mock，不要求测试环境有密钥或连接模型服务。
        self._sdk_client = (
            sdk_client if sdk_client is not None else self._create_sdk_client(settings)
        )

    def generate_structured(
        self,
        *,
        messages: Sequence[dict[str, str]],
        response_model: type[BaseModel],
    ) -> LLMResult:
        """按配置选择原生 schema 或 JSON 模式，并统一转换为本地模型对象。"""
        mode = self.settings.output_mode
        if mode in {"auto", "native"}:
            try:
                # SDK 会把 Pydantic 类型转换成结构化输出要求，并尝试返回 parsed 对象。
                completion = self._sdk_client.chat.completions.parse(
                    model=self.settings.model_name,
                    messages=list(messages),
                    response_format=response_model,
                    max_tokens=self.settings.max_output_tokens,
                )
            except ValidationError as exc:
                # 这是响应结构不符合 Pydantic 契约，不应误当成网络/供应商错误降级重试。
                raise _schema_error(exc) from None
            except Exception as exc:
                if _is_sdk_length_finish_error(exc):
                    completion = getattr(exc, "completion", None)
                    if completion is not None:
                        try:
                            choice, raw_text = _read_choice_and_text(completion)
                            _raise_if_length_terminated(
                                choice, raw_text, _read_usage(completion)
                            )
                        except OutputTruncatedError as truncated:
                            # SDK 在 parse() 阶段抛出的截断同样交给生成器缩小批次。
                            raise truncated from None
                if mode == "auto" and _is_unsupported_structured_output(exc):
                    logger.info("Provider does not support native structured output; using JSON mode")
                    return self._request_json(messages, response_model, mode="json_object")
                # 除“不支持 schema”外不降级，避免把鉴权失败等问题变成额外 API 请求。
                raise _generation_error(exc) from None
            return self._result_from_completion(completion, response_model)

        return self._request_json(messages, response_model, mode=mode)

    def _request_json(
        self,
        messages: Sequence[dict[str, str]],
        response_model: type[BaseModel],
        *,
        mode: str,
    ) -> LLMResult:
        """处理 JSON 路径：取文本、解析 JSON，再做 Pydantic 字段校验。"""
        kwargs: dict[str, Any] = {
            "model": self.settings.model_name,
            "messages": list(messages),
            "max_tokens": self.settings.max_output_tokens,
        }
        if mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        try:
            # JSON Object 只约束 JSON 语法；后面仍要用本地 Pydantic 检查字段和类型。
            completion = self._sdk_client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise _generation_error(exc) from None

        choice, raw_text = _read_choice_and_text(completion)
        usage = _read_usage(completion)
        if getattr(choice.message, "refusal", None):
            raise LLMGenerationError(
                "模型拒绝生成结构化测试用例", raw_text=raw_text, usage=usage
            )
        _raise_if_length_terminated(choice, raw_text, usage)
        parsed = _parse_and_validate(raw_text, response_model, usage=usage)
        return self._build_result(completion, raw_text, parsed, response_model)

    def _result_from_completion(
        self, completion: Any, response_model: type[BaseModel]
    ) -> LLMResult:
        """处理 SDK 原生结构化输出，并兼容 parsed 对象或纯文本响应。"""
        choice, raw_text = _read_choice_and_text(completion)
        usage = _read_usage(completion)
        if getattr(choice.message, "refusal", None):
            raise LLMGenerationError(
                "模型拒绝生成结构化测试用例", raw_text=raw_text, usage=usage
            )
        _raise_if_length_terminated(choice, raw_text, usage)

        parsed_value = getattr(choice.message, "parsed", None)
        if isinstance(parsed_value, response_model):
            parsed = parsed_value
        elif parsed_value is not None:
            try:
                # 兼容 SDK 已解析成 dict 等对象的情况，仍以本地模型作为最终契约。
                parsed = response_model.model_validate(parsed_value)
            except ValidationError as exc:
                raise _schema_error(exc, raw_text=raw_text, usage=usage) from None
        else:
            parsed = _parse_and_validate(raw_text, response_model, usage=usage)
        if not raw_text:
            raw_text = parsed.model_dump_json()
        return self._build_result(completion, raw_text, parsed, response_model)

    def _build_result(
        self,
        completion: Any,
        raw_text: str,
        parsed: BaseModel,
        response_model: type[BaseModel],
    ) -> LLMResult:
        """统一记录安全的用量日志并组装客户端返回值。"""
        usage = _read_usage(completion)
        logger.info(
            "LLM call completed model=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            self.settings.model_name,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.total_tokens,
        )
        return LLMResult(
            raw_text=raw_text,
            parsed=parsed,
            usage=usage,
            response_model=response_model.__name__,
        )

    @staticmethod
    def _create_sdk_client(settings: LLMSettings) -> Any:
        """根据已校验配置创建真实 SDK 客户端；构造失败也转成安全错误。"""
        try:
            # 延迟导入使 `--help` 和纯 Mock 测试在未安装 SDK 时也能启动。
            from openai import OpenAI
        except ImportError:
            raise LLMGenerationError(
                "OpenAI Python SDK is unavailable; install dependencies from requirements.txt"
            ) from None

        # SDK 负责有上限的重试和超时；避免在本层再写循环导致重试次数叠加。
        options: dict[str, Any] = {
            "api_key": settings.api_key,
            "timeout": settings.timeout_seconds,
            "max_retries": settings.max_retries,
        }
        if settings.base_url:
            # 第三方 OpenAI-compatible 服务通过 base_url 复用同一 SDK 调用路径。
            options["base_url"] = settings.base_url
        try:
            return OpenAI(**options)
        except Exception as exc:
            raise _generation_error(exc) from None


def _read_choice_and_text(completion: Any) -> tuple[Any, str]:
    """统一读取 SDK completion，并保留实际收到的空文本与未收到响应的区别。"""
    choices = getattr(completion, "choices", None)
    if not choices:
        raise LLMGenerationError(
            "模型服务返回了空响应", usage=_read_usage(completion)
        )
    choice = choices[0]
    message = getattr(choice, "message", None)
    refusal = getattr(message, "refusal", None)
    content = getattr(message, "content", None)
    if content is None:
        content = refusal if isinstance(refusal, str) else ""
    if not isinstance(content, str):
        raise JSONParseError(
            "模型响应内容不是文本，无法解析为 JSON",
            usage=_read_usage(completion),
        )
    if not content.strip() and getattr(message, "parsed", None) is None and not refusal:
        # 先判断结束原因，否则达到 Token 上限但没有最终正文时会被误报成普通空响应。
        finish_reason = getattr(choice, "finish_reason", None)
        reasoning_content = getattr(message, "reasoning_content", None)
        if finish_reason == "length":
            detail = (
                "模型输出达到 LLM_MAX_OUTPUT_TOKENS 上限，但没有返回最终 content；"
                "请减少生成内容，或检查服务返回的推理/输出通道（finish_reason=length）"
            )
        elif isinstance(reasoning_content, str) and reasoning_content.strip():
            # 只报告通道存在，不保留或输出推理文本，避免泄露模型的内部内容。
            detail = "模型只返回 reasoning_content，没有返回可解析的最终 content"
        else:
            detail = "模型响应内容为空，无法解析为 JSON"
            if finish_reason:
                detail += f"（finish_reason={finish_reason}）"
        error_type = OutputTruncatedError if finish_reason == "length" else JSONParseError
        raise error_type(detail, raw_text=content, usage=_read_usage(completion))
    return choice, content


def _raise_if_length_terminated(
    choice: Any, raw_text: str, usage: TokenUsage
) -> None:
    """把 API 明确标记的长度截断报成可操作的 JSON 响应错误。"""
    if getattr(choice, "finish_reason", None) == "length":
        raise OutputTruncatedError(
            "模型输出达到 LLM_MAX_OUTPUT_TOKENS 上限，响应可能被截断；"
            "生成器会在当前批次大于 1 时拆小后重试（finish_reason=length）",
            raw_text=raw_text,
            usage=usage,
        )


def _is_sdk_length_finish_error(error: Exception) -> bool:
    """识别 OpenAI SDK parse() 对 finish_reason=length 使用的专用异常。"""
    # 用类名判断，避免模块导入时强制依赖 SDK，也便于兼容同接口 SDK 的对应异常。
    return type(error).__name__ == "LengthFinishReasonError"


def _parse_and_validate(
    raw_text: str,
    response_model: type[BaseModel],
    *,
    usage: TokenUsage,
) -> BaseModel:
    """JSON 解码和 Pydantic 校验分开报错，方便定位是语法还是字段契约问题。"""
    try:
        value = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise JSONParseError(
            f"模型响应不是合法 JSON（line {exc.lineno}, column {exc.colno}）",
            raw_text=raw_text,
            usage=usage,
        ) from None
    try:
        return response_model.model_validate(value)
    except ValidationError as exc:
        raise _schema_error(exc, raw_text=raw_text, usage=usage) from None


def _schema_error(
    error: ValidationError,
    *,
    raw_text: str | None = None,
    usage: TokenUsage | None = None,
) -> OutputSchemaError:
    # 只记录字段位置和错误类型，不把错误字段的输入值（可能包含隐私）写进异常。
    safe_details = [
        f"{'.'.join(str(part) for part in item['loc'])}:{item['type']}"
        for item in error.errors(include_input=False)
    ]
    return OutputSchemaError(
        "模型 JSON 不符合输出字段定义"
        + (f"（{', '.join(safe_details[:8])}）" if safe_details else ""),
        raw_text=raw_text,
        usage=usage,
    )


def _read_usage(completion: Any) -> TokenUsage:
    """读取供应商返回的用量；缺失 total_tokens 时由输入/输出用量相加补齐。"""
    usage = getattr(completion, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None) or 0
    completion_tokens = getattr(usage, "completion_tokens", None) or 0
    total_tokens = getattr(usage, "total_tokens", None)
    if total_tokens is None:
        total_tokens = prompt_tokens + completion_tokens
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _is_unsupported_structured_output(error: Exception) -> bool:
    """只识别明确的 HTTP 400 schema 能力错误，其他异常不触发 JSON 降级。"""
    if getattr(error, "status_code", None) != 400:
        return False
    searchable = " ".join((str(getattr(error, "body", "")), str(error))).casefold()
    mentions_schema_feature = any(
        marker in searchable
        for marker in ("response_format", "json_schema", "structured output")
    )
    mentions_unsupported = any(
        marker in searchable
        for marker in ("unsupported", "not supported", "does not support", "not available")
    )
    return mentions_schema_feature and mentions_unsupported


def _generation_error(error: Exception) -> LLMGenerationError:
    """生成安全的错误摘要；不转发 SDK 异常原文，避免其中带入请求或密钥。"""
    error_name = type(error).__name__
    status_code = getattr(error, "status_code", None)
    if "timeout" in error_name.casefold():
        detail = "timeout"
    elif isinstance(status_code, int):
        detail = f"HTTP {status_code}"
    else:
        detail = error_name
    logger.warning("LLM request failed (%s)", detail)
    return LLMGenerationError(f"模型调用失败（{detail}）")
