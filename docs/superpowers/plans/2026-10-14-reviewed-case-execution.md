# Reviewed Case Execution Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan inline, with module-level verification checkpoints. User has approved the design and implementation; no additional execution-choice approval is needed.

**Goal:** Connect human-reviewed generated cases to the existing Pytest API framework with deterministic adaptation, restricted execution and traceable results.

**Architecture:** Separate business TestCase from an explicit operation mapping. Bind a human review receipt to source, mapping and trusted OpenAPI digests; repeat verification before execution. Inject the existing CouponApiClient and reuse function fixtures and report attachments.

**Tech Stack:** Python 3.14, Pydantic v2, Requests, Pytest, bounded offline OpenAPI 3.1 Schema validation, SQLite deduplication registry, Allure/pytest-html. No additional dependency was needed for this phase.

## Global Constraints

- Only http_request, assert_status and assert_json operations; no eval, exec, model-generated code or dynamic imports.
- Only four registered Coupon API operations and the local-coupon environment at http://127.0.0.1:8000 initially.
- Validate review, contract, parameters and target before requests; disable redirects and environment proxies.
- External Schema references are rejected; typed variable references only.
- Preserve pre-existing workspace changes; work in the current directory, with no automatic commit or push.

### Task 1: Contracts and review

Files: ai_testexec/models.py, io.py, review.py, contracts.py, policy.py; tests/test_ai_testexec_adapter.py and tests/support/ai_case_examples.py.

Interfaces: parse SourceArtifact/MappingBundle; Contract validates registered method/path and request/response Schema; create_review(source, mapping, openapi, reviewer) returns content-bound receipt.

- [x] Write tests for valid inputs, missing fields, unknown paths, illegal methods, wrong parameter types, unknown operations and unreviewed input.
- [x] Run module tests and reproduce confirmed review issues with failing regression tests before fixing them.
- [x] Implement strict discriminated operation/value models, local ref resolution and digest-bound receipt. Return field-level errors without any HTTP call.
- [x] Run adapter and contract tests and confirm all module checks pass.

### Task 2: Adapter and deduplication

Files: ai_testexec/adapter.py, registry.py; tests/test_ai_testexec_adapter.py.

Interfaces: CaseAdapter.adapt(...) returns ConversionResult with ready bundle and per-source outcomes; verify_bundle(...) repeats conversion and compares derived output; CaseRegistry tracks canonical executable fingerprints.

- [x] Add tests for missing mappings, bad variable dependencies, duplicate generated cases, idempotent adaptation and changed review/source/contract/bundle.
- [x] Implement deterministic fingerprints and review verification, then rerun adapter tests.

### Task 3: Fixed executor and shared client

Files: ai_testexec/executor.py; modify tests/support/coupon_api_client.py; tests/test_ai_testexec_executor.py.

Interfaces: CaseExecutor.execute(bundle, case_id, review, client) returns ExecutionResult; CouponApiClient.request_operation(...) validates local target and uses existing exchanges/cleanup.

- [x] Write tests for successful execution, wrong assertions, timeouts, invalid runtime data, forbidden targets, proxies, redirects and unreviewed/tampered cases with zero requests.
- [x] Implement three fixed handlers, per-case fixture context and step-level results. Reuse response Pydantic models through injection.
- [x] Run executor and existing Allure/client tests.

### Task 4: Pytest integration

Files: ai_testexec/pytest_plugin.py; modify tests/conftest.py; add tests/test_reviewed_ai_cases.py and tests/test_ai_testexec_pytest.py.

Interfaces: --ai-execution/--ai-review/--ai-openapi/--ai-results; reviewed_case is parameterized before network fixtures run. Result JSON and Allure attachments share source/execution identifiers.

- [x] Add subprocess tests for collection blocking, repeated runs, failure diagnostics, teardown errors and results/attachments.
- [x] Integrate collection and readiness validation, function fixtures, parameterized entry and report hooks.
- [x] Verify this command works without real model credentials: `.venv/bin/python -m pytest -q tests/test_ai_testexec_pytest.py`.

### Task 5: CLI and example

Files: ai_testexec/cli.py, __main__.py, examples/ai_testexec/source.json, mapping.json; tests/test_ai_testexec_cli.py; README.md. Existing requirements are sufficient.

Interfaces: `python -m ai_testexec preview|review|adapt` with source, mapping and OpenAPI paths; review creates an explicit separate receipt; adapt always writes a conversion report and writes execution data only for ready cases.

