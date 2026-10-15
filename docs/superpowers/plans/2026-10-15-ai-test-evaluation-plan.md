# AI Test Evaluation System Implementation Plan

> **For agentic workers:** Implement inline in this workspace, task by task, using test-first development. Preserve all pre-existing workspace changes and do not commit or push without a separate user request. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, explainable evaluator that compares generated API test cases against a human-approved, versioned risk dataset without using the evaluated model as its own judge.

**Architecture:** Add an independent `ai_testeval` package. Keep golden data and approval records separate; wrap the existing generator to record exact requests, raw output, failures, and token use; compute deterministic metrics from immutable snapshots; keep business validity, risk coverage, and semantic duplicate conclusions in human review files. Reuse the existing `TestCaseGenerator`, `validate_suite`, and `CaseAdapter` rather than changing their user-facing behavior.

**Tech Stack:** Python 3.12+, Pydantic v2 strict models, pytest, standard-library `argparse`, `json`, `hashlib`, `pathlib`, and atomic file writes. Tests and examples use fake LLM clients and do not require network access or credentials.

## Global Constraints

- Keep every model strict (`extra="forbid"`, strict scalar types); reject unknown references and duplicate IDs.
- A dataset is formal Golden data only when a separate approval record matches its exact SHA-256; all supplied coupon risk candidates remain an unapproved draft until the user approves them.
- Never send golden risks, expected scenarios, or human review results to the evaluated model.
- Save the original model text and error metadata; do not repair or replace raw output when evaluating it.
- The planned sample/repetition slot remains in denominators after generation failure or interruption.
- No Pytest execution result contributes to business-validity or risk-recall metrics.
- No LLM judge, UI, RAG, automatic execution, database, secret logging, or provider call in tests.
- Do not overwrite existing user changes; add only the evaluation module, targeted docs, and narrowly scoped README/gitignore changes.

---

## File Map

| File | Responsibility |
| --- | --- |
| `ai_testeval/models.py` | Strict schemas for golden datasets, approvals, experiment/run snapshots, attempts, candidates, human reviews, and metric reports. |
| `ai_testeval/dataset.py` | Dataset reference checks, canonical SHA-256, approval creation and verification. |
| `ai_testeval/io.py` | Canonical JSON, digest verification, and atomic immutable snapshot writes. |
| `ai_testeval/recording.py` | Transparent wrapper around `generate_structured`; capture messages, response model, raw text, usage, error type, and timing. |
| `ai_testeval/runner.py` | Run fixed dataset/repetition slots, retain failures, construct manifest and sample records, and persist source snapshots. |
| `ai_testeval/reviews.py` | Blank review template creation and validation of reviewer, evidence, candidate, risk, and duplicate references. |
| `ai_testeval/evaluator.py` | Offline reparsing, candidate indexing, deterministic/semantic duplicate accounting, and all metric numerators/denominators. |
| `ai_testeval/executability.py` | Revalidate source, mapping, review, and OpenAPI using `CaseAdapter`; never send HTTP. |
| `ai_testeval/reports.py` | Provisional/final JSON and Markdown reports, deterministic prompt advice, and comparable-run checks. |
| `ai_testeval/cli.py`, `ai_testeval/__main__.py`, `ai_testeval/__init__.py` | Dataset, run, review, evaluate, and compare commands. |
| `examples/ai_testeval/coupon-golden-draft.json` | Clearly labeled, unapproved coupon risk draft for user review. |
| `examples/ai_testeval/experiment.json` | Reproducible local experiment configuration example. |
| `tests/test_ai_testeval_*.py` | Offline tests for each module and mock end-to-end flow. |
| `docs/ai-test-evaluation.md` | Concise workflow, metric definitions, report interpretation, and limitations. |
| `.gitignore`, `README.md` | Ignore local evaluation artifacts and add a short entry point without rewriting existing guide sections. |

## Task 1: Strict Data Models and Human Approval

**Files:** Create `ai_testeval/__init__.py`, `ai_testeval/models.py`, `ai_testeval/io.py`, `ai_testeval/dataset.py`; create `tests/test_ai_testeval_dataset.py`.

**Interfaces:** `load_dataset(path) -> GoldenDataset`; `dataset_digest(dataset) -> str`; `check_dataset(dataset) -> list[str]`; `create_approval(dataset, reviewer, reviewed_at=None) -> DatasetApproval`; `verify_approval(dataset, approval) -> None`. Validation errors identify a field/reference without embedding requirement text or secrets.

- [ ] Write `test_dataset_rejects_duplicate_ids_and_unknown_risk_refs`: make two samples share a `sample_id` and make a scenario reference `R-UNKNOWN`; assert the Pydantic/reference validation fails with both actionable problems.
- [ ] Write `test_approval_is_bound_to_exact_dataset_content`: approve a valid synthetic dataset, verify it, edit one risk's rationale, and assert verification rejects the stale approval.
- [ ] Run `python -m pytest -q tests/test_ai_testeval_dataset.py`; expected initial result is import/attribute failure because `ai_testeval` is not implemented.
- [ ] Implement frozen, strict Pydantic v2 data structures and deterministic canonical JSON hashing; keep `approval` outside the dataset document.
- [ ] Implement approval creation with a non-empty reviewer and timezone-aware timestamp; never infer a reviewer from the model or current shell user.
- [ ] Re-run `python -m pytest -q tests/test_ai_testeval_dataset.py`; both tests pass, including stale-approval rejection.

## Task 2: Immutable Model Call Recording and Fixed Run Slots

**Files:** Create `ai_testeval/recording.py`, `ai_testeval/runner.py`; create `tests/test_ai_testeval_recording.py`, `tests/test_ai_testeval_runner.py`.

