"""把输入材料组织成请求，调用模型、校验结果并保存追踪产物。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_testgen.config import DEFAULT_MAX_TEST_CASES
from ai_testgen.llm_client import (
    JSONParseError,
    LLMGenerationError,
    LLMResult,
    OutputTruncatedError,
    OutputSchemaError,
)
from ai_testgen.models import (
    GenerationMetadata,
    SourceReference,
    TestCase,
    TestCaseBatch,
    TestSuite,
    TokenUsage,
    ValidationReport,
)
from ai_testgen.validator import (
    BusinessRuleValidationError,
    OpenAPIDocumentError,
    validate_suite,
)


# 修改核心生成指令时更新版本，这样可以区分不同 Prompt 产出的用例。
PROMPT_VERSION = "test-case-generation-v7"
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"}


class GenerationInputError(ValueError):
    """输入为空、版本无效或文档结构不正确时抛出。"""


@dataclass(frozen=True)
class GenerationRequest:
    """一次生成所需的输入；冻结对象避免生成过程中输入被意外改写。"""

    business_requirements: str
    api_document: Mapping[str, Any] | None = None
    business_rules: str | None = None
    constraints: str | None = None
    requirement_version: str | None = None
    focus: str | None = None

    def __post_init__(self) -> None:
        """先挡住空需求、空版本和错误 API 文档，避免发送无效或含糊的模型请求。"""
        if not self.business_requirements.strip():
            raise GenerationInputError("Business requirements must not be empty")
        if self.requirement_version is not None and not self.requirement_version.strip():
            raise GenerationInputError("requirement version must not be empty")
        if self.api_document is not None and not isinstance(self.api_document, Mapping):
            raise GenerationInputError("OpenAPI document must be an object")
        if self.business_rules is not None and not self.business_rules.strip():
            raise GenerationInputError("Business rules must be omitted or contain text")
        if self.constraints is not None and not self.constraints.strip():
            raise GenerationInputError("Constraints must be omitted or contain text")
        if self.focus is not None and not self.focus.strip():
            raise GenerationInputError("focus must be omitted or contain text")


@dataclass(frozen=True)
class GenerationResult:
    """返回调用方常用的数据，同时给出两个产物文件的位置。"""

    metadata: GenerationMetadata
    source_catalog: tuple[SourceReference, ...]
    test_suite: TestSuite
    validation: ValidationReport
    raw_path: Path
    validated_path: Path


class TestCaseGenerator:
    """生成候选用例、保留模型原文，并只把通过校验的结果写入 validated。"""

    def __init__(
        self,
        llm_client: Any,
        *,
        output_dir: str | Path,
        prompt_version: str = PROMPT_VERSION,
        model_name: str | None = None,
        max_test_cases: int = DEFAULT_MAX_TEST_CASES,
        cases_per_request: int | None = None,
    ):
        if not 1 <= max_test_cases <= 50:
            raise ValueError("max_test_cases must be between 1 and 50")
        if cases_per_request is not None and not 1 <= cases_per_request <= 10:
            raise ValueError("cases_per_request must be between 1 and 10")
        self.llm_client = llm_client
        self.output_dir = Path(output_dir)
        self.prompt_version = prompt_version
        self.max_test_cases = max_test_cases
        # 每次最多生成两条；遇到长度截断会继续自动拆批，避免过大的单次响应。
        self.cases_per_request = (
            cases_per_request if cases_per_request is not None else min(max_test_cases, 2)
        )
        configured_model = getattr(getattr(llm_client, "settings", None), "model_name", None)
        self.model_name = model_name or configured_model or "unknown-model"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """依次组装来源、请求模型、保存 raw、执行业务校验并写 validated。"""
        # 给输入材料分配可追踪来源 ID，让模型输出可以指回具体需求段落或 API 操作。
        source_catalog, source_materials, api_contract = _build_sources(request)
        # 输入哈希可在没有人工版本号时标识需求快照；run_id 则区分每一次实际运行。
        input_sha256 = _hash_request(request)
        requirement_version = request.requirement_version or f"sha256:{input_sha256}"
        generated_at = datetime.now(timezone.utc)
        run_id = generated_at.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]

        raw_path = self.output_dir / "raw" / f"{run_id}.json"
        validated_path = self.output_dir / "validated" / f"{run_id}.json"

        batch_outputs: list[dict[str, Any]] = []
        generated_suites: list[TestCaseBatch] = []
        generated_cases: list[TestCase] = []
        clarification_questions: list[str] = []
        try:
            # 总数是硬上限；每次请求只拿一小批，减少单次输出过长的概率。
            while len(generated_cases) < self.max_test_cases:
                requested_count = min(
                    self.cases_per_request,
                    self.max_test_cases - len(generated_cases),
                )
                batch_suites = self._generate_batch(
                    source_materials=source_materials,
                    api_contract=api_contract,
                    generation_focus=request.focus,
                    requested_case_count=requested_count,
                    already_generated=generated_cases,
                    batch_outputs=batch_outputs,
                )
                batch_count = 0
                for batch_suite in batch_suites:
                    generated_suites.append(batch_suite)
                    generated_cases.extend(batch_suite.test_cases)
                    batch_count += len(batch_suite.test_cases)
                    for question in batch_suite.clarification_questions:
                        if question not in clarification_questions:
                            clarification_questions.append(question)
                # 短批次表示模型认为来源支持的独立场景不足，不再为凑数追加请求。
                if batch_count < requested_count:
                    break

            if not generated_cases:
                raise BusinessRuleValidationError(
                    ["model found no test cases supported by the supplied sources"]
                )
            combined_suite = TestSuite(
                suite_title=generated_suites[0].suite_title,
                test_cases=generated_cases,
                clarification_questions=clarification_questions,
            )
        except (
            JSONParseError,
            OutputSchemaError,
            LLMGenerationError,
            BusinessRuleValidationError,
        ) as exc:
            # 截断重试前的消耗和之前成功批次一起记录，方便解释总 Token 与实际 API 次数。
            if any(item["raw_output"] is not None for item in batch_outputs):
                failed_metadata = _build_metadata(
                    run_id=run_id,
                    generated_at=generated_at,
                    model_name=self.model_name,
                    prompt_version=self.prompt_version,
                    requirement_version=requirement_version,
                    input_sha256=input_sha256,
                    usage=_sum_batch_usage(batch_outputs),
                    llm_call_count=len(batch_outputs),
                )
                _write_raw_artifact(
                    raw_path,
                    metadata=failed_metadata,
                    source_catalog=source_catalog,
                    batch_outputs=batch_outputs,
                    response_model=TestCaseBatch.__name__,
                    generation_focus=request.focus,
                )
            raise

        metadata = _build_metadata(
            run_id=run_id,
            generated_at=generated_at,
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            requirement_version=requirement_version,
            input_sha256=input_sha256,
            usage=_sum_batch_usage(batch_outputs),
            llm_call_count=len(batch_outputs),
        )

        _write_raw_artifact(
            raw_path,
            metadata=metadata,
            source_catalog=source_catalog,
            batch_outputs=batch_outputs,
            response_model=TestCaseBatch.__name__,
            generation_focus=request.focus,
        )

        # 每个批次已由 Client 做过结构校验；这里校验合并后的跨批次唯一性和来源引用。
        # 第二阶段检查跨字段/跨用例规则及 OpenAPI 路由；失败时 raw 已保存，validated 不写。
        validation = validate_suite(
            combined_suite,
            source_catalog=source_catalog,
            openapi_document=request.api_document,
            max_test_cases=self.max_test_cases,
        )
        _write_json_atomically(
            validated_path,
            {
                "metadata": metadata.model_dump(mode="json"),
                "source_catalog": [source.model_dump(mode="json") for source in source_catalog],
                "generation_focus": request.focus,
                "test_suite": combined_suite.model_dump(mode="json"),
                "validation": validation.model_dump(mode="json"),
            },
        )
        return GenerationResult(
            metadata=metadata,
            source_catalog=tuple(source_catalog),
            test_suite=combined_suite,
            validation=validation,
            raw_path=raw_path,
            validated_path=validated_path,
        )

    def _generate_batch(
        self,
        *,
        source_materials: list[dict[str, str]],
        api_contract: dict[str, Any] | None,
        generation_focus: str | None,
        requested_case_count: int,
        already_generated: list[TestCase],
        batch_outputs: list[dict[str, Any]],
    ) -> list[TestCaseBatch]:
        """请求一个批次；只在服务明确报告长度截断时递归二分该批次。"""
        messages = _build_messages(
            source_materials=source_materials,
            api_contract=api_contract,
            generation_focus=generation_focus,
            max_cases_in_this_request=requested_case_count,
            total_max_test_cases=self.max_test_cases,
            already_generated_cases=already_generated,
        )
        try:
            llm_result: LLMResult = self.llm_client.generate_structured(
                messages=messages,
                response_model=TestCaseBatch,
            )
        except (JSONParseError, OutputSchemaError, LLMGenerationError) as exc:
            status = "truncated" if isinstance(exc, OutputTruncatedError) else "failed"
            batch_outputs.append(
                _batch_output_record(
                    requested_case_count=requested_case_count,
                    status=status,
                    raw_output=exc.raw_text,
                    usage=exc.usage,
                    response_model=TestCaseBatch.__name__,
                )
            )
            if isinstance(exc, OutputTruncatedError) and requested_case_count > 1:
                # 二分把输出规模减半，重用同一组来源，不重发原始大批次。
                left_count = requested_case_count // 2
                right_count = requested_case_count - left_count
                left_suites = self._generate_batch(
                    source_materials=source_materials,
                    api_contract=api_contract,
                    generation_focus=generation_focus,
                    requested_case_count=left_count,
                    already_generated=already_generated,
                    batch_outputs=batch_outputs,
                )
                left_cases = [case for suite in left_suites for case in suite.test_cases]
                if not left_cases:
                    # 空批次说明来源中没有可用的新场景，跳过未调用的右半批次。
                    return left_suites
                right_suites = self._generate_batch(
                    source_materials=source_materials,
                    api_contract=api_contract,
                    generation_focus=generation_focus,
                    requested_case_count=right_count,
                    already_generated=[*already_generated, *left_cases],
                    batch_outputs=batch_outputs,
                )
                return [*left_suites, *right_suites]
            raise

        if not isinstance(llm_result.parsed, TestCaseBatch):
            batch_outputs.append(
                _batch_output_record(
                    requested_case_count=requested_case_count,
                    status="invalid",
                    raw_output=llm_result.raw_text,
                    usage=llm_result.usage,
                    response_model=llm_result.response_model,
                )
            )
            raise OutputSchemaError(
                "LLM client returned a response outside the TestCaseBatch model",
                raw_text=llm_result.raw_text,
                usage=llm_result.usage,
            )

        batch_outputs.append(
            _batch_output_record(
                requested_case_count=requested_case_count,
                status="completed",
                raw_output=llm_result.raw_text,
                usage=llm_result.usage,
                response_model=llm_result.response_model,
            )
        )
        if len(llm_result.parsed.test_cases) > requested_case_count:
            batch_outputs[-1]["status"] = "invalid"
            raise BusinessRuleValidationError(
                [
                    "suite exceeds the maximum allowed test case count for a generation batch"
                ]
            )
        return [llm_result.parsed]


def _build_metadata(
    *,
    run_id: str,
    generated_at: datetime,
    model_name: str,
    prompt_version: str,
    requirement_version: str,
    input_sha256: str,
    usage: TokenUsage,
    llm_call_count: int,
) -> GenerationMetadata:
    """集中构造元数据，确保 raw 与 validated 文件引用同一份追踪信息。"""
    return GenerationMetadata(
        run_id=run_id,
        generated_at=generated_at,
        model_name=model_name,
        prompt_version=prompt_version,
        requirement_version=requirement_version,
        input_sha256=input_sha256,
        token_usage=usage,
        llm_call_count=llm_call_count,
    )


def _batch_output_record(
    *,
    requested_case_count: int,
    status: str,
    raw_output: str | None,
    usage: TokenUsage,
    response_model: str,
) -> dict[str, Any]:
    """记录一次 LLM 尝试，不保存提示词以免在产物中重复暴露业务输入。"""
    return {
        "requested_case_count": requested_case_count,
        "status": status,
        "raw_output": raw_output,
        "token_usage": usage,
        "response_model": response_model,
    }


def _sum_batch_usage(batch_outputs: list[dict[str, Any]]) -> TokenUsage:
    """统计所有成功和截断请求的用量；失败的截断请求同样可能产生费用。"""
    return TokenUsage(
        prompt_tokens=sum(item["token_usage"].prompt_tokens for item in batch_outputs),
        completion_tokens=sum(
            item["token_usage"].completion_tokens for item in batch_outputs
        ),
        total_tokens=sum(item["token_usage"].total_tokens for item in batch_outputs),
    )


def _write_raw_artifact(
    path: Path,
    *,
    metadata: GenerationMetadata,
    source_catalog: list[SourceReference],
    batch_outputs: list[dict[str, Any]],
    response_model: str,
    generation_focus: str | None,
) -> None:
    """保存每次模型原文和来源目录；raw 不是通过业务校验的证明。"""
    serialized_batches = [
        {
            **item,
            "token_usage": item["token_usage"].model_dump(mode="json"),
        }
        for item in batch_outputs
    ]
    if len(serialized_batches) == 1:
        raw_output: str | None = serialized_batches[0]["raw_output"]
        raw_output_format = "single_response"
    else:
        # 兼容旧的字符串字段；多请求时放入 JSON 字符串 bundle，并另提供结构化 batch_outputs。
        raw_output = json.dumps(
            [item["raw_output"] for item in serialized_batches], ensure_ascii=False
        )
        raw_output_format = "batch_bundle_v1"
    _write_json_atomically(
        path,
        {
            "metadata": metadata.model_dump(mode="json"),
            "source_catalog": [source.model_dump(mode="json") for source in source_catalog],
            "generation_focus": generation_focus,
            "raw_output": raw_output,
            "raw_output_format": raw_output_format,
            "batch_outputs": serialized_batches,
            "response_model": response_model,
        },
    )


def _build_sources(
    request: GenerationRequest,
) -> tuple[list[SourceReference], list[dict[str, str]], dict[str, Any] | None]:
    """建立来源目录，并只把接口契约需要的 OpenAPI 部分整理给 Prompt。"""
    source_catalog: list[SourceReference] = []
    source_materials: list[dict[str, str]] = []

    for prefix, kind, label, text in (
        ("REQ", "requirement", "requirements", request.business_requirements),
        ("RULE", "business_rule", "business rules", request.business_rules),
        ("CONSTRAINT", "constraint", "constraints", request.constraints),
    ):
        if text is None:
            continue
        # 按空行切段并按顺序编号；模型引用 REQ-001 时可以追溯回对应段落。
        blocks = _text_blocks(text)
        for index, block in enumerate(blocks, start=1):
            source_id = f"{prefix}-{index:03d}"
            location = f"{label} paragraph {index}"
            source_catalog.append(
                SourceReference(
                    source_id=source_id,
                    kind=kind,
                    description=location,
                    location=location,
                )
            )
            source_materials.append(
                {
                    "source_id": source_id,
                    "kind": kind,
                    "location": location,
                    "text": block,
                }
            )

    api_operations: list[dict[str, Any]] = []
    api_contract: dict[str, Any] | None = None
    if request.api_document is not None:
        paths = request.api_document.get("paths")
        if not isinstance(paths, Mapping):
            raise OpenAPIDocumentError("OpenAPI document must contain a paths object")
        ordered_operations: list[tuple[str, str, Mapping[str, Any]]] = []
        for path, path_item in paths.items():
            if not isinstance(path, str) or not path.startswith("/"):
                raise OpenAPIDocumentError("OpenAPI paths must be absolute route strings")
            if not isinstance(path_item, Mapping):
                raise OpenAPIDocumentError("Each OpenAPI path item must be an object")
            for method, operation in path_item.items():
                method_name = str(method).upper()
                if method_name not in _HTTP_METHODS:
                    continue
                if not isinstance(operation, Mapping):
                    raise OpenAPIDocumentError("Each OpenAPI operation must be an object")
                ordered_operations.append((path, method_name, operation))

        # 排序后再编号，避免 OpenAPI JSON 属性顺序变化导致 API-001 指向不同接口。
        for index, (path, method, operation) in enumerate(
            sorted(ordered_operations, key=lambda item: (item[0], item[1])), start=1
        ):
            path_item = paths[path]
            source_id = f"API-{index:03d}"
            operation_id = operation.get("operationId")
            if operation_id is not None and not isinstance(operation_id, str):
                raise OpenAPIDocumentError("OpenAPI operationId values must be strings")
            location = f"paths.{path}.{method.lower()}"
            source_catalog.append(
                SourceReference(
                    source_id=source_id,
                    kind="api_operation",
                    description=f"{method} {path}",
                    location=location,
                )
            )
            compact_operation: dict[str, Any] = {
                "source_id": source_id,
                "method": method,
                "path": path,
            }
            if operation_id is not None:
                compact_operation["operation_id"] = operation_id
            for field in ("summary", "description"):
                value = _safe_summary(operation.get(field))
                if value:
                    compact_operation[field] = value
            # 路径级参数同样约束每个操作，和操作级参数合并后只传一次。
            parameters = []
            for parameter_source in (path_item.get("parameters"), operation.get("parameters")):
                if isinstance(parameter_source, list):
                    parameters.extend(parameter_source)
            if parameters:
                compact_operation["parameters"] = [
                    _compact_parameter(parameter) for parameter in parameters
                ]
            if "requestBody" in operation:
                compact_operation["requestBody"] = _compact_request_body(
                    operation["requestBody"]
                )
            if "responses" in operation:
                compact_operation["responses"] = _compact_responses(
                    operation["responses"]
                )
            if "security" in operation:
                compact_operation["security"] = _copy_openapi_value(
                    operation["security"]
                )
            api_operations.append(compact_operation)

        api_contract = _build_compact_api_contract(request.api_document, api_operations)
    return source_catalog, source_materials, api_contract


def _build_compact_api_contract(
    api_document: Mapping[str, Any], operations: list[dict[str, Any]]
) -> dict[str, Any]:
    """仅保留路由契约和被引用的组件，去掉示例、服务器配置等生成用例不需要的内容。"""
    contract: dict[str, Any] = {"operations": operations}
    info = api_document.get("info")
    if isinstance(info, Mapping):
        compact_info = {
            key: _safe_summary(info.get(key))
            for key in ("title", "version", "description")
            if _safe_summary(info.get(key))
        }
        if compact_info:
            contract["info"] = compact_info
    openapi_version = api_document.get("openapi")
    if isinstance(openapi_version, str):
        contract["openapi"] = openapi_version
    if "security" in api_document:
        # OpenAPI 根级认证要求覆盖所有未显式设置 operation.security 的接口。
        contract["security"] = _copy_openapi_value(api_document["security"])

    components = api_document.get("components")
    if not isinstance(components, Mapping):
        return contract

    component_values = {
        group: value
        for group, value in components.items()
        if isinstance(group, str) and isinstance(value, Mapping)
    }
    selected_components: dict[str, dict[str, Any]] = {}
    pending_refs = _find_component_refs(operations)
    security_requirements = [api_document.get("security", [])]
    security_requirements.extend(operation.get("security", []) for operation in operations)
    security_names = {
        name
        for requirements in security_requirements
        if isinstance(requirements, list)
        for requirement in requirements
        if isinstance(requirement, Mapping)
        for name in requirement
        if isinstance(name, str)
    }
    pending_refs.update(("securitySchemes", name) for name in security_names)

    # 递归解析本地 components 引用；未找到的外部或悬空 $ref 仍留在操作中供模型识别。
    while pending_refs:
        group, name = pending_refs.pop()
        source_group = component_values.get(group)
        if not isinstance(source_group, Mapping) or name not in source_group:
            continue
        selected_group = selected_components.setdefault(group, {})
        if name in selected_group:
            continue
        value = source_group[name]
        compact_value = _compact_component(group, value)
        selected_group[name] = compact_value
        pending_refs.update(_find_component_refs(compact_value))

    if selected_components:
        contract["components"] = selected_components
    return contract


def _find_component_refs(value: Any) -> set[tuple[str, str]]:
    """找出嵌套 Schema/参数/响应中的本地 OpenAPI component 引用。"""
    references: set[tuple[str, str]] = set()
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/"):
            pointer_parts = reference.removeprefix("#/components/").split("/")
            if len(pointer_parts) >= 2:
                group = _decode_json_pointer(pointer_parts[0])
                name = _decode_json_pointer("/".join(pointer_parts[1:]))
                references.add((group, name))
        for nested in value.values():
            references.update(_find_component_refs(nested))
    elif isinstance(value, list):
        for nested in value:
            references.update(_find_component_refs(nested))
    return references


def _decode_json_pointer(value: str) -> str:
    """按 RFC 6901 还原 OpenAPI component 名称中的转义字符。"""
    return value.replace("~1", "/").replace("~0", "~")


def _compact_component(group: str, value: Any) -> Any:
    """按 OpenAPI component 类型精简，避免把 Schema 的业务属性名当元数据删掉。"""
    if group == "schemas":
        return _compact_schema(value)
    if group == "parameters":
        return _compact_parameter(value)
    if group == "requestBodies":
        return _compact_request_body(value)
    if group == "responses":
        return _compact_response(value)
    if group == "headers":
        return _compact_parameter(value)
    # 例如 securitySchemes 的 name、flows、scopes 都是实际契约，完整保留。
    return _copy_openapi_value(value)


def _compact_parameter(value: Any) -> Any:
    """保留参数契约字段；只去掉 Parameter Object 自身的示例。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if key in {"example", "examples", "externalDocs"}:
            continue
        if key == "schema":
            result[key] = _compact_schema(nested)
        elif key == "content":
            result[key] = _compact_content(nested)
        else:
            result[key] = _copy_openapi_value(nested)
    return result


