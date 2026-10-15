"""可信的优惠券执行映射样例；模型只提供业务用例，不提供 Python。"""

import json
from pathlib import Path

from ai_testgen.generator import GenerationRequest, TestCaseGenerator
from ai_testgen.llm_client import LLMResult
from ai_testgen.models import TestCaseBatch, TokenUsage


ROOT = Path(__file__).resolve().parents[2]


def contract():
    return json.loads((ROOT / "examples/ai_testgen/coupon-openapi.json").read_text())


def literal(value):
    return {"kind": "literal", "value": value}


def ref(name):
    return {"kind": "ref", "name": name}


def generate_source(output_dir):
    """经过真实生成器/校验器的 Mock 产物，不调用付费模型。"""
    batch = TestCaseBatch.model_validate({
        "suite_title": "同一订单不可叠加优惠券",
        "test_cases": [{
            "case_id": "order_non_stacking", "title": "拒绝同一订单的第二张券",
            "objective": "确认一张成功核销后，另一张券不能核销相同订单",
            "priority": "P1", "scenario_type": "negative",
            "preconditions": ["同一用户领取两张不同的有效券"],
            "steps": [{"step_number": 1, "action": "先核销第一张，再以同一订单核销第二张",
                       "expected_result": "第二次返回409，第二张券仍为claimed",
                       "api_path": "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
                       "http_method": "POST", "operation_id": "redeemCoupon"}],
            "expected_results": ["第一张为redeemed，第二张为claimed"],
            "source_refs": ["REQ-001", "API-004"], "unverifiable_expectations": []
        }], "clarification_questions": []
    })

    class MockModel:
        def generate_structured(self, **kwargs):
            return LLMResult(batch.model_dump_json(), batch, TokenUsage(), "TestCaseBatch")

    result = TestCaseGenerator(MockModel(), output_dir=output_dir, max_test_cases=1).generate(
        GenerationRequest("同一order_id最多成功核销一张券；第二张返回409，第一张保持redeemed，第二张保持claimed。",
                          api_document=contract(), requirement_version="non-stacking-v1")
    )
    return json.loads(result.validated_path.read_text())


def mapping(source):
    operations = []

    def request(step_id, operation_id, method, path, params, body, status, captures=None):
        operations.extend([
            {"op": "http_request", "step_id": step_id, "operation_id": operation_id,
             "method": method, "path": path, "path_params": params, "json_body": body,
             "captures": captures or {}},
            {"op": "assert_status", "step_id": step_id + "_status",
             "request_id": step_id, "expected": status}
        ])

    def check(step_id, request_id, pointer, expected):
        operations.append({"op": "assert_json", "step_id": step_id,
                           "request_id": request_id, "pointer": pointer, "expected": literal(expected)})

    for index in (1, 2):
        request(f"create{index}", "createCoupon", "POST", "/coupons", {}, {
            "name": ref("name"), "discount_amount_cents": literal(2000),
            "minimum_order_amount_cents": literal(10000), "total_quantity": literal(1),
            "expires_at": ref("expiry")
        }, 201, {f"coupon{index}": "/coupon_id"})
        request(f"claim{index}", "claimCoupon", "POST", "/users/{user_id}/coupons/{coupon_id}/claims",
                {"user_id": ref("user"), "coupon_id": ref(f"coupon{index}")}, None, 201,
                {f"claim{index}": "/user_coupon_id"})
    for index, status in ((1, 200), (2, 409)):
        request(f"redeem{index}", "redeemCoupon", "POST",
                "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
                {"user_id": ref("user"), "user_coupon_id": ref(f"claim{index}")},
                {"order_id": ref("order"), "order_amount_cents": literal(10000)}, status)
    check("second_error", "redeem2", "/detail", "order already has a redeemed coupon")
    for index, status in ((1, "redeemed"), (2, "claimed")):
        request(f"query{index}", "getUserCoupon", "GET", "/users/{user_id}/coupons/{user_coupon_id}",
                {"user_id": ref("user"), "user_coupon_id": ref(f"claim{index}")}, None, 200)
        check(f"state{index}", f"query{index}", "/status", status)
    operations.append({"op": "assert_json", "step_id": "first_order_unchanged", "request_id": "query1",
                       "pointer": "/order_id", "expected": ref("order")})
    check("first_discount_unchanged", "query1", "/applied_discount_cents", 2000)
    check("first_payment_unchanged", "query1", "/payable_amount_cents", 8000)
    check("second_order_empty", "query2", "/order_id", None)
    check("second_redemption_empty", "query2", "/redeemed_at", None)
    return {"version": 1, "source_run_id": source["metadata"]["run_id"], "cases": [{
        "source_case_id": "order_non_stacking", "environment": "local-coupon",
        "fixtures": [{"name": "user", "factory": "user_id"}, {"name": "order", "factory": "order_id"},
                     {"name": "name", "factory": "coupon_name"}, {"name": "expiry", "factory": "expires_at"}],
        "operations": operations
    }]}
