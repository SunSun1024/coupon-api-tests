import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_testgen.llm_client import JSONParseError, LLMResult
from ai_testgen.models import TestCaseBatch as BatchModel, TokenUsage
from ai_testgen.pipeline import run_pipeline
from ai_testgen.report import write_summary_report
from ai_testgen.generator import GenerationRequest
from ai_testgen.planner import ExecutionPlanDraft
from tests.support.ai_case_examples import contract, generate_source, mapping


class TwoStageFakeClient:
    """Return a business suite first, then a restricted execution plan."""

    settings = SimpleNamespace(model_name="mock-model")

    def __init__(self, source, *, unmapped=False):
        self.batch = BatchModel.model_validate({
            "suite_title": source["test_suite"]["suite_title"],
            "test_cases": source["test_suite"]["test_cases"],
            "clarification_questions": [],
        })
        self.mapping = mapping(source)
        self.unmapped = unmapped
        self.calls = []

    def generate_structured(self, *, messages, response_model):
        self.calls.append(response_model.__name__)
        if response_model is BatchModel:
            parsed = self.batch
        else:
            request = json.loads(messages[1]["content"])
            source_run_id = request["checked_source_artifact"]["metadata"]["run_id"]
            if self.unmapped:
                parsed = ExecutionPlanDraft.model_validate({
                    "cases": [],
                    "unmapped_cases": [{
                        "source_case_id": self.batch.test_cases[0].case_id,
                        "reason": "The operation cannot be represented by the local executor.",
                    }],
                })
            else:
                plan = dict(self.mapping)
                plan["source_run_id"] = source_run_id
                parsed = ExecutionPlanDraft.model_validate({"cases": plan["cases"], "unmapped_cases": []})
        raw = parsed.model_dump_json()
        return LLMResult(raw, parsed, TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
                         response_model.__name__)


def _request():
    return GenerationRequest(
        "同一订单最多成功核销一张券；第二张返回409，第一张保持redeemed，第二张保持claimed。",
        api_document=contract(),
        requirement_version="pipeline-test-v1",
    )


def _settings():
    return SimpleNamespace(model_name="mock-model", max_test_cases=1, max_cases_per_request=1)


def test_mocked_two_stage_pipeline_executes_real_coupon_api_and_writes_reports(tmp_path):
    example_source = generate_source(tmp_path / "fixture")
    fake_client = TwoStageFakeClient(example_source)

    result = run_pipeline(
        _request(), _settings(), tmp_path / "artifacts", llm_client=fake_client,
    )

    assert fake_client.calls == ["TestCaseBatch", "ExecutionPlanDraft"]
    assert result.status == "passed"
    assert result.exit_code == 0
    assert result.total_usage["total_tokens"] >= 36
    assert result.cases[0]["status"] == "passed"
    assert result.artifacts["planner_raw"]
    assert result.summary_html.is_file()
    assert result.summary_json.is_file()
    assert result.detail_report.is_file()
    assert "http://" not in result.summary_html.read_text(encoding="utf-8")


def test_unmappable_plan_is_a_reported_failure_without_execution_client(tmp_path):
    example_source = generate_source(tmp_path / "fixture")
    fake_client = TwoStageFakeClient(example_source, unmapped=True)

    result = run_pipeline(
        _request(), _settings(), tmp_path / "artifacts", llm_client=fake_client,
    )

    assert result.exit_code != 0
    assert result.cases[0]["status"] == "plan_invalid"
    case_result = json.loads(result.cases[0]["result_path"].read_text(encoding="utf-8"))
    assert case_result["steps"] == []
    assert case_result["automated_permit_id"] is None
    assert "executor" in case_result["reason"]


def test_invalid_plan_does_not_request_api_fixture(tmp_path):
    from tests.test_ai_testgen_generated_cases import test_generated_case

    fixture_requests = []
    results_dir = tmp_path / "results"

    class FakeRequest:
        config = SimpleNamespace(getoption=lambda option: {
            "--ai-results": str(results_dir),
            "--ai-automated-execution": None,
        }[option])
        node = SimpleNamespace()

        @staticmethod
        def getfixturevalue(name):
            fixture_requests.append(name)
            raise AssertionError(f"API fixture must not be requested: {name}")

    with pytest.raises(pytest.fail.Exception, match="status=plan_invalid"):
        test_generated_case({
            "source_case_id": "coupon-case-invalid-plan",
            "status": "plan_invalid",
            "code": "operation_not_allowed",
            "reason": "The generated operation is outside the local allowlist.",
        }, FakeRequest())

    assert fixture_requests == []
    saved_result = json.loads(
        (results_dir / "coupon-case-invalid-plan.json").read_text(encoding="utf-8")
    )
    assert saved_result["status"] == "plan_invalid"
    assert saved_result["steps"] == []


