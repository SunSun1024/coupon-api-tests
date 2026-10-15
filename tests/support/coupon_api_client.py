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

    def request_operation(self, operation_id, method, path, *, json_body=None, query=None):
        """受限执行入口：复用日志/清理；目标和登记表在实际请求前再次检查。"""
        from ai_testexec.contracts import OPERATIONS, serialize_query
        from ai_testexec.errors import GateError
        from ai_testexec.policy import validate_target, validate_concrete_path, path_value

        validate_target(self.base_url)
        registered = OPERATIONS.get(operation_id)
        if registered is None or registered[0] != method:
            raise GateError("target_forbidden", "接口操作或Method不在登记表")
        validate_concrete_path(registered[1], path)
        if getattr(self.session, "proxies", {}):
            raise GateError("target_forbidden", "执行会话不允许代理配置")
        self.session.trust_env = False
        kwargs = {"allow_redirects": False}
        if json_body is not None:
            kwargs["json"] = json_body
        if query:
            kwargs["params"] = serialize_query(query)
        response = self._request(method, path, **kwargs)
        if operation_id == "createCoupon" and response.status_code == 201:
            self.cleanup.add_coupon(path_value(response.json()["coupon_id"]))
        elif operation_id == "claimCoupon" and response.status_code == 201:
            self.cleanup.add_user_coupon(path_value(response.json()["user_coupon_id"]))
        return response

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}"
        exchange = {
            "request": {
                "method": method.upper(),
                "path": urlsplit(url).path,
                "json": kwargs.get("json"),
            }
        }
        if kwargs.get("params"):
            exchange["request"]["query"] = kwargs["params"]
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
