import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DATABASE_PATH = Path(
    os.environ.get("COUPON_DB_PATH", Path(__file__).resolve().parent.parent / "coupon.db")
)


@contextmanager
def connect():
    """Open one SQLite connection and commit or roll back as a unit."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    """创建优惠券表和用户优惠券表（表不存在时才创建）。"""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS coupons (
                coupon_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                discount_amount_cents INTEGER NOT NULL CHECK (discount_amount_cents > 0),
                minimum_order_amount_cents INTEGER NOT NULL CHECK (minimum_order_amount_cents >= 0),
                total_quantity INTEGER NOT NULL CHECK (total_quantity > 0),
                remaining_quantity INTEGER NOT NULL CHECK (remaining_quantity >= 0),
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_coupons (
                user_coupon_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                coupon_id TEXT NOT NULL REFERENCES coupons(coupon_id),
                status TEXT NOT NULL CHECK (status IN ('claimed', 'redeemed')),
                claimed_at TEXT NOT NULL,
                order_id TEXT,
                redeemed_at TEXT,
                UNIQUE (user_id, coupon_id)
            );
            """
        )

        # Keep existing local databases usable when redemption fields are added.
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(user_coupons)")
        }
        if "applied_discount_cents" not in columns:
            connection.execute(
                "ALTER TABLE user_coupons ADD COLUMN applied_discount_cents INTEGER"
            )
        if "payable_amount_cents" not in columns:
            connection.execute(
                "ALTER TABLE user_coupons ADD COLUMN payable_amount_cents INTEGER"
            )
