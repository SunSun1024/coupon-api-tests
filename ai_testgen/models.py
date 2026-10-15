"""定义模型输出契约、来源信息及生成/校验元数据。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


# 用注解复用非空文本约束，避免标题、步骤等字段各自写出不一致的校验。
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# 限制 case_id 字符集，便于放进 pytest ID、文件名和报告链接中。
CaseId = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$", min_length=1),
]
# Literal 让模型生成越界优先级或场景名称时直接校验失败，而不是接受任意字符串。
Priority = Literal["P0", "P1", "P2", "P3"]
ScenarioType = Literal[
    "positive",
    "negative",
    "boundary",
    "security",
    "exception",
    "other",
]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE"]
SourceKind = Literal["requirement", "business_rule", "constraint", "api_operation"]


class StrictModel(BaseModel):
    """模型边界统一拒绝额外字段和隐式类型转换。"""

    # LLM 输出字段名拼错或把数字写成字符串时直接报错，不静默“修好”错误数据。
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class TestStep(StrictModel):
    """一个测试步骤；API 信息允许为空，因为步骤也可能是准备/检查动作。"""

    step_number: int = Field(ge=1)
    action: NonEmptyString
    expected_result: NonEmptyString
    api_path: NonEmptyString | None
    http_method: HttpMethod | None
    operation_id: NonEmptyString | None


class TestCase(StrictModel):
    """一条可追踪的测试用例。

    priority/scenario_type 用于分类；preconditions、steps 和 expected_results
    描述如何验证；source_refs 说明依据；unverifiable_expectations 显式保留未知点。
    """

    case_id: CaseId
    title: NonEmptyString
    objective: NonEmptyString
    priority: Priority
    scenario_type: ScenarioType
    preconditions: list[NonEmptyString]
    steps: list[TestStep] = Field(min_length=1)
    expected_results: list[NonEmptyString] = Field(min_length=1)
    source_refs: list[NonEmptyString] = Field(min_length=1)
    unverifiable_expectations: list[NonEmptyString]


class TestSuite(StrictModel):
    """一次生成的用例集合；澄清问题放在集合级，避免被误当作已确认规则。"""

    suite_title: NonEmptyString
    test_cases: list[TestCase] = Field(min_length=1)
    clarification_questions: list[NonEmptyString]


class TestCaseBatch(StrictModel):
    """模型的一次小批次响应；空列表表示没有更多可区分的场景。"""

    suite_title: NonEmptyString
    test_cases: list[TestCase]
    clarification_questions: list[NonEmptyString]


class SourceReference(StrictModel):
    """需求、规则、约束或 API 操作的目录项，供用例引用和事后追踪。"""

    source_id: NonEmptyString
    kind: SourceKind
    description: NonEmptyString
    location: NonEmptyString | None


class TokenUsage(StrictModel):
    """保留模型返回的 token 统计；本地 Mock 或不返回 usage 的服务默认记为 0。"""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class GenerationMetadata(StrictModel):
    """记录追踪信息：运行 ID、UTC 时间、模型/Prompt/需求版本、输入摘要和 token 数。"""

    run_id: NonEmptyString
    generated_at: datetime
    model_name: NonEmptyString
    prompt_version: NonEmptyString
    requirement_version: NonEmptyString
    input_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    token_usage: TokenUsage
    llm_call_count: int = Field(default=1, ge=1)

    @field_validator("generated_at")
    @classmethod
    def generated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        # 强制带时区，避免把不同时区的生成时间误认为同一个时间点。
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value


class ValidationReport(StrictModel):
    """汇总结构之外的校验结果；warning 表示信息不足，未必表示用例错误。"""

    status: Literal["passed", "passed_with_warnings"]
    case_count: int = Field(ge=0)
    warnings: list[NonEmptyString]
    unverifiable_expectations: list[NonEmptyString]