def _compact_request_body(value: Any) -> Any:
    """移除 Request Body 的媒体示例，同时保留实际 JSON Schema。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if key == "externalDocs":
            continue
        result[key] = _compact_content(nested) if key == "content" else _copy_openapi_value(nested)
    return result


def _compact_responses(value: Any) -> Any:
    """按状态码逐个精简 Responses Object。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    return {
        # 4xx 示例通常承载具体业务错误 detail；成功响应示例则通常与 Schema 重复且较长。
        status: _compact_response(response, preserve_examples=str(status).startswith("4"))
        for status, response in value.items()
    }


def _compact_response(value: Any, *, preserve_examples: bool = False) -> Any:
    """压缩 Response Object；错误示例保留其 value，以便模型使用具体业务 detail。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if key in {"externalDocs", "links"}:
            continue
        if key == "content":
            result[key] = _compact_content(nested, preserve_examples=preserve_examples)
        elif key == "headers" and isinstance(nested, Mapping):
            result[key] = {
                name: _compact_parameter(header)
                for name, header in nested.items()
            }
        else:
            result[key] = _copy_openapi_value(nested)
    return result


def _compact_content(value: Any, *, preserve_examples: bool = False) -> Any:
    """精简 Media Type Object；示例位于这里，而非其 schema.properties 中。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    result: dict[str, Any] = {}
    for media_type, media_object in value.items():
        if not isinstance(media_object, Mapping):
            result[media_type] = _copy_openapi_value(media_object)
            continue
        compact_media: dict[str, Any] = {}
        for key, nested in media_object.items():
            if key == "externalDocs":
                continue
            if key == "example":
                if preserve_examples:
                    compact_media[key] = _copy_openapi_value(nested)
                continue
            if key == "examples":
                if preserve_examples:
                    compact_media[key] = _compact_example_values(nested)
                continue
            compact_media[key] = (
                _compact_schema(nested)
                if key == "schema"
                else _copy_openapi_value(nested)
            )
        result[media_type] = compact_media
    return result


