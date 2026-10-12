# Coupon API Response Schema Validation Plan

> **For agentic workers:** Execute inline in the current session because this repository has no Git metadata and the user authorized direct implementation.

**Goal:** Validate the structure and field types of successful coupon API responses while preserving existing business assertions.

**Architecture:** Define four Pydantic response models in `tests/support/response_schemas.py` for create, claim, redeem, and query responses. Integration tests will parse successful response JSON through the matching model after checking the HTTP status code. Existing status, amount, and state assertions remain the business checks.

**Tech Stack:** Python 3.14, pytest 9, Pydantic 2, Requests, FastAPI.

## Global Constraints

- Do not change production API behavior or add dependencies.
- Keep failed-response assertions separate from successful-response models.
- Preserve existing test comments and data-driven case IDs.
- Validate each of the four response contracts in existing coupon API flows.
- Report GitHub Actions local feasibility separately from an online GitHub run.

---

### Task 1: Define response contracts and test invalid response shapes

**Files:**
- Create: `tests/support/response_schemas.py`
- Create: `tests/test_response_schemas.py`

**Interfaces:**
- `CouponCreatedResponse`: `coupon_id: StrictStr`, `name: StrictStr`
- `CouponClaimedResponse`: `user_coupon_id: StrictStr`, `coupon_id: StrictStr`, `status: Literal["claimed"]`
- `CouponRedemptionResponse`: `user_coupon_id: StrictStr`, `order_id: StrictStr`, `status: Literal["redeemed"]`, `applied_discount_cents: StrictInt`, `payable_amount_cents: StrictInt`
- `UserCouponResponse`: IDs and status, timestamp fields, and nullable redemption fields matching the query endpoint.

- [x] Write tests that require each response model to reject a missing required field or a value with the wrong primitive type.
- [x] Run `python -m pytest -q tests/test_response_schemas.py`; confirm the tests fail because the contract models are not implemented.
- [x] Add the four Pydantic models with strict string and integer types; use `datetime` for timestamp fields and optional values for fields that are null before redemption.
- [x] Run `python -m pytest -q tests/test_response_schemas.py`; confirm invalid payloads are rejected.

### Task 2: Apply models to successful integration responses

**Files:**
- Modify: `tests/test_coupon_flow.py`
- Modify: `tests/test_coupon_redemption_data.py`
- Modify: `tests/test_coupon_ownership.py`

**Interfaces:**
- Integration tests call `Model.model_validate(response.json())` only after asserting the expected successful HTTP status.

- [x] Validate create, claim, redeem, and query responses in the main successful coupon journey.
- [x] Validate successful data-driven redemption responses and all successful query responses; leave expected error responses checked by their existing status/detail assertions.
- [x] Validate successful setup and owner-query responses in ownership tests without changing the unauthorized request assertions.
- [x] Upgrade `actions/setup-node` to a Node 24-compatible release because GitHub removed Node 20 from Actions runners on 2026-09-23.
- [x] Mark the cleanup unit test as regression so the CI regression command runs this framework test too.
- [x] Run compilation, response-model tests, cleanup tests, and pytest collection.
- [x] Run the local equivalent of the GitHub Actions workflow where the environment permits; document any loopback/network limitation and explain how to inspect a real run in GitHub Actions.
