from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class CouponCreate(BaseModel):
    """定义创建优惠券接口的请求字段及基本校验规则。"""
    name: str = Field(min_length=1, max_length=100)
    discount_amount_cents: int = Field(gt=0)
    minimum_order_amount_cents: int = Field(ge=0)
    total_quantity: int = Field(gt=0)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_must_include_timezone(cls, value: datetime) -> datetime:
        """拒绝不带时区的过期时间，避免无法判断实际时间。"""
        if value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value


class RedemptionRequest(BaseModel):
    """定义核销接口需要的订单编号和订单金额。"""
    order_id: str = Field(min_length=1, max_length=100)
    order_amount_cents: int = Field(ge=0)