def _compact_example_values(value: Any) -> dict[str, Any]:
    """保留内嵌 Example Object 的值；不保留说明文本或外部 URL。"""
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for name, example in value.items():
        if not isinstance(name, str) or not isinstance(example, Mapping) or "value" not in example:
            continue
        result[name] = {"value": _copy_openapi_value(example["value"])}
    return result


_SCHEMA_MAP_KEYS = {
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
}
_SCHEMA_SINGLE_KEYS = {
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "items",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
}
_SCHEMA_LIST_KEYS = {"allOf", "anyOf", "oneOf", "prefixItems"}


def _compact_schema(value: Any) -> Any:
    """精简 Schema 注解，但按 properties 的名字原样保留业务字段。"""
    if not isinstance(value, Mapping):
        return _copy_openapi_value(value)
    result: dict[str, Any] = {}
    for key, nested in value.items():
        # 示例不是生成测试步骤所必需的信息；Schema 属性表中的同名字段仍会保留。
        if key in {"example", "examples", "externalDocs"}:
            continue
        if key in _SCHEMA_MAP_KEYS and isinstance(nested, Mapping):
            result[key] = {
                name: _compact_schema(schema) for name, schema in nested.items()
            }
        elif key in _SCHEMA_SINGLE_KEYS:
            result[key] = _compact_schema(nested)
        elif key in _SCHEMA_LIST_KEYS and isinstance(nested, list):
            result[key] = [_compact_schema(schema) for schema in nested]
        else:
            # enum/default 等可能包含任意 JSON 值，不能把其中同名键当成 OpenAPI 元数据。
            result[key] = _copy_openapi_value(nested)
    return result


