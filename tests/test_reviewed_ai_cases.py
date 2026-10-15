"""--ai-execution 文件中的每条已审核用例独立进入现有 fixture/报告链路。"""

from pathlib import Path

import allure
import pytest

from ai_testexec.executor import CaseExecutor
from ai_testexec.io import write_json
from tests.support.response_schemas import (
    CouponCreatedResponse, CouponClaimedResponse, CouponRedemptionResponse, UserCouponResponse,
)

pytestmark = pytest.mark.regression


def test_reviewed_case(api_client, reviewed_case, reviewed_run, request):
    bundle, receipt, openapi = reviewed_run
    executor = CaseExecutor(openapi, response_models={
        "createCoupon": CouponCreatedResponse, "claimCoupon": CouponClaimedResponse,
        "redeemCoupon": CouponRedemptionResponse, "getUserCoupon": UserCouponResponse,
    })
    result = executor.execute(bundle, reviewed_case, receipt, api_client)
    result = result.model_copy(update={"input_path": request.config.getoption("--ai-execution")})
    path = Path(request.config.getoption("--ai-results")) / (result.execution_run_id + ".json")
    write_json(path, result)
    request.node._ai_execution_result, request.node._ai_execution_path = result, path
    allure.dynamic.parameter("source_case_id", result.source_case_id)
    allure.dynamic.parameter("generation_run_id", result.generation_run_id)
    allure.dynamic.parameter("execution_case_id", result.execution_case_id)
    assert result.status == "passed", (
        f"source_case={result.source_case_id}; execution={result.execution_case_id}; "
        f"status={result.status}; code={result.code}; reason={result.reason}; result={path}"
    )