**Interfaces:** `RecordingClient(inner).generate_structured(*, messages, response_model)` has the same call signature as the existing client and appends an `AttemptRecord`; `run_experiment(dataset, approval, config, generator_factory, output_dir) -> RunManifest` writes one `SampleRun` for every planned `(sample_id, repetition)`.

- [ ] Test a successful fake response and assert the attempt stores exact messages, schema name, raw content, usage, timestamps, and no client credentials.
- [ ] Test timeout, parse error, schema error, and truncation; assert each failure has a typed error record and raw availability is `false` when the exception has no raw text.
- [ ] Test two samples with one generation failure; assert both planned slots are persisted and the second sample still runs.
- [ ] Test serialized requests contain requirement inputs but never `risks`, `expected_scenarios`, approval reviewer, or approval rationale.
- [ ] Run `python -m pytest -q tests/test_ai_testeval_recording.py tests/test_ai_testeval_runner.py`; expected initial result is missing-module/import failure.
- [ ] Implement the transparent recording wrapper and runner using the existing `GenerationRequest`/`TestCaseGenerator`; set evaluation-run retries to zero and a JSON output mode, and record this policy in the immutable config snapshot.
- [ ] Atomically save run, attempts, raw outputs, and validated outputs with digests; redact API keys and authorization headers before serialization.
- [ ] Re-run the two test files and confirm failure slots remain in the manifest and all requests are fake/offline.

## Task 3: Evidence-Bound Human Reviews and Metric Calculation

**Files:** Create `ai_testeval/reviews.py`, `ai_testeval/evaluator.py`; create `tests/test_ai_testeval_reviews.py`, `tests/test_ai_testeval_evaluator.py`.

**Interfaces:** `build_review_template(run, dataset) -> HumanReview`; `validate_review(run, dataset, review) -> None`; `evaluate(run, dataset, review=None, execution_evidence=None) -> EvaluationReport` recomputes all automatic evidence from snapshots.

- [ ] Test a blank template sets all candidate validity, risk links, risk audits, and semantic duplicate decisions to `pending` with no reviewer or evidence.
- [ ] Test review rejection for stale run hash, stale candidate hash, unknown candidate/risk IDs, missing evidence for `covered`, and cross-sample semantic duplicate groups.
- [ ] Test a synthetic 10-candidate evaluation with 3 valid, 1 invalid, 6 pending, five risks, two covered, two duplicate surplus items, and two executable candidates; assert valid rate `3/4`, review completion `4/10`, confirmed valid yield `3/10`, risk recall `2/5`, duplicate rate `2/10`, and executable rate `2/10`.
- [ ] Test null denominators, a failed zero-candidate slot, pending risk coverage, malformed JSON, schema-invalid array elements, and incomplete/truncated batches.
- [ ] Run `python -m pytest -q tests/test_ai_testeval_reviews.py tests/test_ai_testeval_evaluator.py`; expected initial result is missing-module/import failure.
- [ ] Implement review binding and the explicit metric cohort rules from the design; parse the saved original content and never count Pytest pass as semantic validity.
- [ ] Re-run both test files and compare all six displayed ratios with the hand calculation above.

## Task 4: Adapter-Based Executability, Reports, Comparison, and CLI

**Files:** Create `ai_testeval/executability.py`, `ai_testeval/reports.py`, `ai_testeval/cli.py`, `ai_testeval/__main__.py`; create `examples/ai_testeval/coupon-golden-draft.json`, `examples/ai_testeval/experiment.json`, `tests/test_ai_testeval_reports.py`, `tests/test_ai_testeval_cli.py`; create `docs/ai-test-evaluation.md`; modify `.gitignore` and append a concise section to `README.md`.

**Interfaces:** `check_executability(source, mapping, openapi, review) -> list[ExecutableEvidence]`; `compare_reports(runs, reviews) -> ComparisonReport` refuses mismatched benchmark content/config; CLI commands are `dataset-check`, `dataset-approve`, `run`, `review-template`, `evaluate`, and `compare`.

- [ ] Test adapter evidence for ready, missing mapping, unreviewed mapping, invalid contract, and malformed source; assert no HTTP client is created or called.
- [ ] Test JSON and Markdown report include each metric numerator/denominator, provisional review status, missed versus pending risks, failure stages, duplicate groups, executability reasons, token usage, and snapshot IDs.
- [ ] Test comparison accepts two model/prompt runs over identical dataset hash, sample slots, budget, protocol, and rubric; reject changed dataset content, missing slots, or incompatible budget.
- [ ] Test `python -m ai_testeval --help` and `dataset-check` run without `LLM_API_KEY`; ensure no draft approval file is supplied or created automatically.
- [ ] Run targeted tests and the full `python -m pytest -q`; the draft dataset remains unapproved and formal risk recall is unavailable until the user approves it.
- [ ] Document the shortest user flow and explicitly explain that `passed_with_warnings`, valid-case review, risk coverage, and Pytest execution answer different questions.

## Acceptance Verification

- Run `python -m pytest -q tests/test_ai_testeval_*.py` and then `python -m pytest -q`.
- Run `python -m ai_testeval --help` and `python -m ai_testeval dataset-check --dataset examples/ai_testeval/coupon-golden-draft.json`; both run offline and the draft reports `approval required` rather than producing formal risk metrics.
- Re-evaluate one saved mock run twice; all metric values, cohorts, and details match (excluding report ID and generated timestamp).
- Confirm with `git status --short` that existing user changes remain present and no API key, committed approval record, or generated artifact was added to source control.

