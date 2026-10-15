"""执行数据与自然语言 TestCase 分开：操作和值均采用明确的判别联合。"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, field_validator, model_validator

from ai_testgen.models import (
    CaseId, GenerationMetadata, NonEmptyString, SourceReference, StrictModel, TestSuite, ValidationReport,
)


class Model(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)


class LiteralValue(Model):
    kind: Literal["literal"]
    value: JsonValue


class VariableValue(Model):
    kind: Literal["ref"]
    name: CaseId


Value = Annotated[LiteralValue | VariableValue, Field(discriminator="kind")]


class Fixture(Model):
    name: CaseId
    factory: Literal["user_id", "order_id", "coupon_name", "expires_at"]


class HttpRequest(Model):
    op: Literal["http_request"]
    step_id: CaseId
    operation_id: CaseId
    method: Literal["GET", "POST"]
    path: NonEmptyString
    path_params: dict[str, Value] = Field(default_factory=dict)
    query: dict[str, Value] = Field(default_factory=dict)
    json_body: dict[str, Value] | None
    captures: dict[str, str] = Field(default_factory=dict)


class AssertStatus(Model):
    op: Literal["assert_status"]
    step_id: CaseId
    request_id: CaseId
    expected: int = Field(ge=100, le=599)


class AssertJson(Model):
    op: Literal["assert_json"]
    step_id: CaseId
    request_id: CaseId
    pointer: str
    expected: Value


Operation = Annotated[HttpRequest | AssertStatus | AssertJson, Field(discriminator="op")]


class CaseMapping(Model):
    source_case_id: CaseId
    environment: Literal["local-coupon"]
    fixtures: list[Fixture]
    operations: list[Operation] = Field(min_length=1, max_length=100)


class MappingBundle(Model):
    version: Literal[1]
    source_run_id: NonEmptyString
    cases: list[CaseMapping]


class SourceArtifact(Model):
    metadata: GenerationMetadata
    source_catalog: list[SourceReference]
    generation_focus: str | None = None
    test_suite: TestSuite
    validation: ValidationReport


class ReviewReceipt(Model):
    version: Literal[1] = 1
    review_id: NonEmptyString
    reviewer: NonEmptyString
    reviewed_at: datetime
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledged_warnings: bool = False

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include timezone")
        return value


class AutomatedPermit(Model):
    """机器校验记录，不含审核人，也不能替代人工 ReviewReceipt。"""

    version: Literal[1] = 1
    kind: Literal["automated"] = "automated"
    permit_id: NonEmptyString
    validator_version: NonEmptyString
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mapping_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ExecutableCase(Model):
    execution_case_id: NonEmptyString
    generation_run_id: NonEmptyString
    source_case_id: CaseId
    source_refs: list[NonEmptyString]
    fingerprint: str
    mapping: CaseMapping


class ExecutableBundle(Model):
    version: Literal[1] = 1
    source: SourceArtifact
    mapping: MappingBundle
    review_id: NonEmptyString | None = None
    automated_permit_id: NonEmptyString | None = None
    source_digest: str
    mapping_digest: str
    contract_digest: str
    cases: list[ExecutableCase] = Field(min_length=1)

    @model_validator(mode="after")
    def exactly_one_authorization(self):
        if (self.review_id is None) == (self.automated_permit_id is None):
            raise ValueError("exactly one of review_id or automated_permit_id is required")
        return self


class ConversionOutcome(Model):
    source_case_id: str | None
    status: Literal["ready", "needs_manual_mapping", "conversion_failed", "plan_invalid"]
    code: NonEmptyString
    reason: NonEmptyString


class ConversionResult(Model):
    bundle: ExecutableBundle | None
    outcomes: list[ConversionOutcome]


class StepResult(Model):
    step_id: str
    op: str
    status: Literal["passed", "failed"]
    request_id: str | None = None
    code: str | None = None
    reason: str | None = None
    expected: JsonValue = None
    actual: JsonValue = None


class ExecutionResult(Model):
    execution_run_id: str
    generation_run_id: str | None
    source_case_id: str | None
    execution_case_id: str | None
    source_digest: str | None
    mapping_digest: str | None
    contract_digest: str | None
    review_id: str | None
    automated_permit_id: str | None = None
    input_digest: str | None = None
    input_path: str | None = None
    trace_verified: bool = False
    started_at: datetime
    ended_at: datetime
    status: Literal["passed", "assertion_failed", "execution_error", "review_required", "review_invalid", "conversion_failed", "plan_invalid"]
    code: str | None
    reason: str | None
    steps: list[StepResult]
