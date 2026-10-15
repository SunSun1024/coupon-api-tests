"""Ask the configured model to translate checked business cases into restricted API steps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from ai_testexec.models import CaseMapping, MappingBundle, Model
from ai_testexec.io import parse
from ai_testgen.llm_client import LLMResult, OutputSchemaError
from ai_testexec.models import SourceArtifact
from ai_testgen.models import CaseId, NonEmptyString


PLAN_PROMPT_VERSION = "coupon-execution-plan-v1"


class UnmappedPlanCase(Model):
    """A generated case intentionally left unexecutable, never silently omitted."""

    source_case_id: CaseId
    reason: NonEmptyString


class ExecutionPlanDraft(Model):
    """Restricted mappings plus explicit reasons for cases the model cannot map safely."""

    cases: list[CaseMapping]
    unmapped_cases: list[UnmappedPlanCase]


class PlanValidationError(ValueError):
    """The structured plan does not cover the generated suite exactly once."""

    def __init__(self, code: str, message: str, *, llm_result: LLMResult | None = None):
        self.code = code
        self.llm_result = llm_result
        super().__init__(message)


@dataclass(frozen=True)
class PlanResult:
    llm_result: LLMResult
    mapping: MappingBundle
    unmapped_cases: tuple[UnmappedPlanCase, ...] = ()
    messages: tuple[dict[str, str], ...] = ()
    prompt_version: str = PLAN_PROMPT_VERSION


def generate_execution_plan(
    llm_client: Any,
    *,
    source: Mapping[str, Any],
    requirements: str,
    business_rules: str | None,
    constraints: str | None,
    openapi: Mapping[str, Any],
) -> PlanResult:
    """Make the second model call and bind its mappings to this exact source run."""
    try:
        artifact = parse(SourceArtifact, source)
    except (TypeError, ValueError):
        raise PlanValidationError("source_invalid", "已校验用例文件无法重新读取") from None

    messages = _build_messages(
        source=artifact.model_dump(mode="json"),
        requirements=requirements,
        business_rules=business_rules,
        constraints=constraints,
        openapi=openapi,
    )
    result: LLMResult = llm_client.generate_structured(
        messages=messages,
        response_model=ExecutionPlanDraft,
    )
    if not isinstance(result.parsed, ExecutionPlanDraft):
        raise OutputSchemaError(
            "LLM client returned a response outside the ExecutionPlanDraft model",
            raw_text=result.raw_text,
            usage=result.usage,
        )

    expected_ids = [case.case_id for case in artifact.test_suite.test_cases]
    mapped_ids = [case.source_case_id for case in result.parsed.cases]
    unmapped_ids = [case.source_case_id for case in result.parsed.unmapped_cases]
    all_ids = mapped_ids + unmapped_ids
    if len(set(all_ids)) != len(all_ids):
        raise PlanValidationError("plan_cases_mismatch", "执行计划重复引用同一条生成用例",
                                  llm_result=result)
    if set(all_ids) != set(expected_ids):
        raise PlanValidationError("plan_cases_mismatch", "每条生成用例必须映射或明确说明无法映射",
                                  llm_result=result)

    mappings = {case.source_case_id: case for case in result.parsed.cases}
    ordered_mappings = [mappings[case_id] for case_id in expected_ids if case_id in mappings]
    bundle = MappingBundle(
        version=1,
        source_run_id=artifact.metadata.run_id,
        cases=ordered_mappings,
    )
    return PlanResult(
        llm_result=result,
        mapping=bundle,
        unmapped_cases=tuple(result.parsed.unmapped_cases),
        messages=tuple(messages),
    )


def _build_messages(*, source, requirements, business_rules, constraints, openapi):
    system_message = """You translate already generated Coupon API test cases into the supplied restricted JSON execution format.
Return only JSON matching the provided ExecutionPlanDraft schema. Every supplied case_id must appear exactly once across cases and unmapped_cases, with no other IDs. Put a case in unmapped_cases with a concise reason if it has an unverifiable expectation or cannot be represented using the operations, fields, fixtures, and assertions in the schema; do not invent behavior. Preserve each mapped case's intended action, preconditions, expected results, and covered order of steps. Use only operation_ids and method/path pairs found in the supplied OpenAPI document. Use only the declared operations: http_request, assert_status, and assert_json. Each HTTP request must be followed immediately by an assert_status operation before any later request. Use fixture factories only for unique user_id, order_id, coupon_name, and expires_at values; use prior response captures through typed refs for generated coupon IDs and user-coupon IDs. Assertions must come from the supplied requirement, business rule, or generated expected result. Do not output a host, URL, port, headers, cookies, SQL, Python, shell, or other code. Do not claim that plan validation proves business correctness."""
    payload = {
        "requirements": requirements,
        "business_rules": business_rules,
        "constraints": constraints,
        "checked_source_artifact": source,
        "trusted_openapi": openapi,
        "response_schema": ExecutionPlanDraft.model_json_schema(),
    }
    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
    ]