def _copy_openapi_value(value: Any) -> Any:
    """递归复制未被指定精简规则覆盖的值，不修改调用方原始 OpenAPI 文档。"""
    if isinstance(value, Mapping):
        return {key: _copy_openapi_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_copy_openapi_value(nested) for nested in value]
    return value


def _text_blocks(text: str) -> list[str]:
    """把多段文本分成引用粒度较小的块，并过滤空段落。"""
    blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return blocks or [text.strip()]


def _safe_summary(value: Any) -> str | None:
    """只把非空字符串作为 API 摘要，避免把任意 OpenAPI 值塞进 Prompt。"""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _hash_request(request: GenerationRequest) -> str:
    """对规范化 JSON 求 SHA-256；排序键让同一份输入不受字典顺序影响。"""
    canonical_input = {
        "business_requirements": request.business_requirements,
        "business_rules": request.business_rules,
        "constraints": request.constraints,
        "focus": request.focus,
        "api_document": request.api_document,
    }
    try:
        encoded = json.dumps(
            canonical_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise GenerationInputError("Input documents must be JSON-serializable") from None
    return hashlib.sha256(encoded).hexdigest()


def _build_messages(
    *,
    source_materials: list[dict[str, str]],
    api_contract: dict[str, Any] | None,
    generation_focus: str | None,
    max_cases_in_this_request: int,
    total_max_test_cases: int,
    already_generated_cases: list[TestCase] | None = None,
) -> list[dict[str, str]]:
    """构造分工明确的 system/user 消息，指示模型引用来源、标记未知规则并输出 JSON。"""
    # system_message 放稳定的行为约束；user_payload 放本次需求数据，便于版本化和测试。
    system_message = """You design API test cases from explicit requirements and API documentation.
Return one JSON object matching output_requirements.response_schema exactly. Do not invent
business rules, status codes, response fields, or state transitions. Treat documented 4xx response
examples as evidence for their named error scenarios. If an expected behavior stated by the case
cannot be derived from the supplied sources, put it in clarification_questions and mark that
specific expected behavior in unverifiable_expectations. Do not mark optional response details
that the case does not assert as unverifiable; ask about them at suite level only if useful.
Include every required field, use the declared
JSON types and enum values, and use [] for empty arrays. Every test case must cite one or more
exact source_id values from the supplied source catalog. Every step needs an action and an
expected_result.
The optional generation_focus is only a hint for prioritizing source-supported test scenarios.
It must not override or change business rules or the API contract. If it conflicts with those
sources, follow the sources and add a clarification question instead of inventing a rule.
Use only API method/path pairs present in the supplied compact OpenAPI contract. For non-API
steps, set api_path, http_method, and operation_id to null. Keep case_id to letters, numbers,
underscores, or hyphens. Generate no more than {total_max_test_cases} test cases across the
complete suite, and no more than {max_cases_in_this_request} in this request. If the sources
support fewer distinct cases, return only those. Do not repeat the previously generated case IDs
or titles. Select distinct, high-priority scenarios. Keep every field concise, avoid repeating
the same facts, and normally use no more than 3 steps per case unless the scenario genuinely
requires more. Do not omit required keys. Output no markdown fences or commentary.""".format(
        max_cases_in_this_request=max_cases_in_this_request,
        total_max_test_cases=total_max_test_cases,
    )
    user_payload = {
        "source_materials": source_materials,
        "api_contract": api_contract,
        "generation_focus": generation_focus,
        "already_generated_cases": [
            {"case_id": case.case_id, "title": case.title}
            for case in (already_generated_cases or [])
        ],
        "output_requirements": {
            "max_test_cases": total_max_test_cases,
            "max_cases_in_this_request": max_cases_in_this_request,
            "recommended_max_steps_per_case": 3,
            "style": "concise; avoid repeating facts",
            # SiliconFlow 当前模型支持 JSON Mode，不支持原生 Structured Outputs；把 Pydantic
            # 生成的 JSON Schema 放进 Prompt，让模型看见每个必填字段、类型和 Literal 枚举。
            "response_schema": TestCaseBatch.model_json_schema(),
        },
    }
    return [
        {"role": "system", "content": system_message},
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
        },
    ]


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """用受限权限和临时文件原子替换，避免敏感产物权限过宽或写到一半损坏。"""
    # 新建目录设为仅当前用户可访问；文件也用受限权限，因为模型回复可能复述输入材料。
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # 临时文件和目标文件放在同一目录，确保 os.replace 在同一文件系统内原子完成。
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(temporary_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # 替换是原子操作：读者只会看到旧文件或完整的新文件，不会读到半截 JSON。
        os.replace(temporary_path, path)
    except Exception:
        # 写失败时清理临时文件，但保留并重新抛出原始异常供 CLI 报告。
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise
