"""Run generation, restricted planning, local execution, and report writing."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_testexec.adapter import create_automated_bundle
from ai_testexec.cli import DEFAULT_OPENAPI
from ai_testexec.io import read_json, write_json
from ai_testexec.models import ConversionOutcome
from ai_testexec.policy import BASE_URL
from ai_testgen.generator import GenerationRequest, TestCaseGenerator
from ai_testgen.llm_client import (
    JSONParseError,
    LLMGenerationError,
    OpenAICompatibleClient,
    OutputTruncatedError,
    OutputSchemaError,
)
from ai_testgen.planner import (
    PLAN_PROMPT_VERSION,
    PlanValidationError,
    _build_messages,
    generate_execution_plan,
)
from ai_testgen.report import write_summary_report
from ai_testgen.validator import BusinessRuleValidationError


@dataclass
class PipelineResult:
    """In-memory pipeline manifest; paths are saved as strings in pipeline.json."""

    run_id: str
    model_name: str
    status: str = "running"
    exit_code: int = 1
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    cases: list[dict[str, Any]] = field(default_factory=list)
    usage_by_stage: dict[str, dict[str, int]] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    pytest_exit_code: int | None = None
    summary_json: Path | None = None
    summary_html: Path | None = None
    detail_report: Path | None = None

    @property
    def total_usage(self) -> dict[str, int]:
        fields = ("prompt_tokens", "completion_tokens", "total_tokens")
        return {field: sum(self.usage_by_stage.get(stage, {}).get(field, 0)
                           for stage in self.usage_by_stage) for field in fields}

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "model_name": self.model_name,
            "status": self.status,
            "exit_code": self.exit_code,
            "pytest_exit_code": self.pytest_exit_code,
            "phases": self.phases,
            "cases": [
                {key: (str(value) if isinstance(value, Path) else value)
                 for key, value in case.items()}
                for case in self.cases
            ],
            "usage_by_stage": self.usage_by_stage,
            "total_usage": self.total_usage,
            "artifacts": self.artifacts,
        }


def run_pipeline(
    request: GenerationRequest,
    settings: Any,
    output_dir: str | Path,
    *,
    llm_client: Any | None = None,
    pytest_runner: Callable[[list[str], dict[str, str]], int] | None = None,
) -> PipelineResult:
    """Generate, plan, locally validate, execute, and always summarize a run."""
    # Pytest子进程固定从仓库根目录启动，因此把用户提供的相对输出路径
    # 按 CLI 调用时的当前目录解析，避免子进程在另一个目录下找不到清单。
    output_dir = Path(output_dir).expanduser().resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    model_name = getattr(settings, "model_name", None) or "unknown-model"
    result = PipelineResult(run_id=run_id, model_name=model_name)
    client = llm_client
    max_cases = getattr(settings, "max_test_cases", 6)
    cases_per_request = getattr(settings, "max_cases_per_request", None)
    generation_dir = run_dir / "generation"

    generation = None
    source: dict[str, Any] | None = None
    try:
        if client is None:
            client = OpenAICompatibleClient(settings)
        generator = TestCaseGenerator(
            client,
            output_dir=generation_dir,
            model_name=model_name,
            max_test_cases=max_cases,
            cases_per_request=cases_per_request,
        )
        generation = generator.generate(request)
        source = read_json(generation.validated_path)
        result.phases["generation"] = {
            "status": "passed",
            "model_name": generation.metadata.model_name,
            "prompt_version": generation.metadata.prompt_version,
            "requirement_version": generation.metadata.requirement_version,
            "llm_calls": generation.metadata.llm_call_count,
            "case_count": generation.validation.case_count,
            "validation": generation.validation.model_dump(mode="json"),
        }
        result.usage_by_stage["generation"] = generation.metadata.token_usage.model_dump(mode="json")
        result.artifacts.update({
            "generation_raw": str(generation.raw_path),
            "validated_source": str(generation.validated_path),
        })
    except Exception as error:
        code, message, raw_text, usage = _stage_error(error)
        result.phases["generation"] = {"status": "failed", "code": code, "message": message}
        result.usage_by_stage["generation"] = usage
        raw_candidates = sorted((generation_dir / "raw").glob("*.json"))
        if raw_candidates:
            result.artifacts["generation_raw"] = str(raw_candidates[-1])
        result.status = "generation_failed"
        result.exit_code = 1
        _finish(result, run_dir)
        return result

    assert generation is not None and source is not None
    openapi = request.api_document
    if openapi is None:
        try:
            openapi = read_json(DEFAULT_OPENAPI)
        except (OSError, ValueError, TypeError):
            openapi = {}
    openapi_path = run_dir / "trusted-openapi.json"
    write_json(openapi_path, openapi)
    result.artifacts["trusted_openapi"] = str(openapi_path)

    plan_raw_path = run_dir / "execution-plan" / "raw" / f"{run_id}.json"
    plan_validated_path = run_dir / "execution-plan" / "validated" / f"{run_id}.json"
    messages = _build_messages(
        source=source,
        requirements=request.business_requirements,
        business_rules=request.business_rules,
        constraints=request.constraints,
        openapi=openapi,
    )
    plan_result = None
    raw_text: str | None = None
    planning_call_count = 0
    planning_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    plan_raw_data = {
        "metadata": {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_name": model_name,
            "prompt_version": PLAN_PROMPT_VERSION,
            "source_run_id": generation.metadata.run_id,
        },
        "request_messages": messages,
        "response_model": "ExecutionPlanDraft",
        "raw_output": None,
        "token_usage": planning_usage,
        "error": None,
    }
    try:
        planning_call_count = 1
        plan_result = generate_execution_plan(
            client,
            source=source,
            requirements=request.business_requirements,
            business_rules=request.business_rules,
            constraints=request.constraints,
            openapi=openapi,
        )
        raw_text = plan_result.llm_result.raw_text
        planning_usage = plan_result.llm_result.usage.model_dump(mode="json")
        plan_raw_data["raw_output"] = raw_text
        plan_raw_data["token_usage"] = planning_usage
        result.phases["planning"] = {
            "status": "passed",
            "model_name": model_name,
            "prompt_version": plan_result.prompt_version,
            "response_model": plan_result.llm_result.response_model,
            "llm_calls": planning_call_count,
        }
    except Exception as error:
        code, message, raw_text, planning_usage = _stage_error(error)
        plan_raw_data["raw_output"] = raw_text
        plan_raw_data["token_usage"] = planning_usage
        plan_raw_data["error"] = {"code": code, "message": message}
        result.phases["planning"] = {
            "status": "failed", "code": code, "message": message,
            "model_name": model_name, "prompt_version": PLAN_PROMPT_VERSION,
            "llm_calls": planning_call_count,
        }
    write_json(plan_raw_path, plan_raw_data)
    result.usage_by_stage["planning"] = planning_usage
    result.artifacts["planner_raw"] = str(plan_raw_path)

    bundle = permit = None
    outcomes: list[ConversionOutcome]
    if plan_result is None:
        code = result.phases["planning"]["code"]
        message = result.phases["planning"]["message"]
        outcomes = [ConversionOutcome(
            source_case_id=case.case_id, status="plan_invalid", code=code, reason=message
        ) for case in generation.test_suite.test_cases]
    else:
        bundle, outcomes, permit = create_automated_bundle(source, plan_result.mapping, openapi)
        explicit_reasons = {item.source_case_id: item.reason for item in plan_result.unmapped_cases}
        outcomes = [
            outcome.model_copy(update={"reason": explicit_reasons[outcome.source_case_id]})
            if outcome.source_case_id in explicit_reasons and outcome.status == "plan_invalid"
            else outcome
            for outcome in outcomes
        ]

    result.phases["local_validation"] = {
        "status": "passed" if bundle is not None and all(item.status == "ready" for item in outcomes)
        else ("passed_with_invalid_cases" if any(item.status == "ready" for item in outcomes) else "failed"),
        "validator_version": "coupon-plan-validator-v1",
        "outcomes": [item.model_dump(mode="json") for item in outcomes],
    }
    executable_path = run_dir / "execution" / "executable.json"
    permit_path = run_dir / "execution" / "automated-permit.json"
    if bundle is not None and permit is not None:
        write_json(executable_path, bundle)
        write_json(permit_path, permit)
        result.artifacts["executable_bundle"] = str(executable_path)
        result.artifacts["automated_permit"] = str(permit_path)
    if plan_result is not None:
        write_json(plan_validated_path, {
            "metadata": plan_raw_data["metadata"],
            "mapping": plan_result.mapping.model_dump(mode="json"),
            "unmapped_cases": [item.model_dump(mode="json") for item in plan_result.unmapped_cases],
            "outcomes": [item.model_dump(mode="json") for item in outcomes],
            "automated_permit": permit.model_dump(mode="json") if permit else None,
        })
        result.artifacts["planner_validated"] = str(plan_validated_path)

    outcome_by_id = {item.source_case_id: item for item in outcomes}
    case_manifest = []
    for case in generation.test_suite.test_cases:
        outcome = outcome_by_id.get(case.case_id)
        status = outcome.status if outcome else "plan_invalid"
        item = {
            "source_case_id": case.case_id,
            "title": case.title,
            "priority": case.priority,
            "scenario_type": case.scenario_type,
            "source_refs": case.source_refs,
            "status": status,
            "code": outcome.code if outcome else "plan_missing",
            "reason": outcome.reason if outcome else "第二阶段没有返回该用例的执行结果",
        }
        case_manifest.append(item)
    manifest_path = run_dir / "execution" / "pytest-cases.json"
    write_json(manifest_path, {"run_id": run_id, "cases": case_manifest})
    result.artifacts["case_manifest"] = str(manifest_path)

    if generation.test_suite.test_cases:
        results_dir = run_dir / "results"
        allure_dir = run_dir / "allure-results"
        detail_report = run_dir / "test-report.html"
        junit_report = run_dir / "pytest.xml"
        result.detail_report = detail_report
        result.artifacts.update({
            "results_dir": str(results_dir),
            "allure_results": str(allure_dir),
            "detail_report": str(detail_report),
            "junit_report": str(junit_report),
        })
        arguments = [
            "-q", "tests/test_ai_testgen_generated_cases.py",
            f"--ai-pipeline-cases={manifest_path}",
            f"--ai-openapi={openapi_path}",
            f"--ai-results={results_dir}",
            f"--alluredir={allure_dir}",
            f"--html={detail_report}", "--self-contained-html",
            f"--junitxml={junit_report}",
            "--ai-inprocess-api",
        ]
        if bundle is not None and permit is not None:
            arguments.extend([
                f"--ai-automated-execution={executable_path}",
                f"--ai-automated-permit={permit_path}",
            ])
        with tempfile.TemporaryDirectory(prefix="coupon-ai-test-") as isolated_dir:
            environment = os.environ.copy()
            environment.pop("LLM_API_KEY", None)
            environment["COUPON_DB_PATH"] = str(Path(isolated_dir) / "coupon.db")
            environment["COUPON_API_URL"] = BASE_URL
            runner = pytest_runner or _run_pytest_subprocess
            try:
                result.pytest_exit_code = int(runner(arguments, environment))
            except Exception as error:
                result.pytest_exit_code = 4
                result.phases["execution"] = {
                    "status": "failed", "code": "pytest_launch_failed",
                    "message": f"无法启动本地 Pytest 子进程（{type(error).__name__}）",
                }
        _collect_case_results(result, results_dir, case_manifest)
        if "execution" not in result.phases:
            result.phases["execution"] = {
                "status": "passed" if result.pytest_exit_code == 0 else "failed",
                "pytest_exit_code": result.pytest_exit_code,
                "target": "in-process local Coupon API",
                "database": "temporary SQLite, removed after execution",
            }
    else:
        result.phases["execution"] = {"status": "not_run", "message": "生成阶段没有可评估用例"}

    all_passed = bool(result.cases) and all(case.get("status") == "passed" for case in result.cases)
    result.status = "passed" if all_passed and result.pytest_exit_code == 0 else "failed"
    result.exit_code = 0 if result.status == "passed" else 1
    _finish(result, run_dir)
    return result


def _collect_case_results(result: PipelineResult, results_dir: Path, manifest: list[dict[str, Any]]) -> None:
    result.cases = []
    for case in manifest:
        path = results_dir / f"{case['source_case_id']}.json"
        entry = dict(case)
        entry["result_path"] = path
        if path.is_file():
            try:
                execution = read_json(path)
            except (OSError, ValueError, TypeError):
                execution = None
            if isinstance(execution, dict):
                entry.update({
                    "status": execution.get("status", "execution_error"),
                    "code": execution.get("code") or case.get("code"),
                    "reason": execution.get("reason") or case.get("reason"),
                    "execution_result": execution,
                })
            else:
                entry.update(status="execution_error", code="result_invalid", reason="结果文件无法解析")
        elif case["status"] == "ready":
            entry.update(status="execution_error", code="pytest_result_missing",
                         reason="Pytest 未为该可执行用例写入结果")
        result.cases.append(entry)


def _finish(result: PipelineResult, run_dir: Path) -> None:
    result.artifacts["run_directory"] = str(run_dir)
    result.summary_json = run_dir / "pipeline.json"
    result.summary_html = run_dir / "summary.html"
    result.artifacts["summary_json"] = str(result.summary_json)
    result.artifacts["summary_html"] = str(result.summary_html)
    write_summary_report(result, run_dir)


def _stage_error(error: Exception) -> tuple[str, str, str | None, dict[str, int]]:
    raw_text = getattr(error, "raw_text", None)
    usage_obj = getattr(error, "usage", None)
    usage = usage_obj.model_dump(mode="json") if usage_obj is not None else {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    }
    if isinstance(error, PlanValidationError):
        result = error.llm_result
        if result is not None:
            raw_text = result.raw_text
            usage = result.usage.model_dump(mode="json")
        return error.code, str(error), raw_text, usage
    if isinstance(error, OutputTruncatedError):
        return "output_truncated", str(error), raw_text, usage
    if isinstance(error, JSONParseError):
        return "json_parse_failed", str(error), raw_text, usage
    if isinstance(error, OutputSchemaError):
        return "schema_validation_failed", str(error), raw_text, usage
    if isinstance(error, LLMGenerationError):
        return "model_call_failed", str(error), raw_text, usage
    if isinstance(error, BusinessRuleValidationError):
        return "business_validation_failed", str(error), raw_text, usage
    return "stage_failed", f"{type(error).__name__}: {error}", raw_text, usage


def _run_pytest_subprocess(arguments: list[str], environment: dict[str, str]) -> int:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *arguments],
        cwd=root,
        env=environment,
        check=False,
    )
    return completed.returncode
