from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    UserCouponResponse,
)


pytestmark = pytest.mark.regression


@pytest.fixture
def claimed_coupon(api_client):
    """创建一张新券并由一个用户领取，供本模块验证用户隔离。"""
    owner_user_id = f"test-user-{uuid4()}"
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    create_response = api_client.create_coupon(
        {
            "name": f"ownership-{uuid4()}",
            "discount_amount_cents": 2000,
            "minimum_order_amount_cents": 10000,
            "total_quantity": 1,
            "expires_at": expires_at,
        }
    )
    assert create_response.status_code == 201, create_response.text
    created_coupon = CouponCreatedResponse.model_validate(create_response.json())
    coupon_id = created_coupon.coupon_id

    claim_response = api_client.claim_coupon(owner_user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    claim = CouponClaimedResponse.model_validate(claim_response.json())
    user_coupon_id = claim.user_coupon_id

    return {
        "owner_user_id": owner_user_id,
        "user_coupon_id": user_coupon_id,
    }


def test_user_cannot_query_coupon_owned_by_another_user(api_client, claimed_coupon):
    other_user_id = f"test-user-{uuid4()}"

    response = api_client.get_user_coupon(
        other_user_id, claimed_coupon["user_coupon_id"]
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "claimed coupon not found"


def test_user_cannot_redeem_coupon_owned_by_another_user(api_client, claimed_coupon):
    other_user_id = f"test-user-{uuid4()}"

    redemption = api_client.redeem_coupon(
        other_user_id, claimed_coupon["user_coupon_id"]
    )
    owner_view = api_client.get_user_coupon(
        claimed_coupon["owner_user_id"], claimed_coupon["user_coupon_id"]
    )

    assert redemption.status_code == 404, redemption.text
    assert owner_view.status_code == 200, owner_view.text
    owner_coupon = UserCouponResponse.model_validate(owner_view.json())
    assert owner_coupon.status == "claimed"
