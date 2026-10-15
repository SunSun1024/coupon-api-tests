"""从环境变量读取并校验模型服务配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit


# 供应商对结构化输出的支持程度不同，因此允许调用方显式选择输出方式。
OutputMode = Literal["auto", "native", "json_object", "prompt"]
# auto 尝试原生 schema 并在明确不支持时降级；native 不降级；json_object/prompt 最终都做本地 JSON 校验。
_VALID_OUTPUT_MODES = {"auto", "native", "json_object", "prompt"}
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_TEST_CASES = 6
DEFAULT_MAX_CASES_PER_REQUEST = 2


class ConfigurationError(ValueError):
    """配置缺失或无效时抛出；错误信息不包含密钥值。"""


@dataclass(frozen=True)
class LLMSettings:
    """模型客户端所需的不可变配置快照。"""

    # repr=False 防止调试打印配置对象时意外暴露密钥；frozen 防止运行中被改写。
    api_key: str = field(repr=False)
    model_name: str
    base_url: str | None = None
    timeout_seconds: float = 30.0
    max_retries: int = 2
    output_mode: OutputMode = "auto"
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    max_test_cases: int = DEFAULT_MAX_TEST_CASES
    max_cases_per_request: int = DEFAULT_MAX_CASES_PER_REQUEST

    def __post_init__(self) -> None:
        """配置无论来自环境变量还是代码构造，都执行同一套边界检查。"""
        if not self.api_key.strip():
            raise ConfigurationError("LLM_API_KEY must not be empty")
        if not self.model_name.strip():
            raise ConfigurationError("LLM_MODEL must not be empty")
        if not 0.1 <= self.timeout_seconds <= 600:
            raise ConfigurationError("LLM_TIMEOUT_SECONDS must be between 0.1 and 600")
        if not 0 <= self.max_retries <= 5:
            raise ConfigurationError("LLM_MAX_RETRIES must be between 0 and 5")
        if not 256 <= self.max_output_tokens <= 65536:
            raise ConfigurationError(
                "LLM_MAX_OUTPUT_TOKENS must be between 256 and 65536"
            )
        if not 1 <= self.max_test_cases <= 50:
            raise ConfigurationError("LLM_MAX_TEST_CASES must be between 1 and 50")
        if not 1 <= self.max_cases_per_request <= 10:
            raise ConfigurationError("LLM_MAX_CASES_PER_REQUEST must be between 1 and 10")
        if self.output_mode not in _VALID_OUTPUT_MODES:
            raise ConfigurationError(
                "LLM_OUTPUT_MODE must be auto, native, json_object, or prompt"
            )
        if self.base_url is not None:
            parts = urlsplit(self.base_url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                raise ConfigurationError("LLM_BASE_URL must be an HTTP or HTTPS URL")

    @classmethod
    def from_env(cls) -> LLMSettings:
        """读取统一的 LLM_* 环境变量，避免把密钥写进代码或提交到仓库。"""
        api_key = os.getenv("LLM_API_KEY", "")
        model_name = os.getenv("LLM_MODEL", "")
        missing = []
        if not api_key.strip():
            missing.append("LLM_API_KEY")
        if not model_name.strip():
            missing.append("LLM_MODEL")
        if missing:
            # 只报告缺少哪个变量，不把实际密钥或需求正文放进错误消息。
            raise ConfigurationError(
                "Set the required environment variable(s): " + ", ".join(missing)
            )

        timeout_raw = os.getenv("LLM_TIMEOUT_SECONDS", "30")
        retries_raw = os.getenv("LLM_MAX_RETRIES", "2")
        max_output_tokens_raw = os.getenv(
            "LLM_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS)
        )
        max_test_cases_raw = os.getenv("LLM_MAX_TEST_CASES", str(DEFAULT_MAX_TEST_CASES))
        max_cases_per_request_raw = os.getenv(
            "LLM_MAX_CASES_PER_REQUEST", str(DEFAULT_MAX_CASES_PER_REQUEST)
        )
        try:
            timeout_seconds = float(timeout_raw)
            max_retries = int(retries_raw)
            max_output_tokens = int(max_output_tokens_raw)
            max_test_cases = int(max_test_cases_raw)
            max_cases_per_request = int(max_cases_per_request_raw)
        except ValueError:
            raise ConfigurationError(
                "LLM_TIMEOUT_SECONDS, LLM_MAX_RETRIES, LLM_MAX_OUTPUT_TOKENS, "
                "LLM_MAX_TEST_CASES, and LLM_MAX_CASES_PER_REQUEST must be numeric"
            ) from None

        output_mode_raw = os.getenv("LLM_OUTPUT_MODE", "auto").strip().lower()
        if output_mode_raw not in _VALID_OUTPUT_MODES:
            raise ConfigurationError(
                "LLM_OUTPUT_MODE must be auto, native, json_object, or prompt"
            )

        # 官方服务可使用默认地址；兼容服务可用 base_url 指向自己的 API endpoint。
        base_url = os.getenv("LLM_BASE_URL", "").strip() or None
        return cls(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            output_mode=output_mode_raw,  # type: ignore[arg-type]
            max_output_tokens=max_output_tokens,
            max_test_cases=max_test_cases,
            max_cases_per_request=max_cases_per_request,
        )
