"""Small HTTP client for the coupon API integration tests."""

from uuid import uuid4


class CouponApiClient:
    def __init__(self, base_url, session, cleanup, timeout=5):
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.cleanup = cleanup
        self.timeout = timeout

    def create_coupon(self, payload):
        response = self.session.post(
            f"{self.base_url}/coupons", json=payload, timeout=self.timeout
        )
        if response.status_code == 201:
            self.cleanup.add_coupon(response.json()["coupon_id"])
        return response

    def claim_coupon(self, user_id, coupon_id):
        response = self.session.post(
            f"{self.base_url}/users/{user_id}/coupons/{coupon_id}/claims",
            timeout=self.timeout,
        )
        if response.status_code == 201:
            self.cleanup.add_user_coupon(response.json()["user_coupon_id"])
        return response

    def redeem_coupon(
        self, user_id, user_coupon_id, order_amount_cents=12000, order_id=None
    ):
        return self.session.post(
            f"{self.base_url}/users/{user_id}/coupons/{user_coupon_id}/redemptions",
            json={
                "order_id": order_id or f"test-order-{uuid4()}",
                "order_amount_cents": order_amount_cents,
            },
            timeout=self.timeout,
        )

    def get_user_coupon(self, user_id, user_coupon_id):
        return self.session.get(
            f"{self.base_url}/users/{user_id}/coupons/{user_coupon_id}",
            timeout=self.timeout,
        )
