# Coupon Success Flow Implementation Plan

> **For agentic workers:** Implement this plan inline, one task at a time, with a review checkpoint after each task.

**Goal:** Build a local SQLite-backed coupon API and verify the successful create, claim, redeem, and query flow with Requests and Pytest.

**Architecture:** FastAPI is the local system under test. SQLite persists coupon campaigns and user-owned coupon records. One end-to-end API test creates unique data through the API, then claims, redeems, and queries it; no test depends on another test's data.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, SQLite (`sqlite3`), Uvicorn, Requests, Pytest.

## Global Constraints

- Keep this change to the happy path; add business edge cases in later steps.
- Store monetary values as integer cents.
- Store datetimes as timezone-aware UTC ISO-8601 strings.
- Use parameterized SQLite statements.
- Ignore local database files and keep test data unique per flow.
- Do not add Allure, CI, authentication, or generic client abstractions in this slice.

---

### Task 1: Define and test the API flow

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `tests/test_coupon_flow.py`
- Create: `coupon_api/__init__.py`
- Create: `coupon_api/main.py`

**Interfaces:**
- `POST /coupons` accepts a name, discount cents, minimum-order cents, quantity, and future UTC expiry; returns `201` and a `coupon_id`.
- `POST /users/{user_id}/coupons/{coupon_id}/claims` returns `201`, a `user_coupon_id`, and status `claimed`.
- `POST /users/{user_id}/coupons/{user_coupon_id}/redemptions` accepts an order ID and order amount in cents; returns status `redeemed`.
- `GET /users/{user_id}/coupons/{user_coupon_id}` returns the persisted owner, coupon, status, and redemption order.

- [ ] Add FastAPI and Uvicorn to `requirements.txt`; add `*.db` to `.gitignore`.
- [ ] Create the test first. Generate a unique user and future-expiring coupon; call create, claim, redeem, and query in order. Assert each documented status and the final persisted state.
- [ ] Start the empty FastAPI app and run the test once; confirm it fails because `POST /coupons` is not implemented.
- [ ] Add the SQLite schema, request/response validation, and four handlers to `coupon_api/main.py`.
- [ ] Start the API with `python -m uvicorn coupon_api.main:app --host 127.0.0.1 --port 8000` and run `python -m pytest -q` from another terminal.
- [ ] Confirm one end-to-end test passes, then explain the database, API flow, and test isolation in the walkthrough.
