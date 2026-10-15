# One-Command AI Test Pipeline Implementation Plan

> **For agentic workers:** Execute this plan inline in task order. Keep each change small, preserve user workspace changes, and run the targeted offline checks after each task.

**Goal:** Add `python -m ai_testgen run` to generate business cases, ask the model for a restricted execution plan, validate it locally, run it against an isolated local Coupon API, and produce a combined HTML/JSON report.

**Architecture:** Keep `generate` unchanged. Add a second structured-output planner that emits existing `CaseMapping` data plus explicit unmapped-case reasons, a separate machine-validation receipt, and a pipeline runner. The runner persists both model stages, then starts an isolated Pytest subprocess using the existing `ai_testexec` adapter/executor, an in-process ASGI session, and temporary SQLite.

**Tech Stack:** Python 3.14, Pydantic 2, OpenAI-compatible Chat Completions, pytest, pytest-html, Allure, FastAPI, SQLite, Python standard library.

## Global Constraints

- The model returns only the existing restricted Coupon execution DSL; never `eval`, `exec`, dynamic import, SQL, shell, host, port, headers, or proxy values from model output.
- Runtime target remains the repository Coupon API; validate every operation against the trusted local OpenAPI and operation registry.
- Save generated artifacts below `artifacts/ai_testgen/`; never include API keys in prompt/output archives or logs.
- Keep `ai_testgen generate` and the explicit human review/preview/adapt commands compatible.
- Use a new temporary SQLite database for each `run`; do not access the developer database.
- Preserve unrelated dirty files; do not commit or push.

---

## File map

- `ai_testgen/planner.py`: `ExecutionPlanDraft`, planner prompt version, and second-stage structured LLM call.
- `ai_testgen/pipeline.py`: stage orchestration, per-case statuses, temporary database setup, Pytest invocation, and exit status.
- `ai_testgen/report.py`: run manifest and escaped summary HTML/JSON, including failures before Pytest.
- `ai_testgen/cli.py`: new `run` subcommand and clear output paths.
- `ai_testexec/models.py`: `AutomatedPermit`, distinct from human `ReviewReceipt`, plus explicit authorization fields on executable and result models.
- `ai_testexec/review.py` and `ai_testexec/adapter.py`: deterministic automatic permit validation and existing operation/schema validation.
- `ai_testexec/pytest_plugin.py`: optional automated bundle/permit input; retain human CLI behavior.
- `ai_testexec/executor.py`: verify either human receipt or automated permit via explicit type dispatch.
- `tests/conftest.py`: auto-pipeline-only in-process ASGI fixture mode.
- `tests/test_ai_testgen_pipeline.py`: parameterize every source case, including invalid plans, and execute valid ones with existing `CaseExecutor`.
- `README.md`: replace the step requiring users to hand-edit mappings with the single-command workflow; keep advanced manual workflow documented separately.
- `docs/superpowers/specs/2026-10-15-one-command-ai-test-pipeline-design.md`: link to implementation plan and update status after work.

## Task 1: Define and validate the planner response

**Files:** Create `tests/test_ai_testgen_planner.py`, create `ai_testgen/planner.py`.

**Interfaces:**

- `ExecutionPlanDraft`: strict Pydantic model containing mapped cases and an explicit `unmapped_cases` list.
- `generate_execution_plan(client, source, requirements, business_rules, constraints, openapi) -> PlanResult`.
- The planner prompt receives only this run's validated business case/source and contract; it must return exactly one mapping for each `case_id`, use only allowlisted operations, and emit no executable code.

- [x] Write tests proving the restricted schema, explicit unmapped reasons, duplicate/missing/unknown case handling, raw response, and usage retention.
- [x] Run the target first and confirm the missing planner import fails.
- [x] Implement the strict model, `PLAN_PROMPT_VERSION`, request builder, and prompt that forbids model-controlled destinations or arbitrary code.
- [x] Run the targeted planner tests and confirm they pass.

## Task 2: Add a machine permit without weakening manual approval

**Files:** Modify `ai_testexec/models.py`, `ai_testexec/review.py`, `ai_testexec/adapter.py`, `ai_testexec/executor.py`; create `tests/test_ai_testexec_automated.py`.

**Interfaces:**

- `AutomatedPermit`: explicit `kind="automated"`, permit id, source/mapping/OpenAPI SHA-256 digests, and validator version; it does not carry a human reviewer.
- `ExecutableBundle`: exactly one of `review_id` or `automated_permit_id` is set. `ExecutionResult` reports the matching authorization field; a machine permit is never stored in `review_id`.
- `create_automated_bundle(source, mapping, openapi) -> (ExecutableBundle | None, list[ConversionOutcome], AutomatedPermit | None)`.
- `verify_automated_bundle(bundle, permit, openapi) -> ExecutableBundle` reruns the contract checks and compares all derived digests.
- `verify_bundle` and `CaseExecutor.execute` explicitly dispatch to either existing human `ReviewReceipt` or `AutomatedPermit` verification.

