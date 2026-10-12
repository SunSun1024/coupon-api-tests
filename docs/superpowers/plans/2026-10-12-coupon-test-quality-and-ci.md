# Coupon Test Quality and CI Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. This session runs it inline because the user requested all three tasks in sequence.

**Goal:** Cover coupon expiry and concurrent last-stock claims, make failed API exchanges visible in Allure, and verify the changes through GitHub Actions.

**Architecture:** Keep expiry setup deterministic by moving only the coupon expiration timestamp in the test database, then exercise claim and redeem behavior through HTTP. Protect stock with the conditional SQLite update's affected-row count. Record HTTP exchanges in the function-scoped API client and attach them from a pytest report hook only when the test call fails.

**Tech Stack:** Python 3.14, FastAPI, SQLite, pytest, Requests, Allure Pytest, GitHub Actions.

## Global Constraints

- Do not add Python dependencies.
- Keep request and response behavior unchanged except for preventing claims beyond remaining stock.
- Use cents as integer amounts.
- Expiry tests may adjust only the coupons.expires_at field to simulate time passing; claim and redemption requests still use the HTTP API.
- Attach API details only for failed test calls and omit request headers and cookies.
- Push the completed, verified changes to the configured GitHub remote so the workflow can run.

---

### Task 1: Add expiry and concurrent inventory coverage

**Files:**
- Create: tests/test_coupon_expiration.py
- Create: tests/test_coupon_concurrency.py
- Modify: coupon_api/main.py

**Interfaces:**
- Expiry tests create coupons and claims with api_client, update only coupons.expires_at through coupon_api.database.connect, then assert the API response and persisted user-coupon state.
- The concurrency test invokes the real claim route function from separate worker threads against a temporary SQLite database. A barrier synchronizes contenders immediately before the conditional stock update, making the last-stock race deterministic without relying on timing through HTTP transport.
- The claim endpoint returns HTTP 409 with detail "coupon is out of stock" when its conditional inventory decrement updates no row.

- [x] Add a test proving an expired coupon cannot be claimed and its quantity stays unchanged.
- [x] Add a test proving a coupon that expires after claim cannot be redeemed and remains claimed.
- [x] Add a parallel last-stock test with eight distinct users; assert one HTTP 201, seven HTTP 409, and no other status.
- [x] Run the concurrency test against the current implementation and confirm it reproduces overselling before the fix.
- [x] Check the conditional decrement's rowcount before inserting user_coupons; return the existing out-of-stock HTTP 409 when it is zero.
- [ ] Run expiry and concurrency tests again, then run the full regression suite against a live API in GitHub Actions.

### Task 2: Attach failed HTTP exchanges to Allure

**Files:**
- Modify: tests/support/coupon_api_client.py
- Modify: tests/conftest.py
- Create: tests/test_allure_diagnostics.py
- Modify: README.md
- Modify: docs/testing-framework.md

**Interfaces:**
- CouponApiClient stores one JSON-serializable exchange per request with method, URL path, JSON request body, response status and response body; request exceptions are recorded and re-raised.
- A pytest_runtest_makereport hook attaches the current test client's exchanges as JSON only when the test's call phase fails.
- Attachment names include the HTTP method, path, and request number.

- [x] Add tests for captured request/response content and for attaching details only on failed calls.
- [x] Run those tests before implementation to confirm they fail for the missing exchange recorder and report hook.
- [x] Route the four API methods through a shared request method and record response or exception details.
- [x] Add the failure-only Allure attachment hook without attaching headers or cookies.
- [x] Run the report tests and inspect a generated Allure result for attachment metadata.
- [x] Document where failed-call attachments appear in the Allure test detail view.

### Task 3: Verify and publish the workflow run

**Files:**
- Verify: .github/workflows/coupon-api-tests.yml
- Publish: all changes from Tasks 1 and 2 plus this plan.

**Interfaces:**
- A push to the current branch triggers the existing Coupon API tests workflow.
- A successful run uploads coupon-api-allure-report.

- [x] Run focused locally runnable tests (14 passed), collect the regression suite (37 collected, 1 deselected), compile the Python sources, and run git diff --check. HTTP integration tests require a live API and are delegated to the Actions run.
- [ ] Review the staged diff and commit the finished work on the current branch.
- [ ] Push the commit to origin.
- [ ] Inspect the resulting Actions run and confirm its test result and report artifact.
- [ ] If the run fails, fix the cause, re-run locally where possible, push the correction, and inspect the next run.
