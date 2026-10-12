import json
from types import SimpleNamespace

import pytest
import requests

from tests import conftest
from tests.support.coupon_api_client import CouponApiClient


pytestmark = pytest.mark.regression


class StubSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


class StubCleanup:
    def __init__(self):
        self.coupon_ids = []

    def add_coupon(self, coupon_id):
        self.coupon_ids.append(coupon_id)


def json_response(status_code, body):
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = "application/json"
    return response


def test_api_client_records_request_and_response():
    payload = {"name": "spring", "discount_amount_cents": 2000}
    body = {"coupon_id": "coupon-1", "name": "spring"}
    session = StubSession(json_response(201, body))
    cleanup = StubCleanup()
    client = CouponApiClient("http://coupon-api.test", session, cleanup)

    response = client.create_coupon(payload)

    assert response.status_code == 201
    assert cleanup.coupon_ids == ["coupon-1"]
    assert client.exchanges == [
        {
            "request": {
                "method": "POST",
                "path": "/coupons",
                "json": payload,
            },
            "response": {"status_code": 201, "body": body},
        }
    ]


def test_api_client_records_connection_errors_before_reraising():
    session = StubSession(error=requests.ConnectionError("service unavailable"))
    client = CouponApiClient("http://coupon-api.test", session, StubCleanup())

    with pytest.raises(requests.ConnectionError):
        client.get_user_coupon("user-1", "claim-1")

    assert client.exchanges[0]["request"] == {
        "method": "GET",
        "path": "/users/user-1/coupons/claim-1",
        "json": None,
    }
    assert client.exchanges[0]["error"] == {
        "type": "ConnectionError",
        "message": "service unavailable",
    }


def test_failed_call_attaches_exchange_as_json_but_passing_call_does_not(monkeypatch):
    exchange = {
        "request": {
            "method": "POST",
            "path": "/coupons",
            "json": {"name": "spring"},
        },
        "response": {"status_code": 422, "body": {"detail": "invalid"}},
    }
    client = SimpleNamespace(exchanges=[exchange])
    item = SimpleNamespace(funcargs={"api_client": client})
    attachments = []

    monkeypatch.setattr(
        conftest.allure,
        "attach",
        lambda body, name, attachment_type: attachments.append(
            (body, name, attachment_type)
        ),
    )

    conftest._attach_failed_api_exchanges(
        item, SimpleNamespace(when="call", failed=True)
    )

    assert len(attachments) == 1
    attached_body, name, attachment_type = attachments[0]
    assert json.loads(attached_body) == exchange
    assert name == "API 1: POST /coupons"
    assert attachment_type == conftest.allure.attachment_type.JSON

    conftest._attach_failed_api_exchanges(
        item, SimpleNamespace(when="call", failed=False)
    )
    conftest._attach_failed_api_exchanges(
        item, SimpleNamespace(when="setup", failed=True)
    )
    assert len(attachments) == 1
