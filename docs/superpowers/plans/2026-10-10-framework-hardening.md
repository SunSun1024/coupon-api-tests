# Coupon API Test Framework Improvements Plan

**Goal:** Improve API test readability, data isolation, report visibility, and continuous integration while preserving existing test behavior and user comments.

**Architecture:** Add a small Requests-based API client; track IDs created through successful API calls and remove only those rows in a function-scoped cleanup fixture; generate Allure results locally and in GitHub Actions, publishing the generated report as a workflow artifact.

## Tasks

1. Encapsulate HTTP requests in `tests/support/coupon_api_client.py` and update coupon integration tests to use a fixture-provided client.
2. Track test-created coupon and user-coupon IDs and clean up only those rows after each test. Add focused cleanup verification with a temporary SQLite database.
3. Add Allure pytest dependency, ignore generated output, document local commands, and add a GitHub Actions workflow that starts the API against a temporary database and uploads the report.
4. Verify syntax, test collection, cleanup behavior, workflow YAML, and report plugin availability where the current environment permits.