- [x] Test CLI review, conversion success/failure, warning acknowledgement, dedupe registry and help without credentials.
- [x] Provide the same-order non-stacking example as an unreviewed draft, with exact tutorial commands and JSON-format explanation.

### Task 6: End-to-end verification and review

Files: tests/test_ai_testexec_pytest.py and tests/support/asgi_coupon_session.py; update design/plan verification evidence.

- [x] Exercise Mock generation through existing generator, Schema validation, simulated explicit review receipt, conversion, Pytest execution, report artifacts and cleanup using an isolated local Coupon API database.
- [x] Run all AI generation/execution module tests and the entire local regression suite through real FastAPI ASGI routes and isolated SQLite.
- [x] Request code review of security boundaries and traceability, fix verified findings, and rerun affected checks.
- [x] Audit each of the ten explicit scenarios and ten requirements against current implementation and test evidence before marking goal complete.

## Verification evidence — 2026-10-15

- Full regression: **166 passed, 1 skipped, 1 deselected** in 10.91 seconds. Includes original AI generation Mock tests, new execution tests and existing coupon/framework tests. The skipped entry has no `--ai-execution`; the deselected external smoke example is outside regression.
- Integration transport: Requests-compatible session invokes real FastAPI ASGI routes and SQLite using a temporary database. The environment forbids local TCP binding; Uvicorn/TCP connectivity has **not** been verified here. README provides commands for that local check.
- End-to-end subprocess tests passed with two consecutive executions, empty database after cleanup, unique runtime IDs, HTML/Allure attachments, assertion failures, collection refusal and cleanup failure classification.
- `python -m ai_testexec --help` and the documented example `preview` passed without model credentials.
- `compileall` and `git diff --check` passed. AST inspection found no eval/exec/compile/__import__ calls in ai_testexec.
- Review fixes reproduced red→green: invalid timezone offset, required request body, unsupported/null query serialization, stale executable output, blocked-input traceability, and ref-shaped JSON inside literal values. Final read-only review reports no remaining Critical/Important findings within reviewed scope.
- Local artifacts: `artifacts/ai_testexec/verification/regression.html`, `regression.xml`, `allure-results/`, and `preview.json`.
- Tested versions: Python 3.14.3, Pydantic 2.14.0, Pytest 9.1.1, Requests 2.34.2, FastAPI 0.143.0.

## Requirement audit

| Requirement | Implementation / evidence |
| --- | --- |
| Independent Case Adapter | ai_testexec/adapter.py; strict source/mapping/derived models |
| No model-generated Python execution | Fixed operation handlers; code review and AST inspection |
| Predefined operations only | Discriminated union: http_request/assert_status/assert_json |
| Method, path, parameters and data validation | Contract + policy before conversion; real resolved values checked again before request |
| Authorized target only | Exact local target, registered four operations, no redirects/proxies; target checked before api_ready |
| Business vs executable distinction | Separate models; absent mapping or unknown expectation → needs_manual_mapping |
| Batch Pytest integration | pytest_generate_tests parameterizes source_case_id; existing function fixtures |
| Reuse diagnostics and reports | Shared client exchanges, Pydantic responses, Allure hooks and pytest-html |
| Preserve provenance | Embedded source/mapping; three review digests; generation/case/execution IDs; observed-input trace marked unverified when refused |
| Distinguish failures | Conversion outcomes; review refusal; execution_error; assertion_failed with expected/actual/request_id |

## Required test scenario audit

| Scenario | Automated coverage |
| --- | --- |
| Normal generation and execution | test_normal_mock_generation_schema_review_and_conversion; test_parameterized_execution_reports_and_cleans_real_api_data_twice |
| Missing required field | test_invalid_mapping_is_reported_before_conversion: operations removed; CLI fixtures removed |
| Nonexistent API path | Same parameterized adapter test: /nonexistent |
| Illegal HTTP method | Same parameterized adapter test: TRACE |
| Parameter violates Schema | Same parameterized adapter test: total_quantity as string; contracts boundary tests |
| Unsupported operation | Same parameterized adapter test: python operation |
| Assertion mismatch | test_wrong_json_assertion_records_actual_expected_and_step; subprocess failure attachments |
| Interface timeout | test_timeout_is_execution_error_and_write_not_retried |
| Repeated generated case | test_duplicate_generated_plan_with_different_case_id_is_identified; persistent registry CLI test |
| Unreviewed execution | test_unreviewed_execution_makes_zero_requests; collection rejection before API/DB fixture |

The workspace is preserved in place, including pre-existing changes. No commit, push, merge or deployment was performed.