def test_relative_output_paths_are_absolute_for_pytest_subprocess(tmp_path, monkeypatch):
    example_source = generate_source(tmp_path / "fixture")
    fake_client = TwoStageFakeClient(example_source)
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_pytest_runner(arguments, environment):
        captured["arguments"] = arguments
        for option in ("--ai-pipeline-cases=", "--ai-openapi=", "--ai-results=",
                       "--html=", "--junitxml=", "--alluredir="):
            value = next(argument[len(option):] for argument in arguments
                         if argument.startswith(option))
            artifact_path = Path(value)
            assert artifact_path.is_absolute()
            if option != "--alluredir=":
                assert artifact_path.exists()
        return 0

    result = run_pipeline(
        _request(), _settings(), "relative-artifacts", llm_client=fake_client,
        pytest_runner=fake_pytest_runner,
    )

    assert Path(result.artifacts["run_directory"]).is_absolute()
    assert Path(result.artifacts["case_manifest"]).is_absolute()


def test_invalid_second_stage_json_preserves_both_model_stage_artifacts(tmp_path):
    example_source = generate_source(tmp_path / "fixture")
    fake_client = TwoStageFakeClient(example_source)
    original = fake_client.generate_structured

    def fail_planning(*, messages, response_model):
        if response_model is not BatchModel:
            raise JSONParseError(
                "response is not valid JSON",
                raw_text="{invalid plan",
                usage=TokenUsage(prompt_tokens=13, completion_tokens=5, total_tokens=18),
            )
        return original(messages=messages, response_model=response_model)

    fake_client.generate_structured = fail_planning
    result = run_pipeline(_request(), _settings(), tmp_path / "artifacts", llm_client=fake_client)

    assert result.status == "failed"
    assert result.cases[0]["status"] == "plan_invalid"
    generation_raw = json.loads(Path(result.artifacts["generation_raw"]).read_text(encoding="utf-8"))
    planning_raw = json.loads(Path(result.artifacts["planner_raw"]).read_text(encoding="utf-8"))
    assert generation_raw["metadata"]["token_usage"]["total_tokens"] >= 18
    assert planning_raw["raw_output"] == "{invalid plan"
    assert planning_raw["error"]["code"] == "json_parse_failed"
    assert result.total_usage["total_tokens"] >= 36


def test_generation_validation_failure_keeps_raw_output_and_summary(tmp_path):
    example_source = generate_source(tmp_path / "fixture")
    fake_client = TwoStageFakeClient(example_source)
    invalid_batch = fake_client.batch.model_dump()
    invalid_batch["test_cases"][0]["source_refs"] = ["UNKNOWN-SOURCE"]
    fake_client.batch = BatchModel.model_validate(invalid_batch)

    result = run_pipeline(_request(), _settings(), tmp_path / "artifacts", llm_client=fake_client)

    assert result.status == "generation_failed"
    assert result.cases == []
    assert Path(result.artifacts["generation_raw"]).is_file()
    assert result.summary_html.is_file()
    assert result.summary_json.is_file()


def test_summary_report_escapes_model_supplied_case_text(tmp_path):
    result = SimpleNamespace(
        run_id="run-1", status="plan_invalid", model_name="model",
        phases={}, cases=[{"source_case_id": "case1", "title": "<script>alert(1)</script>",
                           "status": "plan_invalid", "reason": "invalid"}],
        total_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        artifacts={}, exit_code=1, detail_report=None,
        to_dict=lambda: {
            "run_id": "run-1", "status": "plan_invalid", "model_name": "model",
            "phases": {}, "cases": [{"source_case_id": "case1",
                                        "title": "<script>alert(1)</script>",
                                        "status": "plan_invalid", "reason": "invalid"}],
            "total_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "artifacts": {},
        },
    )

    json_path, html_path = write_summary_report(result, tmp_path)

    assert json_path.is_file()
    assert "&lt;script&gt;" in html_path.read_text(encoding="utf-8")
    assert "<script>" not in html_path.read_text(encoding="utf-8")
