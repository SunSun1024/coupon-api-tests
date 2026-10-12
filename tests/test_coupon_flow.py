from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    CouponRedemptionResponse,
    UserCouponResponse,
)


pytestmark = pytest.mark.regression

# 用户创建优惠券
def create_coupon(
    api_client,
    total_quantity=5,
    minimum_order_amount_cents=10000,
    discount_amount_cents=2000,
):
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = api_client.create_coupon(
        {
            "name": f"integration-{uuid4()}",
            "discount_amount_cents": discount_amount_cents,
            "minimum_order_amount_cents": minimum_order_amount_cents,
            "total_quantity": total_quantity,
            "expires_at": expires_at,
        }
    )
    assert response.status_code == 201, response.text
    created_coupon = CouponCreatedResponse.model_validate(response.json())
    return created_coupon.coupon_id

# 用户领取优惠券
def claim_coupon(api_client, user_id, coupon_id):
    return api_client.claim_coupon(user_id, coupon_id)

# 用户核销优惠券
def redeem_coupon(
    api_client, user_id, user_coupon_id, order_amount_cents=12000
):
    return api_client.redeem_coupon(user_id, user_coupon_id, order_amount_cents)

# 获取用户的优惠券信息
def get_user_coupon(api_client, user_id, user_coupon_id):
    return api_client.get_user_coupon(user_id, user_coupon_id)


@pytest.mark.smoke
def test_user_can_claim_redeem_and_query_coupon(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(api_client)
    claim_response = claim_coupon(api_client, user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    claim = CouponClaimedResponse.model_validate(claim_response.json())
    user_coupon_id = claim.user_coupon_id
    assert claim.status == "claimed"

    order_id = f"test-order-{uuid4()}"
    redeem_response = api_client.redeem_coupon(
        user_id, user_coupon_id, order_amount_cents=12000, order_id=order_id
    )
    assert redeem_response.status_code == 200, redeem_response.text
    redemption = CouponRedemptionResponse.model_validate(redeem_response.json())
    assert redemption.status == "redeemed"
    assert redemption.applied_discount_cents == 2000
    assert redemption.payable_amount_cents == 10000

    query_response = get_user_coupon(api_client, user_id, user_coupon_id)
    assert query_response.status_code == 200, query_response.text
    coupon = UserCouponResponse.model_validate(query_response.json())
    assert coupon.status == "redeemed"
    assert coupon.order_id == order_id
    assert coupon.coupon_id == coupon_id
    assert coupon.applied_discount_cents == 2000
    assert coupon.payable_amount_cents == 10000

# 订单金额是10000，优惠券满10000➖2000
def test_order_at_minimum_spend_receives_full_reduction(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(
        api_client, minimum_order_amount_cents=10000
    )
    claim_response = claim_coupon(api_client, user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    user_coupon_id = claim_response.json()["user_coupon_id"]

    redemption = redeem_coupon(
        api_client, user_id, user_coupon_id, order_amount_cents=10000
    )

    assert redemption.status_code == 200, redemption.text
    assert redemption.json()["applied_discount_cents"] == 2000
    assert redemption.json()["payable_amount_cents"] == 8000

# 同一个用户同一张优惠券只能领取一次，不能重复领取优惠券
def test_user_cannot_claim_the_same_coupon_twice(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(api_client)

    first_claim = claim_coupon(api_client, user_id, coupon_id)
    second_claim = claim_coupon(api_client, user_id, coupon_id)

    assert first_claim.status_code == 201, first_claim.text
    assert second_claim.status_code == 409, second_claim.text

# 优惠券数量为1，那么只能领取一次
def test_coupon_cannot_be_claimed_after_stock_runs_out(api_client):
    coupon_id = create_coupon(api_client, total_quantity=1)
    first_user = f"test-user-{uuid4()}"
    second_user = f"test-user-{uuid4()}"

    first_claim = claim_coupon(api_client, first_user, coupon_id)
    second_claim = claim_coupon(api_client, second_user, coupon_id)

    assert first_claim.status_code == 201, first_claim.text
    assert second_claim.status_code == 409, second_claim.text

# 先mock订单金额小于优惠券最低使用金额，使用失败；再mock订单金额大于等于优惠券最低使用金额，使用成功
def test_failed_redemption_keeps_coupon_claimed(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(
        api_client, minimum_order_amount_cents=10000
    )
    claim_response = claim_coupon(api_client, user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    user_coupon_id = claim_response.json()["user_coupon_id"]

    failed_redemption = redeem_coupon(
        api_client, user_id, user_coupon_id, order_amount_cents=9999
    )
    state_after_failure = get_user_coupon(
        api_client, user_id, user_coupon_id
    )
    successful_redemption = redeem_coupon(
        api_client, user_id, user_coupon_id, order_amount_cents=10000
    )

    assert failed_redemption.status_code == 409, failed_redemption.text
    assert state_after_failure.status_code == 200, state_after_failure.text
    assert state_after_failure.json()["status"] == "claimed"
    assert successful_redemption.status_code == 200, successful_redemption.text
    assert successful_redemption.json()["applied_discount_cents"] == 2000
    assert successful_redemption.json()["payable_amount_cents"] == 8000

# 优惠券是满1000-2000，订单金额是1000，实付0元
def test_discount_cannot_exceed_order_amount(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(
        api_client,
        minimum_order_amount_cents=1000,
        discount_amount_cents=2000,
    )
    claim_response = claim_coupon(api_client, user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    user_coupon_id = claim_response.json()["user_coupon_id"]

    redemption = redeem_coupon(
        api_client, user_id, user_coupon_id, order_amount_cents=1000
    )

    assert redemption.status_code == 200, redemption.text
    assert redemption.json()["applied_discount_cents"] == 1000
    assert redemption.json()["payable_amount_cents"] == 0

# 同一张优惠券无法核销两次
def test_user_cannot_redeem_the_same_coupon_twice(api_client):
    user_id = f"test-user-{uuid4()}"
    coupon_id = create_coupon(api_client)
    claim_response = claim_coupon(api_client, user_id, coupon_id)
    assert claim_response.status_code == 201, claim_response.text
    user_coupon_id = claim_response.json()["user_coupon_id"]

    first_redemption = redeem_coupon(api_client, user_id, user_coupon_id)
    second_redemption = redeem_coupon(api_client, user_id, user_coupon_id)

    assert first_redemption.status_code == 200, first_redemption.text
    assert second_redemption.status_code == 409, second_redemption.text

# 领取不存在的优惠券应返回404

def test_user_cannot_claim_nonexistent_coupon(api_client):
    user_id = f"test-user-{uuid4()}"
    nonexistent_coupon_id = str(uuid4())

    response = claim_coupon(api_client, user_id, nonexistent_coupon_id)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "coupon not found"
