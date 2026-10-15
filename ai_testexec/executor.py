"""固定三种操作的执行器。返回可追踪结果，由 Pytest 决定测试成败。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import requests
from pydantic import ValidationError

from ai_testexec.adapter import verify_automated_bundle, verify_bundle
from ai_testexec.contracts import Contract, read_pointer, validate_value
from ai_testexec.errors import GateError
from ai_testexec.io import strict_equal
from ai_testexec.models import AutomatedPermit, HttpRequest, AssertStatus, LiteralValue, ExecutionResult, StepResult
from ai_testexec.policy import validate_target, build_path
from ai_testexec.trace import observed_trace


def fixture_context(fixtures):
    factories = {
        "user_id": lambda: "test-user-" + uuid4().hex,
        "order_id": lambda: "test-order-" + uuid4().hex,
        "coupon_name": lambda: "ai-case-" + uuid4().hex,
        "expires_at": lambda: (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
    }
    return {fixture.name: factories[fixture.factory]() for fixture in fixtures}


class AssertionMismatch(Exception):
    def __init__(self, code, reason, expected=None, actual=None):
        self.code, self.expected, self.actual = code, expected, actual
        super().__init__(reason)


class CaseExecutor:
    def __init__(self, openapi, *, response_models=None):
        self.openapi = openapi
        self.response_models = response_models or {}

    def execute(self, bundle, source_case_id, review, client):
        started = datetime.now(timezone.utc)
        trace = observed_trace(bundle, source_case_id, openapi=self.openapi)
        trace.update(execution_run_id=uuid4().hex, started_at=started)
        steps = []

        def finish(status, code=None, reason=None):
            return ExecutionResult(**trace, ended_at=datetime.now(timezone.utc), status=status,
                                   code=code, reason=reason, steps=steps)

        try:
            raw_authorization = review.model_dump(mode="json") if isinstance(review, AutomatedPermit) else review
            if isinstance(raw_authorization, dict) and raw_authorization.get("kind") == "automated":
                verified = verify_automated_bundle(bundle, raw_authorization, self.openapi)
            else:
                verified = verify_bundle(bundle, raw_authorization, self.openapi)
            case = next((c for c in verified.cases if c.source_case_id == source_case_id), None)
            if case is None:
                raise GateError("schema_invalid", "执行文件未包含所选用例")
            trace.update(generation_run_id=case.generation_run_id, execution_case_id=case.execution_case_id,
                         source_digest=verified.source_digest, mapping_digest=verified.mapping_digest,
                         contract_digest=verified.contract_digest, review_id=verified.review_id,
                         automated_permit_id=verified.automated_permit_id, trace_verified=True)
            validate_target(client.base_url, case.mapping.environment)
            if getattr(client.session, "proxies", {}):
                raise GateError("target_forbidden", "执行会话不允许代理配置")
            if type(client.timeout) not in {int, float} or not 0.1 <= client.timeout <= 30:
                raise GateError("timeout_invalid", "HTTP超时必须在0.1至30秒之间")
            contract = Contract(self.openapi)
        except GateError as error:
            status = error.code if error.code in {"review_required", "review_invalid"} else (
                "execution_error" if error.code in {"target_forbidden", "timeout_invalid"} else "conversion_failed")
            return finish(status, error.code, str(error))

        context = fixture_context(case.mapping.fixtures)
        responses, bodies, capture_sources, http_operations = {}, {}, {}, {}

        def body(request_id):
            if request_id not in bodies:
                try:
                    bodies[request_id] = responses[request_id].json()
                except (ValueError, KeyError):
                    raise GateError("response_json_invalid", "响应不是可解析JSON") from None
            return bodies[request_id]

        def value(reference):
            if isinstance(reference, LiteralValue):
                return reference.value
            if reference.name not in context:
                if reference.name not in capture_sources:
                    raise GateError("variable_missing", "运行时变量不存在")
                request_id, pointer = capture_sources[reference.name]
                context[reference.name] = read_pointer(body(request_id), pointer)
            return context[reference.name]

        for op in case.mapping.operations:
            step_trace = dict(step_id=op.step_id, op=op.op,
                              request_id=op.step_id if isinstance(op, HttpRequest) else op.request_id)
            try:
                if isinstance(op, HttpRequest):
                    params = {k: value(v) for k, v in op.path_params.items()}
                    payload = None if op.json_body is None else {k: value(v) for k, v in op.json_body.items()}
                    query = {k: value(v) for k, v in op.query.items()}
                    # 静态转换期检查过的引用，在这里针对真实响应值再次检查。
                    contract.validate_request(op, params, payload, query)
                    path = build_path(op.path, params)
                    responses[op.step_id] = client.request_operation(op.operation_id, op.method, path,
                                                                    json_body=payload, query=query)
                    http_operations[op.step_id] = op
                    for name, pointer in op.captures.items():
                        capture_sources[name] = op.step_id, pointer
                elif isinstance(op, AssertStatus):
                    actual = responses[op.request_id].status_code
                    if actual != op.expected:
                        raise AssertionMismatch("status_mismatch", "HTTP状态码不符合预期", op.expected, actual)
                    operation = http_operations[op.request_id]
                    try:
                        validate_value(body(op.request_id), contract.response_schema(operation.operation_id, actual), "response")
                        success_status = 201 if operation.operation_id in {"createCoupon", "claimCoupon"} else 200
                        model = self.response_models.get(operation.operation_id)
                        if model is not None and actual == success_status:
                            model.model_validate(body(op.request_id))
                    except (GateError, ValidationError) as error:
                        if isinstance(error, GateError) and error.code == "response_json_invalid":
                            raise
                        raise AssertionMismatch("response_contract_failed", "响应字段不符合契约") from None
                else:
                    actual = read_pointer(body(op.request_id), op.pointer)
                    expected = value(op.expected)
                    if not strict_equal(expected, actual):
                        raise AssertionMismatch("json_mismatch", f"JSON字段不符合预期: {op.pointer}", expected, actual)
                steps.append(StepResult(**step_trace, status="passed"))
            except AssertionMismatch as error:
                steps.append(StepResult(**step_trace, status="failed", code=error.code,
                                        reason=str(error), expected=error.expected, actual=error.actual))
                return finish("assertion_failed", error.code, str(error))
            except requests.Timeout:
                code, reason = "request_timeout", "目标接口超时；写请求未自动重试"
            except requests.RequestException as error:
                code, reason = "request_failed", "HTTP请求异常: " + type(error).__name__
            except GateError as error:
                code, reason = error.code, str(error)
            except (ValueError, KeyError, TypeError):
                code, reason = "response_invalid", "响应解析或字段提取失败"
            else:
                continue
            steps.append(StepResult(**step_trace, status="failed", code=code, reason=reason))
            return finish("execution_error", code, reason)
        return finish("passed")
