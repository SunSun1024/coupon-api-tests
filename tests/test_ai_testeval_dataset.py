from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from ai_testeval.dataset import (
    DatasetApprovalError,
    create_approval,
    dataset_digest,
    verify_approval,
)
from ai_testeval.models import GoldenDataset


def valid_dataset_payload():
    return {
        "schema_version": 1,
        "dataset_id": "coupon-redemption",
        "dataset_version": "1.0.0",
        "description": "Human-reviewed coupon redemption risks.",
        "samples": [
            {
                "sample_id": "redeem-coupon",
                "requirement_id": "REQ-001",
                "requirement_version": "coupon-v1",
                "requirements": "Claimed coupons may be redeemed when the order meets the minimum.",
                "business_rules": [
                    {
                        "rule_id": "RULE-001",
                        "text": "Minimum spend is 10000 cents.",
                        "source_refs": ["REQ-001"],
                    }
                ],
                "api_constraints": [
                    {
                        "constraint_id": "API-001",
                        "text": "POST /redemptions returns a redemption result.",
                    }
                ],
                "openapi_document": {
                    "openapi": "3.1.0",
                    "paths": {"/redemptions": {"post": {"operationId": "redeemCoupon"}}},
                },
                "generation_focus": "Cover the minimum-spend boundary.",
                "risks": [
                    {
                        "risk_id": "R-001",
                        "description": "The exact minimum order is accepted.",
                        "severity": "high",
                        "source_refs": ["RULE-001", "API-001"],
                        "rationale": "The minimum is inclusive.",
                    }
                ],
                "expected_scenarios": [
                    {
                        "scenario_id": "S-001",
                        "scenario_type": "boundary",
                        "risk_ids": ["R-001"],
                        "preconditions": ["The user has claimed an unexpired coupon."],
                        "actions": ["Redeem it with an order amount of 10000 cents."],
                        "assertions": ["The redemption succeeds."],
                    }
                ],
                "rubric_version": "coupon-rubric-v1",
            }
        ],
    }


def test_dataset_rejects_duplicate_ids_and_unknown_risk_refs():
    payload = valid_dataset_payload()
    sample = payload["samples"][0]
    sample["risks"].append({
        **sample["risks"][0],
        "description": "Second risk reusing the same ID.",
        "source_refs": ["REQ-001"],
    })
    sample["expected_scenarios"][0]["risk_ids"] = ["R-UNKNOWN"]

    with pytest.raises(ValidationError) as error:
        GoldenDataset.model_validate(payload)

    message = str(error.value)
    assert "duplicate risk_id" in message
    assert "R-UNKNOWN" in message


def test_approval_is_bound_to_exact_dataset_content():
    dataset = GoldenDataset.model_validate(valid_dataset_payload())
    approval = create_approval(
        dataset,
        reviewer="sunsun",
        reviewed_at=datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc),
    )

    assert approval.dataset_sha256 == dataset_digest(dataset)
    verify_approval(dataset, approval)

    changed = dataset.model_copy(update={"description": "Edited after approval."})
    with pytest.raises(DatasetApprovalError, match="digest"):
        verify_approval(changed, approval)


def test_approval_requires_an_explicit_human_reviewer():
    dataset = GoldenDataset.model_validate(valid_dataset_payload())

    with pytest.raises(ValueError, match="reviewer"):
        create_approval(dataset, reviewer=" ")
