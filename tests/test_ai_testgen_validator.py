import pytest

from ai_testgen.models import SourceReference, TestSuite as SuiteModel
from ai_testgen.validator import (
    BusinessRuleValidationError,
    OpenAPIDocumentError,
    validate_suite,
)


pytestmark = pytest.mark.regression


def step(**overrides):
    payload = {
        "step_number": 1,
        "action": "Submit an order for 100.00 yuan",
        "expected_result": "The redemption succeeds",
        "api_path": "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
        "http_method": "POST",
        "operation_id": "redeemCoupon",
    }
    payload.update(overrides)
    return payload


def case(**overrides):
    payload = {
        "case_id": "redeem_at_minimum",
        "title": "Redeem at the minimum spend",
        "objective": "Verify redemption at the minimum spend",
        "priority": "P1",
        "scenario_type": "boundary",
        "preconditions": ["The user has claimed a valid coupon"],
        "steps": [step()],
        "expected_results": ["The coupon becomes redeemed"],
        "source_refs": ["REQ-001", "API-001"],
        "unverifiable_expectations": [],
    }
    payload.update(overrides)
    return payload


def suite(cases=None, questions=None):
    return SuiteModel.model_validate(
        {
            "suite_title": "Coupon redemption",
            "test_cases": cases if cases is not None else [case()],
            "clarification_questions": questions if questions is not None else [],
        }
    )


def source_catalog():
    return [
        SourceReference(
            source_id="REQ-001",
            kind="requirement",
            description="Order must meet the minimum spend",
            location="requirements paragraph 1",
        ),
        SourceReference(
            source_id="API-001",
            kind="api_operation",
            description="POST coupon redemption",
            location="openapi paths entry",
        ),
    ]


def openapi_document():
    return {
        "openapi": "3.1.0",
        "paths": {
            "/users/{user_id}/coupons/{user_coupon_id}/redemptions": {
                "post": {"operationId": "redeemCoupon"}
            }
        },
    }


def test_validates_sources_steps_and_openapi_operation():
    report = validate_suite(
        suite(), source_catalog=source_catalog(), openapi_document=openapi_document()
    )

    assert report.status == "passed"
    assert report.case_count == 1
    assert report.warnings == []


def test_rejects_suites_that_exceed_the_configured_case_limit():
    second_case = case(
        case_id="redeem_over_minimum",
        objective="Verify redemption above the minimum spend",
        steps=[step(action="Submit an order above the minimum amount")],
    )

    with pytest.raises(BusinessRuleValidationError, match="maximum allowed test case count"):
        validate_suite(
            suite(cases=[case(), second_case]),
            source_catalog=source_catalog(),
            max_test_cases=1,
        )


def test_rejects_non_contiguous_steps_and_unknown_source_ids_without_echoing_source_text():
    with pytest.raises(BusinessRuleValidationError, match="step_number"):
        validate_suite(
            suite(cases=[case(steps=[step(step_number=2)])]),
            source_catalog=source_catalog(),
        )

    private_source_id = "private-user@example.test"
    with pytest.raises(BusinessRuleValidationError) as exc_info:
        validate_suite(
            suite(cases=[case(source_refs=[private_source_id])]),
            source_catalog=source_catalog(),
        )
    assert "unknown source IDs" in str(exc_info.value)
    assert private_source_id not in str(exc_info.value)


def test_rejects_duplicate_case_ids_and_semantically_identical_cases():
    original = case()
    same_id = case(title="Another title")
    with pytest.raises(BusinessRuleValidationError, match="duplicate case_id"):
        validate_suite(
            suite(cases=[original, same_id]), source_catalog=source_catalog()
        )

    same_content_different_id = case(case_id="duplicate_case")
    with pytest.raises(BusinessRuleValidationError, match="duplicate test cases"):
        validate_suite(
            suite(cases=[original, same_content_different_id]),
            source_catalog=source_catalog(),
        )


def test_rejects_illegal_api_method_path_and_operation_id():
    invalid_method = case(steps=[step(http_method="GET")])
    with pytest.raises(BusinessRuleValidationError, match="not found in OpenAPI"):
        validate_suite(
            suite(cases=[invalid_method]),
            source_catalog=source_catalog(),
            openapi_document=openapi_document(),
        )

    invalid_operation = case(steps=[step(operation_id="anotherOperation")])
    with pytest.raises(BusinessRuleValidationError, match="operationId"):
        validate_suite(
            suite(cases=[invalid_operation]),
            source_catalog=source_catalog(),
            openapi_document=openapi_document(),
        )


def test_marks_expectations_and_routes_that_cannot_be_verified():
    report = validate_suite(
        suite(cases=[case(unverifiable_expectations=["The coupon is restored after failure"])]),
        source_catalog=source_catalog(),
    )

    assert report.status == "passed_with_warnings"
    assert any("unverifiable" in warning for warning in report.warnings)
    assert any("OpenAPI" in warning for warning in report.warnings)


def test_rejects_malformed_openapi_input():
    with pytest.raises(OpenAPIDocumentError, match="paths"):
        validate_suite(
            suite(), source_catalog=source_catalog(), openapi_document={"openapi": "3.1.0"}
        )
