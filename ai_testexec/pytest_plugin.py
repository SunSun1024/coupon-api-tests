"""Pytest 集成：先验证审核，再运行网络 fixture；结果关联附加到现有报告。"""

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import allure
import pytest

from ai_testexec.adapter import verify_automated_bundle, verify_bundle
from ai_testexec.cli import DEFAULT_OPENAPI
from ai_testexec.errors import GateError
from ai_testexec.io import read_json, write_json
from ai_testexec.models import ExecutionResult
from ai_testexec.policy import validate_target
from ai_testexec.trace import observed_trace


def pytest_addoption(parser):
    group = parser.getgroup("reviewed AI cases")
    group.addoption("--ai-execution", help="reviewed executable JSON bundle")
    group.addoption("--ai-review", help="independent human review receipt")
    group.addoption("--ai-automated-execution", help="locally validated executable bundle for the one-command pipeline")
    group.addoption("--ai-automated-permit", help="machine validation record; not a human review receipt")
    group.addoption("--ai-pipeline-cases", help="manifest of all generated cases, including invalid plans")
    group.addoption("--ai-inprocess-api", action="store_true", help="use the local FastAPI ASGI app without a network port")
    group.addoption("--ai-openapi", default=str(DEFAULT_OPENAPI), help="trusted local OpenAPI")
    group.addoption("--ai-results", default="artifacts/ai_testexec/results", help="execution result JSON directory")


def record_blocked(config, error, *, bundle=None, openapi=None, trace_verified=False):
    now = datetime.now(timezone.utc)
    status = error.code if error.code in {"review_required", "review_invalid"} else (
        "execution_error" if error.code in {"target_forbidden", "api_unavailable"} else "conversion_failed")
    path = config.getoption("--ai-execution")
    if bundle is None:
        try:
            bundle = read_json(path)
        except (OSError, ValueError, TypeError):
            pass
    trace = observed_trace(bundle, openapi=openapi, input_path=path)
    trace["trace_verified"] = trace_verified
    result = ExecutionResult(**trace, execution_run_id=uuid4().hex,
                             started_at=now, ended_at=now, status=status, code=error.code,
                             reason=str(error), steps=[])
    write_json(Path(config.getoption("--ai-results")) / (result.execution_run_id + ".json"), result)


def load_run(config):
    if not config.getoption("--ai-execution"):
        return None
    bundle, openapi, verified = None, None, False
    try:
        if not config.getoption("--ai-review"):
            raise GateError("review_required", "执行必须指定独立的 --ai-review 审核记录")
        bundle = read_json(config.getoption("--ai-execution"))
        receipt = read_json(config.getoption("--ai-review"))
        openapi = read_json(config.getoption("--ai-openapi"))
        bundle = verify_bundle(bundle, receipt, openapi)
        verified = True
        validate_target(os.getenv("COUPON_API_URL", "http://127.0.0.1:8000"))
        return bundle, receipt, openapi
    except (OSError, ValueError, TypeError) as error:
        if not isinstance(error, GateError):
            error = GateError("input_invalid", "执行文件、审核记录或接口契约无法读取")
        record_blocked(config, error, bundle=bundle, openapi=openapi, trace_verified=verified)
        raise error from None


def load_automated_run(config):
    """Load and revalidate an automatic bundle only for cases marked ready."""
    execution_path = config.getoption("--ai-automated-execution")
    permit_path = config.getoption("--ai-automated-permit")
    if not execution_path and not permit_path:
        return None
    bundle, openapi = None, None
    try:
        if not execution_path or not permit_path:
            raise GateError("permit_invalid", "自动执行必须同时提供执行文件和机器校验凭据")
        bundle = read_json(execution_path)
        permit = read_json(permit_path)
        openapi = read_json(config.getoption("--ai-openapi"))
        bundle = verify_automated_bundle(bundle, permit, openapi)
        validate_target(os.getenv("COUPON_API_URL", "http://127.0.0.1:8000"))
        return bundle, permit, openapi
    except (OSError, ValueError, TypeError) as error:
        if not isinstance(error, GateError):
            error = GateError("input_invalid", "自动执行文件、机器凭据或接口契约无法读取")
        raise error from None


def read_pipeline_cases(config):
    path = config.getoption("--ai-pipeline-cases")
    if not path:
        return None
    try:
        manifest = read_json(path)
        cases = manifest["cases"]
        if not isinstance(cases, list):
            raise ValueError("cases must be a list")
        return cases
    except (KeyError, OSError, ValueError, TypeError):
        raise GateError("manifest_invalid", "自动流水线用例清单无法读取") from None


def pytest_generate_tests(metafunc):
    if "automated_case" in metafunc.fixturenames:
        try:
            cases = read_pipeline_cases(metafunc.config)
        except GateError as error:
            raise pytest.UsageError(f"{error.code}: {error}") from None
        # 用户显式运行普通 test_ai_testgen_*.py glob 时，这个 pipeline-only
        # 参数化入口安全跳过；run 子进程提供 manifest 后才会收集具体用例。
        if cases is None:
            cases = []
        metafunc.parametrize("automated_case", cases,
                             ids=[f"{case.get('source_case_id', 'unknown')}" for case in cases])
    if "reviewed_case" not in metafunc.fixturenames:
        return
    try:
        run = load_run(metafunc.config)
    except GateError as error:
        raise pytest.UsageError(f"{error.code}: {error}") from None
    cases = [] if run is None else run[0].cases
    metafunc.parametrize("reviewed_case", [case.source_case_id for case in cases],
                         ids=[f"{case.source_case_id}-{case.execution_case_id[:8]}" for case in cases])


@pytest.fixture
def reviewed_run(pytestconfig):
    return load_run(pytestconfig)


def pytest_ignore_collect(collection_path, config):
    """Keep the pipeline-only case runner out of ordinary project regression runs."""
    if collection_path.name == "test_ai_testgen_generated_cases.py":
        return not bool(config.getoption("--ai-pipeline-cases"))
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    result = getattr(item, "_ai_execution_result", None)
    if result is None:
        return
    if report.when == "teardown" and report.failed:
        # fixture清理失败也应改变最终JSON状态，不能保留一个误导的passed结果。
        result = result.model_copy(update={"status": "execution_error", "code": "cleanup_failed",
                                           "reason": "测试资源清理失败", "ended_at": datetime.now(timezone.utc)})
        item._ai_execution_result = result
        write_json(item._ai_execution_path, result)
    if report.when == "call" or (report.when == "teardown" and report.failed):
        allure.attach(result.model_dump_json(indent=2), name="Reviewed AI case execution result",
                      attachment_type=allure.attachment_type.JSON)
