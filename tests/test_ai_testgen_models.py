import pytest
from pydantic import ValidationError

from ai_testgen.models import TestCase as CaseModel
from ai_testgen.models import TestCaseBatch as BatchModel
from ai_testgen.models import TestStep as StepModel
from ai_testgen.models import TestSuite as SuiteModel


pytestmark = pytest.mark.regression


def valid_step(**overrides):
    payload = {
        "step_number": 1,
        "action": "Submit an order for 100.00 yuan",
        "expected_result": "The redemption request succeeds",
        "api_path": "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
        "http_method": "POST",
        "operation_id": "redeemCoupon",
    }
    payload.update(overrides)
    return payload


def valid_case(**overrides):
    payload = {
        "case_id": "coupon_at_minimum",
        "title": "Redeem at the minimum spend",
        "objective": "Verify that an order at the threshold can use the coupon",
        "priority": "P1",
        "scenario_type": "boundary",
        "preconditions": ["The user has claimed an unexpired coupon"],
        "steps": [valid_step()],
        "expected_results": ["The coupon is redeemed"],
        "source_refs": ["REQ-001", "API-001"],
        "unverifiable_expectations": [],
    }
    payload.update(overrides)
    return payload


def valid_suite(**overrides):
    payload = {
        "suite_title": "Coupon redemption test cases",
        "test_cases": [valid_case()],
        "clarification_questions": [],
    }
    payload.update(overrides)
    return payload


def test_test_suite_accepts_a_fully_described_case():
    suite = SuiteModel.model_validate(valid_suite())

    assert suite.test_cases[0].case_id == "coupon_at_minimum"
    assert suite.test_cases[0].steps[0].http_method == "POST"
    assert suite.clarification_questions == []


def test_batch_response_can_signal_that_no_more_scenarios_are_available():
    batch = BatchModel.model_validate(
        {
            "suite_title": "Coupon redemption test cases",
            "test_cases": [],
            "clarification_questions": [],
        }
    )

    assert batch.test_cases == []
    with pytest.raises(ValidationError):
        SuiteModel.model_validate(
            {
                "suite_title": "Coupon redemption test cases",
                "test_cases": [],
                "clarification_questions": [],
            }
        )


def test_test_case_rejects_missing_fields_and_wrong_types():
    payload = valid_case()
    del payload["objective"]
    payload["priority"] = 1

    with pytest.raises(ValidationError):
        CaseModel.model_validate(payload)


def test_test_case_requires_at_least_one_step_and_expected_result():
    with pytest.raises(ValidationError):
        CaseModel.model_validate(valid_case(steps=[]))

    with pytest.raises(ValidationError):
        CaseModel.model_validate(valid_case(expected_results=[]))

    with pytest.raises(ValidationError):
        CaseModel.model_validate(valid_case(source_refs=[]))


def test_test_step_requires_an_expected_result_and_valid_http_method():
    with pytest.raises(ValidationError):
        StepModel.model_validate(valid_step(expected_result=""))

    with pytest.raises(ValidationError):
        StepModel.model_validate(valid_step(http_method="FETCH"))


def test_test_case_rejects_unexpected_fields_and_unsafe_ids():
    with pytest.raises(ValidationError):
        CaseModel.model_validate(valid_case(unreviewed_prompt="secret"))

    with pytest.raises(ValidationError):
        CaseModel.model_validate(valid_case(case_id="coupon case; import os"))
