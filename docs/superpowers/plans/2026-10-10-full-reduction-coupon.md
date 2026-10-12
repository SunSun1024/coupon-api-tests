# Full-Reduction Coupon Implementation Plan

**Goal:** Calculate and persist the actual discount and payable amount when a claimed fixed-amount coupon is redeemed.

**Architecture:** Keep the existing FastAPI and SQLite flow. During redemption, compare the configured coupon discount with the order amount, save the smaller amount as `applied_discount_cents`, and save the resulting `payable_amount_cents`. Return both fields from redemption and query endpoints.

**Tech Stack:** Python, FastAPI, Pydantic, SQLite, Requests, Pytest.

## Global Constraints

- All money values use integer cents.
- Existing claim and redemption routes remain unchanged.
- Existing SQLite databases receive the two new columns without dropping records.
- Each integration test creates its own coupon and user identifiers.

---

### Task 1: Specify the pricing behavior in integration tests

**Files:**
- Modify: `tests/test_coupon_flow.py`

**Interfaces:**
- The redemption response and the query response expose `applied_discount_cents` and `payable_amount_cents`.

- [ ] Assert a 12,000-cent order with a 2,000-cent coupon pays 10,000 cents.
- [ ] Assert an order exactly at the 10,000-cent minimum pays 8,000 cents.
- [ ] Assert an order below the minimum receives 409 and remains claimed.
- [ ] Assert the applied discount cannot exceed the order amount.
- [ ] Assert a second redemption still receives 409.

### Task 2: Persist and return calculated amounts

**Files:**
- Modify: `coupon_api/database.py`
- Modify: `coupon_api/main.py`

- [ ] Add nullable columns for applied discount and payable amount, including a safe migration for existing SQLite files.
- [ ] Calculate `min(configured_discount, order_amount)` only after the minimum-spend check succeeds.
- [ ] Save the calculated amounts with the redeemed status and return them from redemption and query endpoints.

### Task 3: Verify

- [ ] Run `python -m compileall -q coupon_api tests`.
- [ ] Start Uvicorn and run `python -m pytest -q tests/test_coupon_flow.py`.
