import copy

import pytest

from tests.support.ai_case_examples import contract, generate_source, mapping, literal, ref

pytestmark = pytest.mark.regression


@pytest.fixture
def inputs(tmp_path):
    source = generate_source(tmp_path / "generated")
    return source, mapping(source), contract()


def reviewed(inputs):
    from ai_testexec.review import create_review
    return create_review(*inputs, reviewer="human-tester")


def test_normal_mock_generation_schema_review_and_conversion(inputs):
    from ai_testexec.adapter import CaseAdapter, verify_bundle
    receipt = reviewed(inputs)
    result = CaseAdapter().adapt(*inputs, receipt)
    assert [o.status for o in result.outcomes] == ["ready"]
    assert result.bundle.cases[0].generation_run_id == inputs[0]["metadata"]["run_id"]
    assert verify_bundle(result.bundle, receipt, inputs[2]).cases


@pytest.mark.parametrize("change,code", [
    (lambda m: m["cases"][0].pop("operations"), "schema_invalid"),
    (lambda m: m["cases"][0]["operations"][0].update(path="/nonexistent"), "contract_invalid"),
    (lambda m: m["cases"][0]["operations"][0].update(method="TRACE"), "schema_invalid"),
    (lambda m: m["cases"][0]["operations"][0]["json_body"].update(total_quantity=literal("1")), "contract_invalid"),
    (lambda m: m["cases"][0]["operations"][0].update(op="python", code="print(1)"), "schema_invalid"),
    (lambda m: m["cases"][0]["operations"][0]["json_body"].update(name=ref("later")), "contract_invalid"),
])
def test_invalid_mapping_is_reported_before_conversion(inputs, change, code):
    from ai_testexec.adapter import CaseAdapter
    source, plan, schema = inputs
    change(plan)
    result = CaseAdapter().adapt(source, plan, schema, None)
    assert result.bundle is None
    assert result.outcomes[0].code == code


def test_unreviewed_case_is_blocked(inputs):
    from ai_testexec.adapter import CaseAdapter
    result = CaseAdapter().adapt(*inputs, None)
    assert result.bundle is None
    assert result.outcomes[0].code == "review_required"


def test_review_becomes_invalid_if_mapping_changes(inputs):
    from ai_testexec.adapter import CaseAdapter
    receipt = reviewed(inputs)
    inputs[1]["cases"][0]["operations"][0]["json_body"]["total_quantity"] = literal(2)
    result = CaseAdapter().adapt(*inputs, receipt)
    assert result.outcomes[0].code == "review_invalid"


def test_missing_mapping_requires_manual_processing(inputs):
    from ai_testexec.adapter import CaseAdapter
    source, plan, schema = inputs
    plan["cases"] = []
    result = CaseAdapter().adapt(source, plan, schema, reviewed((source, plan, schema)))
    assert result.outcomes[0].status == "needs_manual_mapping"
    assert result.bundle is None


def test_duplicate_generated_plan_with_different_case_id_is_identified(inputs):
    from ai_testexec.adapter import CaseAdapter
    first = CaseAdapter().adapt(*inputs, reviewed(inputs))
    other_source = copy.deepcopy(inputs[0])
    other_source["metadata"]["run_id"] += "-again"
    other_source["test_suite"]["test_cases"][0]["case_id"] = "generated_again"
    other_plan = copy.deepcopy(inputs[1])
    other_plan["source_run_id"] = other_source["metadata"]["run_id"]
    other_plan["cases"][0]["source_case_id"] = "generated_again"
    second_inputs = other_source, other_plan, inputs[2]
    second = CaseAdapter().adapt(*second_inputs, reviewed(second_inputs),
                                seen={first.bundle.cases[0].fingerprint: (inputs[0]["metadata"]["run_id"], "order_non_stacking")})
    assert second.bundle is None
    assert second.outcomes[0].code == "duplicate_generated"


def test_edited_executable_bundle_is_rejected(inputs):
    from ai_testexec.adapter import CaseAdapter, verify_bundle
    from ai_testexec.errors import GateError
    receipt = reviewed(inputs)
    result = CaseAdapter().adapt(*inputs, receipt)
    raw = result.bundle.model_dump(mode="json")
    raw["cases"][0]["mapping"]["operations"][1]["expected"] = 200
    with pytest.raises(GateError):
        verify_bundle(raw, receipt, inputs[2])


def test_external_schema_reference_is_refused(inputs):
    from ai_testexec.adapter import CaseAdapter
    inputs[2]["components"]["schemas"]["CouponCreateRequest"] = {"$ref": "https://example.test/schema"}
    result = CaseAdapter().adapt(*inputs, None)
    assert result.outcomes[0].code == "contract_invalid"


def test_literal_json_shaped_like_variable_reference_stays_literal(inputs):
    from ai_testexec.adapter import CaseAdapter, verify_bundle
    source, plan, schema = inputs
    case = plan["cases"][0]
    case["operations"] = [
        {"op": "http_request", "step_id": "redeem", "operation_id": "redeemCoupon", "method": "POST",
         "path": "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
         "path_params": {"user_id": ref("user"), "user_coupon_id": literal("claim-1")},
         "json_body": {"order_id": ref("order"), "order_amount_cents": literal(10000)}},
        {"op": "assert_status", "step_id": "status", "request_id": "redeem", "expected": 422},
        {"op": "assert_json", "step_id": "error", "request_id": "redeem", "pointer": "",
         "expected": literal({"detail": [{"loc": ["body", "x"], "msg": "invalid", "type": "datetime_parsing",
                                         "ctx": {"kind": "ref", "name": "missing"}}]})},
    ]
    receipt = reviewed(inputs)
    result = CaseAdapter().adapt(source, plan, schema, receipt)
    assert result.bundle is not None
    assert result.outcomes[0].status == "ready"
    verified = verify_bundle(result.bundle, receipt, schema)
    assert verified.cases[0].mapping.operations[-1].expected.value["detail"][0]["ctx"]["name"] == "missing"


def test_acknowledging_warnings_does_not_make_unknown_expectations_executable(inputs):
    from ai_testexec.adapter import CaseAdapter
    from ai_testexec.errors import GateError
    from ai_testexec.review import create_review
    from ai_testgen.models import TestSuite, SourceReference
    from ai_testgen.validator import validate_suite
    source, plan, schema = inputs
    source["test_suite"]["test_cases"][0]["unverifiable_expectations"] = ["尚不清楚失败后的重试规则"]
    source["validation"] = validate_suite(
        TestSuite.model_validate(source["test_suite"]),
        source_catalog=[SourceReference.model_validate(ref) for ref in source["source_catalog"]],
        openapi_document=schema,
    ).model_dump(mode="json")
    with pytest.raises(GateError, match="警告"):
        create_review(source, plan, schema, reviewer="tester")
    receipt = create_review(source, plan, schema, reviewer="tester", acknowledged_warnings=True)
    result = CaseAdapter().adapt(source, plan, schema, receipt)
    assert result.bundle is None
    assert result.outcomes[0].status == "needs_manual_mapping"
