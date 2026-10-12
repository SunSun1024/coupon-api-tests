from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from coupon_api.database import connect
from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    UserCouponResponse,
)


pytestmark = pytest.mark.regression


def create_coupon(api_client):
    response = api_client.create_coupon(
        {
            "name": f"expiration-{uuid4()}",
            "discount_amount_cents": 2000,
            "minimum_order_amount_cents": 10000,
            "total_quantity": 1,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(days=1)
            ).isoformat(),
        }
    )
    assert response.status_code == 201, response.text
    return CouponCreatedResponse.model_validate(response.json()).coupon_id


def set_coupon_expired(coupon_id):
    expired_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    with connect() as connection:
        connection.execute(
            "UPDATE coupons SET expires_at = ? WHERE coupon_id = ?",
            (expired_at, coupon_id),
        )


def test_create_coupon_rejects_expiration_in_the_past(api_client):
    response = api_client.create_coupon(
        {
            "name": f"already-expired-{uuid4()}",
            "discount_amount_cents": 2000,
            "minimum_order_amount_cents": 10000,
            "total_quantity": 1,
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(days=1)
            ).isoformat(),
        }
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "expires_at must be in the future"


def test_expired_coupon_cannot_be_claimed_and_keeps_its_stock(api_client):
    coupon_id = create_coupon(api_client)
    set_coupon_expired(coupon_id)

    response = api_client.claim_coupon(f"expired-user-{uuid4()}", coupon_id)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == "coupon has expired"
    with connect() as connection:
        remaining_quantity = connection.execute(
            "SELECT remaining_quantity FROM coupons WHERE coupon_id = ?",
            (coupon_id,),
        ).fetchone()["remaining_quantity"]
    assert remaining_quantity == 1


def test_coupon_expired_after_claim_cannot_be_redeemed(api_client):
    user_id = f"expired-user-{uuid4()}"
    coupon_id = create_coupon(api_client)
    claim_response = api_client.claim_coupon(user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    claim = CouponClaimedResponse.model_validate(claim_response.json())
    set_coupon_expired(coupon_id)

    redemption = api_client.redeem_coupon(
        user_id, claim.user_coupon_id, order_amount_cents=12000
    )
    state = api_client.get_user_coupon(user_id, claim.user_coupon_id)

    assert redemption.status_code == 409, redemption.text
    assert redemption.json()["detail"] == "coupon has expired"
    assert state.status_code == 200, state.text
    saved_coupon = UserCouponResponse.model_validate(state.json())
    assert saved_coupon.status == "claimed"
    assert saved_coupon.redeemed_at is None
    assert saved_coupon.applied_discount_cents is None
    assert saved_coupon.payable_amount_cents is None
