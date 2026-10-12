from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException

from coupon_api import database, main


pytestmark = pytest.mark.regression


def test_concurrent_users_cannot_claim_more_than_last_coupon(tmp_path, monkeypatch):
    database_path = tmp_path / "concurrent-claims.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    database.initialize_database()
    coupon_id = str(uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO coupons (
                coupon_id, name, discount_amount_cents, minimum_order_amount_cents,
                total_quantity, remaining_quantity, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (coupon_id, "concurrent-last-stock", 2000, 10000, 1, 1, expires_at),
        )

# Barrier(8) 的意思是：要等 8 个线程都到达这里，才让它们继续。这样它们都会先读到“库存剩 1”，然后再一起尝试更新，测试就能稳定复现并发竞争。
    worker_count = 8
    update_barrier = Barrier(worker_count)

    @contextmanager
    def synchronized_connect():
        with database.connect() as connection:
            yield connection

    class SynchronizedConnection:
        def __init__(self):
            self.connection_context = synchronized_connect()

        def __enter__(self):
            self.connection = self.connection_context.__enter__()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return self.connection_context.__exit__(exc_type, exc_value, traceback)

        def execute(self, sql, params=()):
            if sql.startswith("UPDATE coupons SET remaining_quantity ="):
                update_barrier.wait(timeout=5)
            return self.connection.execute(sql, params)

    monkeypatch.setattr(main, "connect", SynchronizedConnection)

    def claim_coupon(worker_number):
        try:
            response = main.claim_coupon(
                f"concurrent-user-{worker_number}-{uuid4()}", coupon_id
            )
        except HTTPException as error:
            return False, error.status_code, error.detail
        return True, response

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(claim_coupon, range(worker_count)))

    successful_claims = [result for result in results if result[0]]
    conflicts = [result for result in results if not result[0]]
    assert len(successful_claims) == 1, results
    assert len(conflicts) == worker_count - 1, results
    assert {status_code for _, status_code, _ in conflicts} == {409}
    assert {detail for _, _, detail in conflicts} == {"coupon is out of stock"}

    with database.connect() as connection:
        remaining_quantity = connection.execute(
            "SELECT remaining_quantity FROM coupons WHERE coupon_id = ?",
            (coupon_id,),
        ).fetchone()["remaining_quantity"]
        claim_count = connection.execute(
            "SELECT COUNT(*) AS count FROM user_coupons WHERE coupon_id = ?",
            (coupon_id,),
        ).fetchone()["count"]

    assert remaining_quantity == 0
    assert claim_count == 1