- [x] Write tests for valid permits, changed source/mapping/OpenAPI, unsupported operations, missing mappings, and the unchanged human reviewer requirement.
- [x] Run the target first and confirm the missing automated adapter fails.
- [x] Add the permit model and reuse local source/OpenAPI/mapping validation; keep `create_review` unchanged.
- [x] Recompute the expected executable subset from the bundle and machine permit before execution.
- [x] Run automated and manual adapter/executor regression tests.

## Task 3: Orchestrate stages and preserve a report on early failure

**Files:** Create `tests/test_ai_testgen_pipeline.py`, `ai_testgen/pipeline.py`, `ai_testgen/report.py`.

**Interfaces:**

- `run_pipeline(request, settings, output_dir, *, llm_client=None) -> PipelineResult`.
- `PipelineResult` stores run id, source artifact, both raw responses, plan outcomes, automated bundle/permit paths, model usage by stage, pytest report paths, and final exit code.
- `write_summary_report(result, directory) -> (Path, Path)` always writes JSON and escaped HTML, including a generation failure with no cases.

- [x] Write an offline pipeline test with a fake client for both structured model calls and token accounting.
- [x] Write an invalid second-stage JSON test and verify both stage artifacts survive with a `plan_invalid` case.
- [x] Run the target first and confirm the missing pipeline import fails.
- [x] Implement per-run artifacts, summaries on early generation failure, and safe CLI output without API keys.
- [x] Run the targeted pipeline tests.

## Task 4: Run each result in isolated in-process Coupon API tests

**Files:** Modify `tests/conftest.py` and `ai_testexec/pytest_plugin.py`; extend `tests/test_ai_testgen_pipeline.py`.

**Interfaces:**

- Add one opt-in pytest option for the automatic pipeline; only that mode selects `ASGICouponSession` and skips the external `/docs` readiness request.
- Parameterize by source case id and its outcome. Invalid or missing plans produce a test failure before `api_client` is requested; ready plans use the existing `CouponApiClient`/`CaseExecutor` and function-scoped cleanup.
- The pipeline sets `COUPON_DB_PATH` to a `TemporaryDirectory` path before `pytest.main`, restores the prior environment in `finally`, and removes the temporary directory after the app/database is closed.

- [x] Test that an invalid plan writes a failed result without requesting the API client, while a valid plan uses the real FastAPI route through ASGI.
- [x] Run the target first to confirm the pipeline implementation was missing.
- [x] Add an opt-in ASGI fixture branch; normal external-service behavior remains the default.
- [x] Parameterize every generated case and request the API fixture only for locally validated plans.
- [x] Run focused Pytest integration and existing reviewed-execution tests.

## Task 5: Expose one CLI command and combined reports

**Files:** Modify `ai_testgen/cli.py`, `ai_testgen/pipeline.py`, `ai_testgen/report.py`; modify `.gitignore` only if a report path falls outside the existing ignored `artifacts/ai_testgen/` tree.

**Interfaces:**

- `python -m ai_testgen run --requirements PATH [--openapi PATH] [--business-rules PATH] [--constraints PATH] [--focus TEXT] [--requirement-version TEXT] [--output-dir DIR]`.
- `run --help` does not load `LLMSettings`; normal run builds one client and reuses it for generation and planning.
- Return 0 only when every generated case has a valid plan, pytest passes, and cleanup succeeds; otherwise write reports and return non-zero.

- [x] Add parser coverage for `run --help`, missing credentials, both-stage Mock invocation, and early failures producing summary reports.
- [x] Implement the command and print compact phase/token/report paths without request bodies or secrets.
- [x] Run targeted CLI/pipeline tests and offline AI generation/execution tests.

## Task 6: Update the user workflow documentation

**Files:** Modify only the AI execution workflow section in `README.md`; update the approved design status/link.

- [x] Add the one-command `run` example as the primary flow; retain hand-authored mapping as an advanced workflow.
- [x] Explain the two model stages, token usage, plan failure status, HTML locations, and isolated local database.
- [x] Keep the existing manual `preview/review/adapt` route under an advanced section.
- [x] Inspect the targeted README diff and final command paths.

## Plan self-review

- Dataset generation and plan generation have separate models, raw outputs, prompts, usages, and failure states.
- Automated validation is separately typed and bound to source, mapping, and OpenAPI digests; human approval semantics remain truthful.
- The exact four existing operation ids, registry, local schema checker, target policy, and cleanup client are reused.
- Tests prove malformed plans fail before an HTTP fixture; reporting still records cases and earlier outputs.
- The temporary database path is passed only to the child Pytest process before it imports `coupon_api.database`; the child exits before its `TemporaryDirectory` is removed.
- `generate` and human-reviewed commands remain compatible.
- This plan does not change the paused AI evaluation-system objective.
