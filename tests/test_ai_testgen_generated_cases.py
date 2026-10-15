"""Pipeline-only Pytest entry point; the plugin hides this file in normal runs."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import allure
import pytest

from ai_testexec.errors import GateError
from ai_testexec.executor import CaseExecutor
from ai_testexec.io import write_json
from ai_testexec.models import ExecutionResult
from ai_testexec.pytest_plugin import load_automated_run
from ai_testexec.trace import observed_trace
from tests.support.response_schemas import (
    CouponClaimedResponse,
    CouponCreatedResponse,
    CouponRedemptionResponse,
    UserCouponResponse,
)


pytestmark = pytest.mark.regression


def test_generated_case(automated_case, request):
    case_id = automated_case["source_case_id"]
    result_path = Path(request.config.getoption("--ai-results")) / f"{case_id}.json"

    if automated_case.get("status") != "ready":
        result = _record_result(
            case_id,
            result_path,
            status="plan_invalid",
            code=automated_case.get("code", "plan_invalid"),
            reason=automated_case.get("reason", "执行计划未通过本地校验"),
            input_path=request.config.getoption("--ai-automated-execution"),
        )
        _publish(request, result, result_path)
        pytest.fail(
            f"source_case={case_id}; status=plan_invalid; "
            f"code={result.code}; reason={result.reason}; result={result_path}"
        )

    try:
        bundle, permit, openapi = load_automated_run(request.config)
    except GateError as error:
        result = _record_result(
            case_id, result_path, status="conversion_failed", code=error.code,
            reason=str(error), input_path=request.config.getoption("--ai-automated-execution"),
        )
        _publish(request, result, result_path)
        pytest.fail(
            f"source_case={case_id}; status={result.status}; code={result.code}; "
            f"reason={result.reason}; result={result_path}"
        )

    # Deliberately request the API fixture only after the case and machine permit pass.
    api_client = request.getfixturevalue("api_client")
    executor = CaseExecutor(openapi, response_models={
        "createCoupon": CouponCreatedResponse,
        "claimCoupon": CouponClaimedResponse,
        "redeemCoupon": CouponRedemptionResponse,
        "getUserCoupon": UserCouponResponse,
    })
    result = executor.execute(bundle, case_id, permit, api_client)
    result = result.model_copy(update={"input_path": request.config.getoption("--ai-automated-execution")})
    _publish(request, result, result_path)
    allure.dynamic.parameter("source_case_id", case_id)
    allure.dynamic.parameter("generation_run_id", result.generation_run_id)
    allure.dynamic.parameter("automated_permit_id", result.automated_permit_id)
    assert result.status == "passed", (
        f"source_case={case_id}; status={result.status}; code={result.code}; "
        f"reason={result.reason}; result={result_path}"
    )


def _record_result(case_id, result_path, *, status, code, reason, input_path=None):
    now = datetime.now(timezone.utc)
    trace = observed_trace(None, case_id, input_path=input_path)
    result = ExecutionResult(
        **trace,
        execution_run_id=uuid4().hex,
        started_at=now,
        ended_at=now,
        status=status,
        code=code,
        reason=reason,
        steps=[],
    )
    write_json(result_path, result)
    return result


def _publish(request, result, result_path):
    write_json(result_path, result)
    request.node._ai_execution_result = result
    request.node._ai_execution_path = result_path
