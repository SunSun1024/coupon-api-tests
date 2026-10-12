import sqlite3

import pytest

from tests.support.data_cleanup import CouponDataCleanup


pytestmark = pytest.mark.regression


def test_cleanup_removes_only_resources_registered_by_current_test(tmp_path):
    database_path = tmp_path / "test-coupons.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE coupons (coupon_id TEXT PRIMARY KEY);
            CREATE TABLE user_coupons (
                user_coupon_id TEXT PRIMARY KEY,
                coupon_id TEXT NOT NULL REFERENCES coupons(coupon_id)
            );
            INSERT INTO coupons VALUES ('created-by-test');
            INSERT INTO coupons VALUES ('pre-existing');
            INSERT INTO user_coupons VALUES ('claim-created-by-test', 'created-by-test');
            INSERT INTO user_coupons VALUES ('claim-pre-existing', 'pre-existing');
            """
        )

    cleanup = CouponDataCleanup(database_path)
    cleanup.add_coupon("created-by-test")
    cleanup.add_user_coupon("claim-created-by-test")
    cleanup.cleanup()

    with sqlite3.connect(database_path) as connection:
        remaining_coupons = connection.execute(
            "SELECT coupon_id FROM coupons"
        ).fetchall()
        remaining_claims = connection.execute(
            "SELECT user_coupon_id FROM user_coupons"
        ).fetchall()

    assert remaining_coupons == [("pre-existing",)]
    assert remaining_claims == [("claim-pre-existing",)]
