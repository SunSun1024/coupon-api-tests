import json
import os

import allure
import pytest
import requests

from coupon_api.database import DATABASE_PATH
from tests.support.coupon_api_client import CouponApiClient
from tests.support.data_cleanup import CouponDataCleanup

pytest_plugins = ["ai_testexec.pytest_plugin"]


def _attach_failed_api_exchanges(item, report):
    if report.when != "call" or not report.failed:
        return

    api_client = item.funcargs.get("api_client")
    if api_client is None:
        return

    for request_number, exchange in enumerate(api_client.exchanges, start=1):
        request = exchange["request"]
        allure.attach(
            json.dumps(exchange, ensure_ascii=False, indent=2),
            name=(
                f"API {request_number}: {request['method']} "
                f"{request['path']}"
            ),
            attachment_type=allure.attachment_type.JSON,
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Attach HTTP details to Allure only after a failed test call."""
    outcome = yield
    _attach_failed_api_exchanges(item, outcome.get_result())


@pytest.fixture(scope="session")
def base_url(pytestconfig):
    """从环境变量读取被测 API 地址，未设置时使用本机服务。"""
    value = os.getenv("COUPON_API_URL", "http://127.0.0.1:8000").rstrip("/")
    if pytestconfig.getoption("--ai-execution") or pytestconfig.getoption("--ai-automated-execution"):
        from ai_testexec.policy import validate_target
        validate_target(value)
    return value


@pytest.fixture(scope="session")
def api_ready(base_url, pytestconfig):
    """在第一次使用 API 客户端前检查服务是否已经启动。"""
    try:
        if pytestconfig.getoption("--ai-inprocess-api"):
            from ai_testexec.pytest_plugin import load_automated_run
            if pytestconfig.getoption("--ai-automated-execution"):
                load_automated_run(pytestconfig)
            return
        if pytestconfig.getoption("--ai-execution"):
            from ai_testexec.pytest_plugin import load_run
            load_run(pytestconfig)
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"{base_url}/docs", timeout=2, allow_redirects=False)
            if response.status_code != 200:
                raise requests.HTTPError("API readiness did not return HTTP 200")
    except requests.RequestException as error:
        if pytestconfig.getoption("--ai-execution"):
            from ai_testexec.errors import GateError
            from ai_testexec.pytest_plugin import record_blocked
            record_blocked(pytestconfig, GateError("api_unavailable", "Coupon API 就绪检查失败"))
        pytest.exit(
            f"Cannot reach the Coupon API at {base_url}. "
            "Start the API server with "
            "python -m uvicorn coupon_api.main:app --reload. "
            f"Details: {error}",
            returncode=4,
        )


@pytest.fixture
def api_session(pytestconfig):
    """为单个测试创建 Requests 会话，并在测试结束后关闭。"""
    if pytestconfig.getoption("--ai-inprocess-api"):
        from tests.support.asgi_coupon_session import ASGICouponSession
        session = ASGICouponSession()
        try:
            yield session
        finally:
            session.close()
        return
    with requests.Session() as session:
        session.trust_env = False
        yield session


@pytest.fixture
def coupon_data_cleanup():
    """只删除当前测试通过 API 新建的优惠券及领取记录。"""
    cleanup = CouponDataCleanup(DATABASE_PATH)
    yield cleanup
    cleanup.cleanup()


@pytest.fixture
def api_client(base_url, api_ready, api_session, coupon_data_cleanup):
    """提供统一的优惠券 API 请求入口，并登记本测试创建的数据。"""
    return CouponApiClient(base_url, api_session, coupon_data_cleanup)
