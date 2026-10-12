"""Small HTTP client for the coupon API integration tests."""

from uuid import uuid4
from urllib.parse import urlsplit

import requests


class CouponApiClient:
    def __init__(self, base_url, session, cleanup, timeout=5):
        self.base_url = base_url.rstrip("/")
        self.session = session
        self.cleanup = cleanup
        self.timeout = timeout
        self.exchanges = []

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        exchange = {
            "request": {
                "method": method.upper(),
                "path": urlsplit(url).path,
                "json": kwargs.get("json"),
            }
        }
        try:
            response = self.session.request(
                method, url, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as error:
            exchange["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
            self.exchanges.append(exchange)
            raise

        try:
            response_body = response.json()
        except ValueError:
            response_body = response.text
        exchange["response"] = {
            "status_code": response.status_code,
            "body": response_body,
        }
        self.exchanges.append(exchange)
        return response

    def create_coupon(self, payload):
        response = self._request("POST", "/coupons", json=payload)
        if response.status_code == 201:
            self.cleanup.add_coupon(response.json()["coupon_id"])
        return response

    def claim_coupon(self, user_id, coupon_id):
        response = self._request(
            "POST", f"/users/{user_id}/coupons/{coupon_id}/claims"
        )
        if response.status_code == 201:
            self.cleanup.add_user_coupon(response.json()["user_coupon_id"])
        return response

    def redeem_coupon(
        self, user_id, user_coupon_id, order_amount_cents=12000, order_id=None
    ):
        return self._request(
            "POST",
            f"/users/{user_id}/coupons/{user_coupon_id}/redemptions",
            json={
                "order_id": order_id or f"test-order-{uuid4()}",
                "order_amount_cents": order_amount_cents,
            },
        )

    def get_user_coupon(self, user_id, user_coupon_id):
        return self._request(
            "GET", f"/users/{user_id}/coupons/{user_coupon_id}"
        )
