from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, status

from coupon_api.database import connect, initialize_database
from coupon_api.schemas import CouponCreate, RedemptionRequest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Coupon API Demo", lifespan=lifespan)

# 创建优惠券接口
@app.post("/coupons", status_code=status.HTTP_201_CREATED)
def create_coupon(payload: CouponCreate):
    if payload.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="expires_at must be in the future")

    coupon_id = str(uuid4())
    with connect() as connection:
        connection.execute(
            """INSERT INTO coupons (
                coupon_id, name, discount_amount_cents, minimum_order_amount_cents,
                total_quantity, remaining_quantity, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                coupon_id,
                payload.name,
                payload.discount_amount_cents,
                payload.minimum_order_amount_cents,
                payload.total_quantity,
                payload.total_quantity,
                as_utc(payload.expires_at),
            ),
        )
    return {"coupon_id": coupon_id, "name": payload.name}

# 核销优惠券接口
@app.post("/users/{user_id}/coupons/{coupon_id}/claims", status_code=201)
def claim_coupon(user_id: str, coupon_id: str):
    now = utc_now()
    user_coupon_id = str(uuid4())
    with connect() as connection:
        coupon = connection.execute(
            "SELECT * FROM coupons WHERE coupon_id = ?", (coupon_id,)
        ).fetchone()
        if coupon is None:
            raise HTTPException(status_code=404, detail="coupon not found")
        if coupon["expires_at"] <= now:
            raise HTTPException(status_code=409, detail="coupon has expired")
        if coupon["remaining_quantity"] <= 0:
            raise HTTPException(status_code=409, detail="coupon is out of stock")
        try:
            stock_update = connection.execute(
                "UPDATE coupons SET remaining_quantity = remaining_quantity - 1 "
                "WHERE coupon_id = ? AND remaining_quantity > 0",
                (coupon_id,),
            )
            if stock_update.rowcount != 1:
                raise HTTPException(status_code=409, detail="coupon is out of stock")
            connection.execute(
                """INSERT INTO user_coupons
                (user_coupon_id, user_id, coupon_id, status, claimed_at)
                VALUES (?, ?, ?, 'claimed', ?)""",
                (user_coupon_id, user_id, coupon_id, now),
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise HTTPException(status_code=409, detail="user already claimed coupon") from error
            raise
    return {"user_coupon_id": user_coupon_id, "coupon_id": coupon_id, "status": "claimed"}

# 更新已核销的优惠券的状态为redeemed
@app.post("/users/{user_id}/coupons/{user_coupon_id}/redemptions")
def redeem_coupon(user_id: str, user_coupon_id: str, payload: RedemptionRequest):
    now = utc_now()
    with connect() as connection:
        record = connection.execute(
            """SELECT uc.*, c.expires_at, c.minimum_order_amount_cents,
            c.discount_amount_cents
            FROM user_coupons AS uc JOIN coupons AS c ON c.coupon_id = uc.coupon_id
            WHERE uc.user_coupon_id = ? AND uc.user_id = ?""",
            (user_coupon_id, user_id),
        ).fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="claimed coupon not found")
        if record["status"] != "claimed":
            raise HTTPException(status_code=409, detail="coupon is already redeemed")
        if record["expires_at"] <= now:
            raise HTTPException(status_code=409, detail="coupon has expired")
        if payload.order_amount_cents < record["minimum_order_amount_cents"]:
            raise HTTPException(status_code=409, detail="order amount is below coupon minimum")
        applied_discount_cents = min(
            record["discount_amount_cents"], payload.order_amount_cents
        )
        payable_amount_cents = payload.order_amount_cents - applied_discount_cents
        connection.execute(
            """UPDATE user_coupons SET status = 'redeemed', order_id = ?, redeemed_at = ?,
            applied_discount_cents = ?, payable_amount_cents = ?
            WHERE user_coupon_id = ? AND user_id = ? AND status = 'claimed'""",
            (
                payload.order_id,
                now,
                applied_discount_cents,
                payable_amount_cents,
                user_coupon_id,
                user_id,
            ),
        )
    return {
        "user_coupon_id": user_coupon_id,
        "order_id": payload.order_id,
        "status": "redeemed",
        "applied_discount_cents": applied_discount_cents,
        "payable_amount_cents": payable_amount_cents,
    }

# 查询用户的优惠券
@app.get("/users/{user_id}/coupons/{user_coupon_id}")
def get_user_coupon(user_id: str, user_coupon_id: str):
    with connect() as connection:
        record = connection.execute(
            """SELECT user_coupon_id, user_id, coupon_id, status, claimed_at,
            order_id, redeemed_at, applied_discount_cents, payable_amount_cents
            FROM user_coupons
            WHERE user_coupon_id = ? AND user_id = ?""",
            (user_coupon_id, user_id),
        ).fetchone()
    if record is None:
        raise HTTPException(status_code=404, detail="claimed coupon not found")
    return dict(record)
