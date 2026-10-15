import pytest

from ai_testexec.models import CaseMapping, MappingBundle
from ai_testgen.llm_client import LLMResult
from ai_testgen.models import TokenUsage
from ai_testgen.planner import (
    ExecutionPlanDraft,
    PlanValidationError,
    generate_execution_plan,
)
from tests.support.ai_case_examples import generate_source, mapping


def test_plan_uses_separate_structured_model_call_and_binds_generated_source(tmp_path):
    source = generate_source(tmp_path)
    mapped_source = mapping(source)
    draft = ExecutionPlanDraft.model_validate({"cases": mapped_source["cases"], "unmapped_cases": []})
    calls = []

    class FakeClient:
        def generate_structured(self, *, messages, response_model):
            calls.append((messages, response_model))
            return LLMResult(
                raw_text=draft.model_dump_json(),
                parsed=draft,
                usage=TokenUsage(prompt_tokens=321, completion_tokens=123, total_tokens=444),
                response_model=response_model.__name__,
            )

    result = generate_execution_plan(
        FakeClient(),
        source=source,
        requirements="One order can redeem one coupon.",
        business_rules=None,
        constraints=None,
        openapi={},
    )

    assert len(calls) == 1
    messages, response_model = calls[0]
    assert response_model is ExecutionPlanDraft
    assert result.mapping == MappingBundle(
        version=1,
        source_run_id=source["metadata"]["run_id"],
        cases=[CaseMapping.model_validate(case) for case in mapped_source["cases"]],
    )
    assert result.llm_result.raw_text == draft.model_dump_json()
    assert result.llm_result.usage.total_tokens == 444
    assert "order_non_stacking" in messages[1]["content"]
    assert "redeemCoupon" in messages[1]["content"]


def test_plan_rejects_duplicate_and_unknown_source_case_ids(tmp_path):
    source = generate_source(tmp_path)
    mapped_source = mapping(source)
    invalid = ExecutionPlanDraft.model_validate(
        {"cases": [mapped_source["cases"][0], mapped_source["cases"][0]], "unmapped_cases": []}
    )

    class FakeClient:
        def generate_structured(self, *, messages, response_model):
            return LLMResult(invalid.model_dump_json(), invalid, TokenUsage(), "ExecutionPlanDraft")

    try:
        generate_execution_plan(
            FakeClient(),
            source=source,
            requirements="One order can redeem one coupon.",
            business_rules=None,
            constraints=None,
            openapi={},
        )
    except PlanValidationError as error:
        assert error.code == "plan_cases_mismatch"
    else:
        raise AssertionError("duplicate source case mappings must be rejected")

    unknown_case = dict(mapped_source["cases"][0])
    unknown_case["source_case_id"] = "unknown_case"
    unknown = ExecutionPlanDraft.model_validate({"cases": [unknown_case], "unmapped_cases": []})

    class UnknownClient:
        def generate_structured(self, *, messages, response_model):
            return LLMResult(unknown.model_dump_json(), unknown, TokenUsage(), "ExecutionPlanDraft")

    with pytest.raises(PlanValidationError) as error:
        generate_execution_plan(
            UnknownClient(), source=source, requirements="One order can redeem one coupon.",
            business_rules=None, constraints=None, openapi={},
        )
    assert error.value.code == "plan_cases_mismatch"
    assert error.value.llm_result.raw_text == unknown.model_dump_json()


def test_plan_can_explicitly_mark_case_unmapped_with_a_reason(tmp_path):
    source = generate_source(tmp_path)
    draft = ExecutionPlanDraft.model_validate({
        "cases": [],
        "unmapped_cases": [{
            "source_case_id": "order_non_stacking",
            "reason": "This case requires an operation not supported by the local executor.",
        }],
    })

    class FakeClient:
        def generate_structured(self, *, messages, response_model):
            return LLMResult(draft.model_dump_json(), draft, TokenUsage(), "ExecutionPlanDraft")

    result = generate_execution_plan(
        FakeClient(), source=source,
        requirements="One order can redeem one coupon.", business_rules=None,
        constraints=None, openapi={},
    )

    assert result.mapping.cases == []
    assert result.unmapped_cases[0].source_case_id == "order_non_stacking"
    assert "not supported" in result.unmapped_cases[0].reason


def test_plan_draft_rejects_model_supplied_host_or_script_fields():
    mapping_case = {
        "source_case_id": "case_1",
        "environment": "local-coupon",
        "fixtures": [],
        "operations": [{"op": "assert_status", "step_id": "check", "request_id": "call", "expected": 200}],
    }
    payload = {"cases": [mapping_case], "unmapped_cases": []}
    mapping_case["host"] = "http://example.invalid"

    try:
        ExecutionPlanDraft.model_validate(payload)
    except Exception as error:
        assert "host" in str(error)
    else:
        raise AssertionError("model-supplied destination fields must be rejected")
