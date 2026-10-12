import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    CouponRedemptionResponse,
    UserCouponResponse,
)


pytestmark = pytest.mark.regression

DATA_FILE = Path(__file__).parent / "data" / "coupon_redemption_cases.json"
with DATA_FILE.open(encoding="utf-8") as file:
    REDEMPTION_CASES = json.load(file)


@pytest.mark.parametrize(
    "case",
    REDEMPTION_CASES,
    ids=[case["case_id"] for case in REDEMPTION_CASES],
)
def test_coupon_redemption_cases_from_json(api_client, case):
    """用 JSON 中的每条数据独立验证一组满减核销规则。"""
    user_id = f"test-user-{uuid4()}"
    order_id = f"test-order-{uuid4()}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    create_response = api_client.create_coupon(
        {
            "name": f"json-case-{case['case_id']}-{uuid4()}",
            "discount_amount_cents": case["discount_amount_cents"],
            "minimum_order_amount_cents": case["minimum_order_amount_cents"],
            "total_quantity": 1,
            "expires_at": expires_at,
        }
    )
    assert create_response.status_code == 201, create_response.text
    created_coupon = CouponCreatedResponse.model_validate(create_response.json())
    coupon_id = created_coupon.coupon_id

    claim_response = api_client.claim_coupon(user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    claim = CouponClaimedResponse.model_validate(claim_response.json())
    user_coupon_id = claim.user_coupon_id

    redemption_response = api_client.redeem_coupon(
        user_id,
        user_coupon_id,
        order_amount_cents=case["order_amount_cents"],
        order_id=order_id,
    )

    assert redemption_response.status_code == case["expected_status_code"], (
        f"case={case['case_id']}, response={redemption_response.text}"
    )
    if redemption_response.status_code == 200:
        redemption = CouponRedemptionResponse.model_validate(
            redemption_response.json()
        )
        applied_discount_cents = redemption.applied_discount_cents
        payable_amount_cents = redemption.payable_amount_cents
    else:
        error_body = redemption_response.json()
        applied_discount_cents = error_body.get("applied_discount_cents")
        payable_amount_cents = error_body.get("payable_amount_cents")
    assert applied_discount_cents == case["expected_applied_discount_cents"]
    assert payable_amount_cents == case["expected_payable_amount_cents"]

    query_response = api_client.get_user_coupon(user_id, user_coupon_id)
    assert query_response.status_code == 200, query_response.text
    saved_coupon = UserCouponResponse.model_validate(query_response.json())
    assert saved_coupon.status == case["expected_coupon_status"]
    assert saved_coupon.applied_discount_cents == case[
        "expected_applied_discount_cents"
    ]
    assert saved_coupon.payable_amount_cents == case[
        "expected_payable_amount_cents"
    ]
    if case["expected_coupon_status"] == "redeemed":
        assert saved_coupon.order_id == order_id
