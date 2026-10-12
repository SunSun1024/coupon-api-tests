"""Pydantic contracts for successful Coupon API responses."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, StrictInt, StrictStr


class CouponCreatedResponse(BaseModel):
    coupon_id: StrictStr
    name: StrictStr


class CouponClaimedResponse(BaseModel):
    user_coupon_id: StrictStr
    coupon_id: StrictStr
    status: Literal["claimed"]


class CouponRedemptionResponse(BaseModel):
    user_coupon_id: StrictStr
    order_id: StrictStr
    status: Literal["redeemed"]
    applied_discount_cents: StrictInt
    payable_amount_cents: StrictInt


class UserCouponResponse(BaseModel):
    user_coupon_id: StrictStr
    user_id: StrictStr
    coupon_id: StrictStr
    status: Literal["claimed", "redeemed"]
    claimed_at: datetime
    order_id: StrictStr | None
    redeemed_at: datetime | None
    applied_discount_cents: StrictInt | None
    payable_amount_cents: StrictInt | None
