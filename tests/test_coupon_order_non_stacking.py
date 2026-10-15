from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException

from coupon_api import database, main
from coupon_api.schemas import RedemptionRequest


pytestmark = pytest.mark.regression


def _create_claimed_coupon(user_id):
    coupon_id = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO coupons (
                coupon_id, name, discount_amount_cents, minimum_order_amount_cents,
                total_quantity, remaining_quantity, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (coupon_id, "non-stacking-test", 2000, 10000, 1, 1, expires_at),
        )
    claim = main.claim_coupon(user_id, coupon_id)
    return claim["user_coupon_id"]


def _redemption_payload(order_id):
    return RedemptionRequest(order_id=order_id, order_amount_cents=10000)


def test_order_cannot_redeem_two_different_coupons(tmp_path, monkeypatch):
    monkeypatch.setattr(
        database, "DATABASE_PATH", tmp_path / "order-non-stacking.db"
    )
    database.initialize_database()
    user_id = f"non-stacking-user-{uuid4()}"
    first_user_coupon_id = _create_claimed_coupon(user_id)
    second_user_coupon_id = _create_claimed_coupon(user_id)
    order_id = f"non-stacking-order-{uuid4()}"

    first_result = main.redeem_coupon(
        user_id, first_user_coupon_id, _redemption_payload(order_id)
    )

    with pytest.raises(HTTPException) as error:
        main.redeem_coupon(
            user_id, second_user_coupon_id, _redemption_payload(order_id)
        )

    assert first_result["status"] == "redeemed"
    assert error.value.status_code == 409
    assert error.value.detail == "order already has a redeemed coupon"
    with database.connect() as connection:
        states = connection.execute(
            """SELECT status, order_id FROM user_coupons
            WHERE user_coupon_id IN (?, ?) ORDER BY user_coupon_id""",
            (first_user_coupon_id, second_user_coupon_id),
        ).fetchall()
    assert sorted(row["status"] for row in states) == ["claimed", "redeemed"]
    assert [
        row["order_id"] for row in states if row["status"] == "redeemed"
    ] == [order_id]
    assert [
        row["order_id"] for row in states if row["status"] == "claimed"
    ] == [None]


def test_concurrent_redemptions_cannot_stack_on_one_order(tmp_path, monkeypatch):
    monkeypatch.setattr(
        database, "DATABASE_PATH", tmp_path / "concurrent-order.db"
    )
    database.initialize_database()
    user_id = f"concurrent-non-stacking-user-{uuid4()}"
    user_coupon_ids = [
        _create_claimed_coupon(user_id),
        _create_claimed_coupon(user_id),
    ]
    order_id = f"concurrent-non-stacking-order-{uuid4()}"
    start_together = Barrier(2)

    def redeem(user_coupon_id):
        start_together.wait(timeout=5)
        try:
            result = main.redeem_coupon(
                user_id, user_coupon_id, _redemption_payload(order_id)
            )
            return "success", result
        except HTTPException as error:
            return "conflict", error.status_code, error.detail

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(redeem, user_coupon_ids))

    successes = [result for result in results if result[0] == "success"]
    conflicts = [result for result in results if result[0] == "conflict"]
    assert len(successes) == 1, results
    assert len(conflicts) == 1, results
    assert conflicts[0][1:] == (409, "order already has a redeemed coupon")

    with database.connect() as connection:
        states = connection.execute(
            "SELECT status, order_id FROM user_coupons WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    assert sorted(row["status"] for row in states) == ["claimed", "redeemed"]
    assert [
        row["order_id"] for row in states if row["status"] == "redeemed"
    ] == [order_id]
    assert [
        row["order_id"] for row in states if row["status"] == "claimed"
    ] == [None]
