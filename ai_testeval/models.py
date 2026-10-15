"""Strict, versioned data contracts for the evaluation package."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

from ai_testgen.models import TokenUsage


Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class EvalModel(BaseModel):
    """Reject unknown fields and implicit type coercion at artifact boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, frozen=True)


class BusinessRule(EvalModel):
    rule_id: Text
    text: Text
    source_refs: list[Text] = Field(min_length=1)


class ApiConstraint(EvalModel):
    constraint_id: Text
    text: Text


class GoldenRisk(EvalModel):
    risk_id: Text
    description: Text
    severity: Literal["critical", "high", "medium", "low"]
    source_refs: list[Text] = Field(min_length=1)
    rationale: Text


class ExpectedScenario(EvalModel):
    scenario_id: Text
    scenario_type: Literal["positive", "negative", "boundary", "security", "exception"]
    risk_ids: list[Text] = Field(min_length=1)
    preconditions: list[Text]
    actions: list[Text] = Field(min_length=1)
    assertions: list[Text] = Field(min_length=1)


class GoldenSample(EvalModel):
    sample_id: Text
    requirement_id: Text
    requirement_version: Text
    requirements: Text
    business_rules: list[BusinessRule]
    api_constraints: list[ApiConstraint]
    openapi_document: dict[str, JsonValue] | None = None
    generation_focus: Text | None = None
    risks: list[GoldenRisk] = Field(min_length=1)
    expected_scenarios: list[ExpectedScenario] = Field(min_length=1)
    rubric_version: Text


class GoldenDataset(EvalModel):
    """Golden inputs and human-authored risk criteria; never sent as one prompt."""

    schema_version: Literal[1]
    dataset_id: Text
    dataset_version: Text
    description: Text
    samples: list[GoldenSample] = Field(min_length=1)

    @model_validator(mode="after")
    def ids_and_references_are_valid(self) -> "GoldenDataset":
        """Check uniqueness and cross-references across the complete dataset."""
        problems: list[str] = []
        ids_by_kind: dict[str, list[str]] = {
            "sample_id": [sample.sample_id for sample in self.samples],
            "requirement_id": [sample.requirement_id for sample in self.samples],
            "rule_id": [rule.rule_id for sample in self.samples for rule in sample.business_rules],
            "constraint_id": [constraint.constraint_id for sample in self.samples for constraint in sample.api_constraints],
            "risk_id": [risk.risk_id for sample in self.samples for risk in sample.risks],
            "scenario_id": [scenario.scenario_id for sample in self.samples for scenario in sample.expected_scenarios],
        }
        for kind, values in ids_by_kind.items():
            seen: set[str] = set()
            for value in values:
                if value in seen:
                    problems.append(f"duplicate {kind} '{value}'")
                seen.add(value)

        for sample in self.samples:
            source_ids = {sample.requirement_id}
            source_ids.update(rule.rule_id for rule in sample.business_rules)
            source_ids.update(constraint.constraint_id for constraint in sample.api_constraints)
            source_ids.update(_openapi_operation_ids(sample.openapi_document))

            for rule in sample.business_rules:
                for source_ref in rule.source_refs:
                    if source_ref not in source_ids:
                        problems.append(
                            f"sample '{sample.sample_id}' rule '{rule.rule_id}' has unknown source_ref '{source_ref}'"
                        )

            risk_ids = {risk.risk_id for risk in sample.risks}
            for risk in sample.risks:
                for source_ref in risk.source_refs:
                    if source_ref not in source_ids:
                        problems.append(
                            f"sample '{sample.sample_id}' risk '{risk.risk_id}' has unknown source_ref '{source_ref}'"
                        )
            for scenario in sample.expected_scenarios:
                for risk_id in scenario.risk_ids:
                    if risk_id not in risk_ids:
                        problems.append(
                            f"sample '{sample.sample_id}' scenario '{scenario.scenario_id}' references unknown risk_id '{risk_id}'"
                        )

        if problems:
            raise ValueError("; ".join(problems))
        return self


def _openapi_operation_ids(document: dict[str, JsonValue] | None) -> set[str]:
    """Extract operationId values so risk sources can point to API operations."""
    if not isinstance(document, dict):
        return set()
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return set()
    operation_ids: set[str] = set()
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            if isinstance(operation, dict) and isinstance(operation.get("operationId"), str):
                operation_ids.add(operation["operationId"])
    return operation_ids


class DatasetApproval(EvalModel):
    """A separate human decision cryptographically bound to one dataset revision."""

    schema_version: Literal[1] = 1
    dataset_id: Text
    dataset_version: Text
    dataset_sha256: Sha256
    reviewer: Text
    reviewed_at: datetime
    decision: Literal["approved"]

    @field_validator("reviewed_at")
    @classmethod
    def approval_timestamp_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value


