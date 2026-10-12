import pytest
from pydantic import ValidationError

from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    CouponRedemptionResponse,
    UserCouponResponse,
)

pytestmark = pytest.mark.regression


@pytest.mark.parametrize(
    ("response_model", "payload"),
    [
        (CouponCreatedResponse, {"coupon_id": 123, "name": "spring"}),
        (
            CouponClaimedResponse,
            {"user_coupon_id": "claim-1", "coupon_id": "coupon-1", "status": "redeemed"},
        ),
        (
            CouponRedemptionResponse,
            {
                "user_coupon_id": "claim-1",
                "order_id": "order-1",
                "status": "redeemed",
                "applied_discount_cents": 2000,
                "payable_amount_cents": "10000",
            },
        ),
        (
            UserCouponResponse,
            {
                "user_coupon_id": "claim-1",
                "user_id": 123,
                "coupon_id": "coupon-1",
                "status": "claimed",
                "claimed_at": "2026-10-12T12:00:00+00:00",
                "order_id": None,
                "redeemed_at": None,
                "applied_discount_cents": None,
                "payable_amount_cents": None,
            },
        ),
    ],
    ids=["create", "claim-status", "redemption-amount", "query-user-id"],
)
def test_response_models_reject_wrong_field_types(response_model, payload):
    with pytest.raises(ValidationError):
        response_model.model_validate(payload)


@pytest.mark.parametrize(
    ("response_model", "payload"),
    [
        (CouponCreatedResponse, {"name": "spring"}),
        (
            CouponClaimedResponse,
            {"coupon_id": "coupon-1", "status": "claimed"},
        ),
        (
            CouponRedemptionResponse,
            {
                "user_coupon_id": "claim-1",
                "status": "redeemed",
                "applied_discount_cents": 2000,
                "payable_amount_cents": 10000,
            },
        ),
        (
            UserCouponResponse,
            {
                "user_coupon_id": "claim-1",
                "user_id": "user-1",
                "coupon_id": "coupon-1",
                "status": "claimed",
                "order_id": None,
                "redeemed_at": None,
                "applied_discount_cents": None,
                "payable_amount_cents": None,
            },
        ),
    ],
    ids=["create", "claim", "redemption", "query"],
)
def test_response_models_require_contract_fields(response_model, payload):
    with pytest.raises(ValidationError):
        response_model.model_validate(payload)
