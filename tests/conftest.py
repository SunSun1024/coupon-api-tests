import os

import pytest
import requests

from coupon_api.database import DATABASE_PATH
from tests.support.coupon_api_client import CouponApiClient
from tests.support.data_cleanup import CouponDataCleanup


@pytest.fixture
def base_url():
    """从环境变量读取被测 API 地址，未设置时使用本机服务。"""
    return os.getenv("COUPON_API_URL", "http://127.0.0.1:8000").rstrip("/")


@pytest.fixture
def api_session():
    """为单个测试创建 Requests 会话，并在测试结束后关闭。"""
    with requests.Session() as session:
        yield session


@pytest.fixture
def coupon_data_cleanup():
    """只删除当前测试通过 API 新建的优惠券及领取记录。"""
    cleanup = CouponDataCleanup(DATABASE_PATH)
    yield cleanup
    cleanup.cleanup()


@pytest.fixture
def api_client(base_url, api_session, coupon_data_cleanup):
    """提供统一的优惠券 API 请求入口，并登记本测试创建的数据。"""
    return CouponApiClient(base_url, api_session, coupon_data_cleanup)
