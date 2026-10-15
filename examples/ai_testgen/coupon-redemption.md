# 优惠券核销需求示例

## 固定业务规则

- 金额一律使用整数分。示例优惠券为“满 100 元减 20 元”：最低消费金额为 `10000` 分，优惠金额为 `2000` 分。
- 创建优惠券时，`expires_at` 由创建方设置为创建时间后 7 天的 UTC 时间。该券在 `now < expires_at` 时有效；当 `now >= expires_at` 时视为过期。此优惠券的到期时间对所有领取用户相同，不因领取时间重新计算。
- 同一个用户对同一个 `coupon_id` 最多成功领取一次。重复领取返回 HTTP `409`，错误体为 `{"detail":"user already claimed coupon"}`。
- 用户只能核销自己领取且仍为 `claimed` 状态、尚未过期的优惠券。核销成功后状态变为 `redeemed`；再次核销返回 HTTP `409`。
- 订单金额达到 `10000` 分才可使用这张示例券。低于最低消费金额时返回 HTTP `409`，优惠券仍为 `claimed`，错误体为 `{"detail":"order amount is below coupon minimum"}`。
- 实际抵扣金额为优惠金额与订单金额的较小值；实付金额等于订单金额减去实际抵扣金额，因此实付金额不小于 0。对本示例的满 100 减 20 券，订单金额刚好为 `10000` 分时，抵扣 `2000` 分，实付 `8000` 分。
- 同一个 `order_id` 最多只能有一张优惠券核销成功。若该订单已经成功使用一张券，再使用另一张券核销同一订单，返回 HTTP `409`，错误体为 `{"detail":"order already has a redeemed coupon"}`；第二张券保持 `claimed`，第一张核销记录不变。此规则即“不与其他优惠券叠加”。
- 核销请求中的 `order_amount_cents` 必须是该订单的实际金额，不得通过拆分请求或重复使用同一个 `order_id` 规避叠加规则。

## 错误响应约定

- 找不到优惠券，或该用户没有领取该用户优惠券：HTTP `404`，响应体为 `{"detail":"coupon not found"}` 或 `{"detail":"claimed coupon not found"}`。
- 优惠券过期、库存不足、重复领取、已核销、订单金额未达门槛、订单已使用其他优惠券：HTTP `409`，响应体为 `{"detail":"<具体原因>"}`。各业务场景使用接口文档列出的具体 `detail` 值。
- 请求字段格式错误或创建优惠券时 `expires_at` 不在未来：HTTP `422`。FastAPI 校验错误使用其标准 `detail` 错误数组；创建接口中由业务逻辑拒绝的过期时间使用 `{"detail":"expires_at must be in the future"}`。

## 可重复的测试数据准备

通过接口创建测试优惠券，不依赖已有数据库记录：

1. 调用 `POST /coupons`，设置 `minimum_order_amount_cents=10000`、`discount_amount_cents=2000`，并将 `expires_at` 设置为当前 UTC 时间加 7 天。
2. 调用 `POST /users/{user_id}/coupons/{coupon_id}/claims`，取得响应中的 `user_coupon_id`。每个测试使用新的 `user_id` 和新建的优惠券，避免其他测试的领取或核销状态影响本次结果。
3. 测试不能叠加时，创建两张具有相同优惠规则、但不同 `coupon_id` 的券，由同一用户分别领取；先用第一张券核销某个 `order_id`，再尝试用第二张券核销同一个 `order_id`，验证第二次返回 `409` 且第二张券仍为 `claimed`。

典型断言数据：订单 `10000` 分达到门槛，核销成功并抵扣 `2000` 分、实付 `8000` 分；订单 `9999` 分未达到门槛，核销返回 `409` 且状态保持 `claimed`。

需求没有明确的状态码、响应字段或业务行为时，生成器应提出 `clarification_questions`，并将相关预期写入 `unverifiable_expectations`，不得自行补充规则。本文列出的规则和响应约定视为本示例的明确依据。
