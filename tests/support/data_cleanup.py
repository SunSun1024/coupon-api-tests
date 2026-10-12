"""Track and remove only database rows created by the current test."""

import sqlite3
from pathlib import Path


class CouponDataCleanup:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.coupon_ids = []
        self.user_coupon_ids = []

    def add_coupon(self, coupon_id: str) -> None:
        self.coupon_ids.append(coupon_id)

    def add_user_coupon(self, user_coupon_id: str) -> None:
        self.user_coupon_ids.append(user_coupon_id)

    def cleanup(self) -> None:
        if not self.database_path.exists():
            return
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executemany(
                "DELETE FROM user_coupons WHERE user_coupon_id = ?",
                [(user_coupon_id,) for user_coupon_id in self.user_coupon_ids],
            )
            connection.executemany(
                "DELETE FROM coupons WHERE coupon_id = ?",
                [(coupon_id,) for coupon_id in self.coupon_ids],
            )
