import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest


pytestmark = pytest.mark.regression

DATA_FILE = Path(__file__).parent / "data" / "coupon_create_invalid_cases.json"
with DATA_FILE.open(encoding="utf-8") as file:
    INVALID_CREATE_CASES = json.load(file)


@pytest.mark.parametrize(
    "case",
    INVALID_CREATE_CASES,
    ids=[case["case_id"] for case in INVALID_CREATE_CASES],
)
def test_create_coupon_rejects_invalid_payloads(api_client, case):
    """每组无效请求都应返回 422，并指出出错字段。"""
    payload = {
        "name": f"invalid-case-{uuid4()}",
        "discount_amount_cents": 2000,
        "minimum_order_amount_cents": 10000,
        "total_quantity": 5,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }
    payload.update(case.get("payload_overrides", {}))
    for field in case.get("remove_fields", []):
        payload.pop(field)

    response = api_client.create_coupon(payload)

    assert response.status_code == 422, (
        f"case={case['case_id']}, response={response.text}"
    )
    errors = response.json().get("detail")
    assert isinstance(errors, list), (
        f"case={case['case_id']}, expected Pydantic field errors, got: {errors}"
    )
    assert any(
        case["expected_error_field"] in error.get("loc", []) for error in errors
    ), (
        f"case={case['case_id']}, expected error on "
        f"{case['expected_error_field']}, got: {errors}"
    )