class AttemptRecord(EvalModel):
    """One logical `generate_structured` request and its exact local evidence."""

    attempt_id: Text
    started_at: datetime
    ended_at: datetime
    requested_model: Text | None
    response_schema_name: Text
    response_schema_sha256: Sha256
    response_schema: dict[str, JsonValue]
    messages: list[dict[str, JsonValue]]
    call_settings: dict[str, JsonValue]
    outcome: Literal[
        "schema_valid", "json_invalid", "schema_invalid", "truncated", "generation_failed"
    ]
    raw_text: str | None
    raw_available: bool
    schema_valid: bool
    error_type: Text | None
    finish_reason: Text | None
    token_usage: TokenUsage

    @field_validator("started_at", "ended_at")
    @classmethod
    def timestamps_have_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attempt timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def flags_match_outcome(self) -> "AttemptRecord":
        if self.raw_available != (self.raw_text is not None):
            raise ValueError("raw_available must match whether raw_text was captured")
        if self.schema_valid != (self.outcome == "schema_valid"):
            raise ValueError("schema_valid must match the recorded outcome")
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must not be earlier than started_at")
        return self


class ExperimentConfig(EvalModel):
    """Only parameters that define the run; credentials and secret URLs are excluded."""

    experiment_id: Text
    provider: Text
    requested_model: Text
    declared_model_version: Text | None = None
    dataset_sha256: Sha256
    sample_ids: list[Text] = Field(min_length=1)
    repetitions: int = Field(ge=1, le=100)
    max_test_cases: int = Field(ge=1, le=50)
    cases_per_request: int = Field(ge=1, le=10)
    output_mode: Literal["json_object", "prompt"]
    max_output_tokens: int = Field(ge=256, le=65536)
    timeout_seconds: float = Field(gt=0, le=600)
    max_retries: Literal[0] = 0

    @model_validator(mode="after")
    def parameters_are_reproducible(self) -> "ExperimentConfig":
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("sample_ids must be unique")
        if self.cases_per_request > self.max_test_cases:
            raise ValueError("cases_per_request must not exceed max_test_cases")
        return self


class SampleRun(EvalModel):
    """One planned sample/repetition slot, including failures and partial evidence."""

    sample_id: Text
    repetition: int = Field(ge=1)
    status: Literal["planned", "running", "complete", "failed", "interrupted"]
    input_sha256: Sha256
    started_at: datetime | None = None
    ended_at: datetime | None = None
    generation_run_id: Text | None = None
    prompt_version: Text | None = None
    attempts: list[AttemptRecord] = Field(default_factory=list)
    raw_artifact_path: Text | None = None
    raw_artifact_sha256: Sha256 | None = None
    validated_artifact_path: Text | None = None
    validated_artifact_sha256: Sha256 | None = None
    error_stage: Text | None = None
    error_type: Text | None = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)

    @field_validator("started_at", "ended_at")
    @classmethod
    def optional_timestamps_have_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("sample timestamps must include a timezone")
        return value


class RunManifest(EvalModel):
    """Run identity, approved benchmark binding, environment, and every planned slot."""

    schema_version: Literal[1] = 1
    run_id: Text
    created_at: datetime
    protocol_version: Text
    status: Literal["running", "complete", "interrupted"]
    dataset_id: Text
    dataset_version: Text
    dataset_sha256: Sha256
    approval: DatasetApproval
    approval_sha256: Sha256
    dataset_snapshot_path: Text
    approval_snapshot_path: Text
    prompt_version: Text
    requested_model: Text
    reported_model_version: Text
    code_revision: Text | None = None
    workspace_dirty: bool | None = None
    source_sha256: Sha256
    runtime: dict[str, JsonValue]
    config: ExperimentConfig
    slots: list[SampleRun] = Field(min_length=1)
    manifest_path: str

    @field_validator("created_at")
    @classmethod
    def created_at_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value

    @model_validator(mode="after")
    def slots_match_the_frozen_experiment(self) -> "RunManifest":
        if self.dataset_sha256 != self.config.dataset_sha256:
            raise ValueError("manifest dataset digest does not match experiment config")
        keys = [(slot.sample_id, slot.repetition) for slot in self.slots]
        if len(keys) != len(set(keys)):
            raise ValueError("run manifest contains duplicate sample/repetition slots")
        expected = {
            (sample_id, repetition)
            for sample_id in self.config.sample_ids
            for repetition in range(1, self.config.repetitions + 1)
        }
        if set(keys) != expected:
            raise ValueError("run manifest slots do not match the frozen experiment config")
        return self

