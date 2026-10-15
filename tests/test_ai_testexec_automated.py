import copy

import pytest

from ai_testexec.adapter import create_automated_bundle, verify_automated_bundle
from ai_testexec.errors import GateError
from ai_testexec.models import AutomatedPermit
from ai_testexec.review import create_review
from tests.support.ai_case_examples import contract, generate_source, mapping


def test_automated_plan_gets_machine_permit_distinct_from_human_review(tmp_path):
    source = generate_source(tmp_path)
    plan = mapping(source)

    bundle, outcomes, permit = create_automated_bundle(source, plan, contract())

    assert bundle is not None
    assert permit is not None
    assert isinstance(permit, AutomatedPermit)
    assert bundle.review_id is None
    assert bundle.automated_permit_id == permit.permit_id
    assert [item.status for item in outcomes] == ["ready"]
    assert verify_automated_bundle(bundle, permit, contract()) == bundle


@pytest.mark.parametrize("tamper", ["source", "mapping", "openapi"])
def test_automated_permit_rejects_changed_bound_inputs(tmp_path, tamper):
    source = generate_source(tmp_path)
    plan = mapping(source)
    openapi = contract()
    bundle, _, permit = create_automated_bundle(source, plan, openapi)

    changed_bundle = copy.deepcopy(bundle.model_dump(mode="json"))
    changed_openapi = copy.deepcopy(openapi)
    if tamper == "source":
        changed_bundle["source"]["test_suite"]["suite_title"] += " changed"
    elif tamper == "mapping":
        changed_bundle["mapping"]["cases"][0]["operations"][0]["json_body"]["discount_amount_cents"]["value"] = 1
    else:
        changed_openapi["info"]["description"] = "changed contract"

    with pytest.raises(GateError):
        verify_automated_bundle(changed_bundle, permit, changed_openapi)


def test_automated_planner_rejects_unknown_operation_and_missing_case_mapping(tmp_path):
    source = generate_source(tmp_path)
    plan = mapping(source)
    plan["cases"][0]["operations"][0]["operation_id"] = "runArbitraryCode"
    bundle, outcomes, permit = create_automated_bundle(source, plan, contract())
    assert bundle is None
    assert permit is None
    assert outcomes[0].status == "plan_invalid"

    empty_plan = {"version": 1, "source_run_id": source["metadata"]["run_id"], "cases": []}
    bundle, outcomes, permit = create_automated_bundle(source, empty_plan, contract())
    assert bundle is None
    assert permit is None
    assert outcomes[0].status == "plan_invalid"


def test_automated_planner_keeps_unverifiable_expectations_blocked(tmp_path):
    from ai_testgen.models import SourceReference, TestSuite
    from ai_testgen.validator import validate_suite

    source = generate_source(tmp_path)
    expectation = "The business rule for restoring coupon inventory after failure is unspecified."
    source["test_suite"]["test_cases"][0]["unverifiable_expectations"] = [
        expectation
    ]
    source["validation"] = validate_suite(
        TestSuite.model_validate(source["test_suite"]),
        source_catalog=[SourceReference.model_validate(item) for item in source["source_catalog"]],
        openapi_document=contract(),
    ).model_dump(mode="json")

    bundle, outcomes, permit = create_automated_bundle(source, mapping(source), contract())

    assert bundle is None
    assert permit is None
    assert outcomes[0].status == "plan_invalid"
    assert outcomes[0].code == "unverifiable_expectation"


def test_manual_review_still_requires_a_named_human_reviewer(tmp_path):
    source = generate_source(tmp_path)
    plan = mapping(source)

    with pytest.raises(TypeError):
        create_review(source, plan, contract())

    receipt = create_review(source, plan, contract(), reviewer="reviewer-1")
    assert receipt.reviewer == "reviewer-1"
