import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai import LengthFinishReasonError

from ai_testgen.config import LLMSettings
from ai_testgen.generator import GenerationRequest, TestCaseGenerator as Generator
from ai_testgen.llm_client import (
    JSONParseError,
    LLMResult,
    OpenAICompatibleClient,
    OutputTruncatedError,
)
from ai_testgen.models import TestCaseBatch as BatchModel
from ai_testgen.models import TestSuite as SuiteModel, TokenUsage
from ai_testgen.validator import BusinessRuleValidationError


pytestmark = pytest.mark.regression


OPENAPI = {
    "openapi": "3.1.0",
    "paths": {
        "/users/{user_id}/coupons/{user_coupon_id}/redemptions": {
            "post": {
                "operationId": "redeemCoupon",
                "summary": "Redeem a claimed coupon",
            }
        }
    },
}


def suite_payload(**case_overrides):
    case = {
        "case_id": "minimum_order",
        "title": "Order meets the minimum",
        "objective": "Verify coupon redemption at the minimum amount",
        "priority": "P1",
        "scenario_type": "boundary",
        "preconditions": ["The user has claimed a valid coupon"],
        "steps": [
            {
                "step_number": 1,
                "action": "Submit an order at the minimum amount",
                "expected_result": "The redemption succeeds",
                "api_path": "/users/{user_id}/coupons/{user_coupon_id}/redemptions",
                "http_method": "POST",
                "operation_id": "redeemCoupon",
            }
        ],
        "expected_results": ["The coupon is redeemed"],
        "source_refs": ["REQ-001", "API-001"],
        "unverifiable_expectations": [],
    }
    case.update(case_overrides)
    return {
        "suite_title": "Coupon redemption",
        "test_cases": [case],
        "clarification_questions": [],
    }


class FakeLLMClient:
    def __init__(self, raw_text=None, parsed=None, error=None, responses=None):
        self.raw_text = raw_text
        self.parsed = parsed
        self.error = error
        self.responses = list(responses or [])
        self.calls = []

    def generate_structured(self, *, messages, response_model):
        self.calls.append({"messages": messages, "response_model": response_model})
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return self._coerce_batch_response(response, response_model)
        if self.error:
            raise self.error
        return self._coerce_batch_response(
            LLMResult(
                raw_text=self.raw_text,
                parsed=self.parsed,
                usage=TokenUsage(
                    prompt_tokens=123, completion_tokens=45, total_tokens=168
                ),
                response_model=response_model.__name__,
            ),
            response_model,
        )

    @staticmethod
    def _coerce_batch_response(response, response_model):
        parsed = response.parsed
        if response_model is BatchModel and isinstance(parsed, SuiteModel):
            parsed = BatchModel.model_validate(parsed.model_dump())
        return LLMResult(
            raw_text=response.raw_text,
            parsed=parsed,
            usage=response.usage,
            response_model=response_model.__name__,
        )


def make_generator(
    tmp_path: Path,
    payload=None,
    *,
    case_overrides=None,
    client_error=None,
    max_test_cases=6,
    cases_per_request=None,
    responses=None,
):
    suite = SuiteModel.model_validate(
        payload if payload is not None else suite_payload(**(case_overrides or {}))
    )
    raw_text = suite.model_dump_json(indent=2)
    client = FakeLLMClient(
        raw_text=raw_text,
        parsed=suite,
        error=client_error,
        responses=responses,
    )
    generator = Generator(
        client,
        output_dir=tmp_path / "artifacts",
        model_name="mock-model",
        max_test_cases=max_test_cases,
        cases_per_request=cases_per_request,
    )
    return generator, client, suite, raw_text


def request(**overrides):
    values = {
        "business_requirements": "A coupon can be redeemed when the order meets its minimum spend.",
        "api_document": OPENAPI,
        "business_rules": "The paid amount must never be negative.",
        "constraints": "Use integer cents for all monetary values.",
        "requirement_version": "coupon-redemption-v3",
    }
    values.update(overrides)
    return GenerationRequest(**values)


def test_generates_traced_artifacts_separately_and_saves_metadata(tmp_path):
    generator, client, suite, raw_text = make_generator(tmp_path)

    result = generator.generate(request())

    raw_path = tmp_path / "artifacts" / "raw" / f"{result.metadata.run_id}.json"
    validated_path = (
        tmp_path / "artifacts" / "validated" / f"{result.metadata.run_id}.json"
    )
    raw_artifact = json.loads(raw_path.read_text())
    validated_artifact = json.loads(validated_path.read_text())

    assert result.test_suite == suite
    assert raw_path.exists() and validated_path.exists()
    assert raw_artifact["raw_output"] == raw_text
    assert validated_artifact["test_suite"]["test_cases"][0]["case_id"] == "minimum_order"
    assert raw_artifact["metadata"] == validated_artifact["metadata"]
    assert raw_artifact["metadata"]["model_name"] == "mock-model"
    assert raw_artifact["metadata"]["prompt_version"] == "test-case-generation-v7"
    assert raw_artifact["metadata"]["requirement_version"] == "coupon-redemption-v3"
    assert raw_artifact["metadata"]["token_usage"] == {
        "prompt_tokens": 123,
        "completion_tokens": 45,
        "total_tokens": 168,
    }
    assert len(raw_artifact["metadata"]["input_sha256"]) == 64
    assert result.metadata.generated_at.tzinfo is not None
    assert result.validation.status == "passed"
    assert [source["source_id"] for source in validated_artifact["source_catalog"]] == [
        "REQ-001",
        "RULE-001",
        "CONSTRAINT-001",
        "API-001",
    ]
    assert "A coupon can be redeemed" not in raw_path.read_text()
    assert client.calls[0]["response_model"] is BatchModel
    prompt_text = client.calls[0]["messages"][1]["content"]
    assert "REQ-001" in prompt_text
    assert "API-001" in prompt_text
    prompt_payload = json.loads(prompt_text)
    assert prompt_payload["source_materials"][0]["kind"] == "requirement"
    assert prompt_payload["output_requirements"]["max_test_cases"] == 6
    assert len(raw_artifact["batch_outputs"]) == 1


def test_generation_focus_prioritizes_coverage_without_becoming_a_business_source(tmp_path):
    focus = "优先覆盖同一个订单不能叠加使用两张优惠券的场景。"
    generator, client, _, _ = make_generator(tmp_path)

    result = generator.generate(request(focus=focus))

    prompt_payload = json.loads(client.calls[0]["messages"][1]["content"])
    system_prompt = client.calls[0]["messages"][0]["content"]
    raw_artifact = json.loads(result.raw_path.read_text())
    validated_artifact = json.loads(result.validated_path.read_text())
    assert prompt_payload["generation_focus"] == focus
    assert "prioritiz" in system_prompt.lower()
    assert "must not override" in system_prompt.lower()
    assert all(focus not in source["text"] for source in prompt_payload["source_materials"])
    assert raw_artifact["generation_focus"] == focus
    assert validated_artifact["generation_focus"] == focus


def test_generation_focus_changes_the_input_hash(tmp_path):
    first_generator, _, _, _ = make_generator(tmp_path / "first")
    second_generator, _, _, _ = make_generator(tmp_path / "second")

    first_result = first_generator.generate(request(focus="Prioritize the non-stacking case."))
    second_result = second_generator.generate(request(focus="Prioritize the expiry case."))

    assert first_result.metadata.input_sha256 != second_result.metadata.input_sha256


def test_prompt_uses_the_configured_test_case_limit(tmp_path):
    generator, client, _, _ = make_generator(tmp_path, max_test_cases=3)

    generator.generate(request())

    system_prompt = client.calls[0]["messages"][0]["content"]
    prompt_payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert "no more than 3 test cases" in system_prompt
    assert "normally use no more than 3 steps per case" in system_prompt
    assert "Keep every field concise" in system_prompt
    assert prompt_payload["output_requirements"]["max_test_cases"] == 3
    assert prompt_payload["output_requirements"]["max_cases_in_this_request"] == 2
    assert prompt_payload["output_requirements"]["recommended_max_steps_per_case"] == 3
    assert "every required field" in system_prompt
    assert "[] for empty arrays" in system_prompt
    schema = prompt_payload["output_requirements"]["response_schema"]
    assert schema == BatchModel.model_json_schema()
    case_schema = schema["$defs"]["TestCase"]
    assert "preconditions" in case_schema["required"]
    assert case_schema["properties"]["preconditions"]["type"] == "array"
    assert case_schema["properties"]["priority"]["enum"] == ["P0", "P1", "P2", "P3"]


def test_prompt_compacts_openapi_without_dropping_contract_fields(tmp_path):
    api_document = {
        "openapi": "3.1.0",
        "info": {"title": "Long title", "version": "9", "description": "large info"},
        "servers": [{"url": "https://example.test", "description": "unused server"}],
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/admin": {
                "get": {
                    "security": [{"bearerAuth": []}],
                    "responses": {"200": {"description": "Admin response"}},
                }
            },
            "/health": {
                "get": {
                    "security": [],
                    "responses": {"200": {"description": "Public response"}},
                }
            },
            "/users/{user_id}/coupons/{user_coupon_id}/redemptions": {
                "post": {
                    "operationId": "redeemCoupon",
                    "summary": "Redeem a coupon",
                    "description": "Redeem a coupon with required fields",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateCoupon"},
                                "example": {"name": "very large example payload"},
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            "description": "Created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Coupon"},
                                    "examples": {"verbose": {"value": {"name": "large response"}}},
                                }
                            },
                        },
                        "409": {
                            "description": "Already exists",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                                    "examples": {
                                        "alreadyRedeemed": {
                                            "value": {"detail": "coupon is already redeemed"}
                                        }
                                    },
                                }
                            },
                        },
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "CreateCoupon": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "example": {"type": "string"},
                        "examples": {"type": "string"},
                        "servers": {"type": "string"},
                        "links": {"type": "string"},
                        "callbacks": {"type": "string"},
                    },
                },
                "Coupon": {
                    "type": "object",
                    "required": ["coupon_id"],
                    "properties": {
                        "coupon_id": {"type": "string"},
                        "owner": {"$ref": "#/components/schemas/CouponOwner"},
                    },
                },
                "CouponOwner": {
                    "type": "object",
                    "properties": {"user_id": {"type": "string"}},
                },
                "Unused": {"type": "object", "description": "do not send"},
            },
            "securitySchemes": {
                "apiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "bearerAuth": {"type": "http", "scheme": "bearer"},
            },
        },
    }
    generator, client, _, _ = make_generator(tmp_path)

    generator.generate(request(api_document=api_document))

    system_prompt = client.calls[0]["messages"][0]["content"]
    assert "Treat documented 4xx response" in system_prompt
    assert "Do not mark optional response details" in system_prompt
    prompt_payload = json.loads(client.calls[0]["messages"][1]["content"])
    assert "api_document" not in prompt_payload
    contract = prompt_payload["api_contract"]
    assert len(json.dumps(contract)) < len(json.dumps(api_document))
    operations = {operation["path"]: operation for operation in contract["operations"]}
    assert operations["/users/{user_id}/coupons/{user_coupon_id}/redemptions"]["method"] == "POST"
    assert set(
        operations["/users/{user_id}/coupons/{user_coupon_id}/redemptions"]["responses"]
    ) == {"201", "409"}
    responses = operations[
        "/users/{user_id}/coupons/{user_coupon_id}/redemptions"
    ]["responses"]
    assert responses["409"]["content"]["application/json"]["examples"] == {
        "alreadyRedeemed": {"value": {"detail": "coupon is already redeemed"}}
    }
    assert "examples" not in responses["201"]["content"]["application/json"]
    # operation.security 会覆盖根级安全策略；[] 明确允许该接口匿名访问。
    assert operations["/admin"]["security"] == [{"bearerAuth": []}]
    assert operations["/health"]["security"] == []
    assert contract["security"] == [{"apiKeyAuth": []}]
    schemas = contract["components"]["schemas"]
    assert schemas["CreateCoupon"]["properties"]["name"]["type"] == "string"
    assert {
        name: schemas["CreateCoupon"]["properties"][name]["type"]
        for name in ("example", "examples", "servers", "links", "callbacks")
    } == {
        "example": "string",
        "examples": "string",
        "servers": "string",
        "links": "string",
        "callbacks": "string",
    }
    assert contract["components"]["securitySchemes"]["apiKeyAuth"]["name"] == "X-API-Key"
    assert contract["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert schemas["Coupon"]["properties"]["coupon_id"]["type"] == "string"
    assert schemas["CouponOwner"]["properties"]["user_id"]["type"] == "string"
    assert "Unused" not in schemas
    prompt_text = json.dumps(contract)
    assert "very large example payload" not in prompt_text
    assert "large response" not in prompt_text


def test_sdk_truncation_is_split_and_recorded_in_raw_artifact(tmp_path):
    first_batch = BatchModel.model_validate(suite_payload())
    second_batch = BatchModel.model_validate(
        suite_payload(
            case_id="above_minimum",
            title="Order is above the minimum",
            objective="Verify coupon redemption above the minimum spend",
            steps=[
                {
                    **suite_payload()["test_cases"][0]["steps"][0],
                    "action": "Submit an order above the minimum amount",
                }
            ],
        )
    )

    def sdk_completion(parsed, usage, *, raw_text=None, finish_reason="stop"):
        prompt_tokens, completion_tokens, total_tokens = usage
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=raw_text or parsed.model_dump_json(),
                        parsed=parsed,
                        refusal=None,
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            ),
        )

    truncated_completion = sdk_completion(
        first_batch,
        (100, 200, 300),
        raw_text='{"partial":',
        finish_reason="length",
    )
    responses = [
        LengthFinishReasonError(completion=truncated_completion),
        sdk_completion(first_batch, (10, 20, 30)),
        sdk_completion(second_batch, (11, 21, 32)),
    ]

    class SequenceCompletions:
        def __init__(self, queued_responses):
            self.queued_responses = list(queued_responses)
            self.calls = []

        def parse(self, **kwargs):
            self.calls.append(kwargs)
            response = self.queued_responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    completions = SequenceCompletions(responses)
    sdk_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    llm_client = OpenAICompatibleClient(
        LLMSettings(api_key="test-key", model_name="mock-model", output_mode="native"),
        sdk_client=sdk_client,
    )
    generator = Generator(
        llm_client,
        output_dir=tmp_path / "artifacts",
        max_test_cases=2,
        cases_per_request=2,
    )

    result = generator.generate(request())

    raw_artifact = json.loads(result.raw_path.read_text())
    assert [case.case_id for case in result.test_suite.test_cases] == [
        "minimum_order",
        "above_minimum",
    ]
    assert [item["status"] for item in raw_artifact["batch_outputs"]] == [
        "truncated",
        "completed",
        "completed",
    ]
    assert raw_artifact["batch_outputs"][0]["raw_output"] == '{"partial":'
    assert raw_artifact["metadata"]["llm_call_count"] == 3
    assert raw_artifact["metadata"]["token_usage"] == {
        "prompt_tokens": 121,
        "completion_tokens": 241,
        "total_tokens": 362,
    }
    assert len(completions.calls) == 3


def test_generates_multiple_small_batches_and_aggregates_outputs_and_usage(tmp_path):
    first = SuiteModel.model_validate(suite_payload())
    second = SuiteModel.model_validate(
        suite_payload(
            case_id="above_minimum",
            title="Order is above the minimum",
            objective="Verify redemption above the minimum spend",
            steps=[
                {
                    **suite_payload()["test_cases"][0]["steps"][0],
                    "action": "Submit an order above the minimum amount",
                }
            ],
        )
    )
    responses = [
        LLMResult(
            raw_text="first raw",
            parsed=first,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            response_model="TestSuite",
        ),
        LLMResult(
            raw_text="second raw",
            parsed=second,
            usage=TokenUsage(prompt_tokens=11, completion_tokens=21, total_tokens=32),
            response_model="TestSuite",
        ),
    ]
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=2,
        cases_per_request=1,
        responses=responses,
    )

    result = generator.generate(request())

    assert [case.case_id for case in result.test_suite.test_cases] == [
        "minimum_order",
        "above_minimum",
    ]
    assert result.metadata.token_usage == TokenUsage(
        prompt_tokens=21, completion_tokens=41, total_tokens=62
    )
    assert len(client.calls) == 2
    prompts = [json.loads(call["messages"][1]["content"]) for call in client.calls]
    assert [prompt["output_requirements"]["max_test_cases"] for prompt in prompts] == [2, 2]
    assert [
        prompt["output_requirements"]["max_cases_in_this_request"] for prompt in prompts
    ] == [1, 1]
    assert prompts[0]["already_generated_cases"] == []
    assert prompts[1]["already_generated_cases"] == [
        {"case_id": "minimum_order", "title": "Order meets the minimum"}
    ]
    raw_artifact = json.loads(result.raw_path.read_text())
    assert raw_artifact["raw_output_format"] == "batch_bundle_v1"
    assert [item["raw_output"] for item in raw_artifact["batch_outputs"]] == [
        "first raw",
        "second raw",
    ]
    assert raw_artifact["metadata"]["llm_call_count"] == 2
    assert raw_artifact["metadata"]["token_usage"]["total_tokens"] == 62


def test_splits_a_truncated_batch_once_and_counts_the_failed_attempt(tmp_path):
    first = SuiteModel.model_validate(suite_payload())
    second = SuiteModel.model_validate(
        suite_payload(
            case_id="above_minimum",
            title="Order is above the minimum",
            objective="Verify redemption above the minimum spend",
            steps=[
                {
                    **suite_payload()["test_cases"][0]["steps"][0],
                    "action": "Submit an order above the minimum amount",
                }
            ],
        )
    )
    truncated = OutputTruncatedError(
        "output reached token limit",
        raw_text="{partial",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    )
    responses = [
        truncated,
        LLMResult(
            raw_text="first raw",
            parsed=first,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            response_model="TestSuite",
        ),
        LLMResult(
            raw_text="second raw",
            parsed=second,
            usage=TokenUsage(prompt_tokens=11, completion_tokens=21, total_tokens=32),
            response_model="TestSuite",
        ),
    ]
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=2,
        cases_per_request=2,
        responses=responses,
    )

    result = generator.generate(request())

    assert [case.case_id for case in result.test_suite.test_cases] == [
        "minimum_order",
        "above_minimum",
    ]
    assert len(client.calls) == 3
    prompts = [json.loads(call["messages"][1]["content"]) for call in client.calls]
    assert [prompt["output_requirements"]["max_test_cases"] for prompt in prompts] == [
        2,
        2,
        2,
    ]
    assert [
        prompt["output_requirements"]["max_cases_in_this_request"] for prompt in prompts
    ] == [2, 1, 1]
    assert result.metadata.token_usage == TokenUsage(
        prompt_tokens=121, completion_tokens=241, total_tokens=362
    )
    raw_artifact = json.loads(result.raw_path.read_text())
    assert [item["status"] for item in raw_artifact["batch_outputs"]] == [
        "truncated",
        "completed",
        "completed",
    ]
    assert raw_artifact["batch_outputs"][0]["raw_output"] == "{partial"
    assert raw_artifact["metadata"]["llm_call_count"] == 3


def test_empty_batch_stops_generation_without_fabricating_more_cases(tmp_path):
    first_payload = suite_payload()
    second_case = dict(first_payload["test_cases"][0])
    second_case.update(
        case_id="above_minimum",
        title="Redeem above the minimum",
        objective="Verify redemption above the minimum spend",
        steps=[
            {
                **first_payload["test_cases"][0]["steps"][0],
                "action": "Submit an order above the minimum amount",
            }
        ],
    )
    first_payload["test_cases"].append(second_case)
    first_batch = SuiteModel.model_validate(first_payload)
    empty_batch = BatchModel(
        suite_title="Coupon redemption",
        test_cases=[],
        clarification_questions=[],
    )
    responses = [
        LLMResult(
            raw_text="first batch",
            parsed=first_batch,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            response_model="TestSuite",
        ),
        LLMResult(
            raw_text="no more distinct scenarios",
            parsed=empty_batch,
            usage=TokenUsage(prompt_tokens=11, completion_tokens=5, total_tokens=16),
            response_model="TestCaseBatch",
        ),
    ]
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=4,
        cases_per_request=2,
        responses=responses,
    )

    result = generator.generate(request())

    assert len(result.test_suite.test_cases) == 2
    assert len(client.calls) == 2
    assert result.metadata.token_usage.total_tokens == 46
    assert json.loads(result.raw_path.read_text())["batch_outputs"][1]["status"] == "completed"


def test_single_case_truncation_fails_once_and_preserves_the_attempt(tmp_path):
    truncation = OutputTruncatedError(
        "output reached token limit",
        raw_text="",
        usage=TokenUsage(prompt_tokens=50, completion_tokens=8192, total_tokens=8242),
    )
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=1,
        cases_per_request=1,
        client_error=truncation,
    )

    with pytest.raises(OutputTruncatedError):
        generator.generate(request())

    raw_artifacts = list((tmp_path / "artifacts" / "raw").glob("*.json"))
    assert len(client.calls) == 1
    assert len(raw_artifacts) == 1
    artifact = json.loads(raw_artifacts[0].read_text())
    assert artifact["batch_outputs"][0]["status"] == "truncated"
    assert artifact["batch_outputs"][0]["raw_output"] == ""
    assert artifact["metadata"]["llm_call_count"] == 1
    assert artifact["metadata"]["token_usage"]["completion_tokens"] == 8192
    assert not list((tmp_path / "artifacts" / "validated").glob("*.json"))


def test_no_supported_scenarios_saves_raw_but_never_writes_an_empty_suite(tmp_path):
    empty_batch = BatchModel(
        suite_title="Coupon redemption",
        test_cases=[],
        clarification_questions=["Which order state should be used?"],
    )
    response = LLMResult(
        raw_text='{"suite_title":"Coupon redemption","test_cases":[]}',
        parsed=empty_batch,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=15, total_tokens=25),
        response_model="TestCaseBatch",
    )
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=2,
        cases_per_request=2,
        responses=[response],
    )

    with pytest.raises(BusinessRuleValidationError, match="found no test cases"):
        generator.generate(request())

    raw_path = next((tmp_path / "artifacts" / "raw").glob("*.json"))
    raw_artifact = json.loads(raw_path.read_text())
    assert len(client.calls) == 1
    assert raw_artifact["batch_outputs"][0]["status"] == "completed"
    assert not list((tmp_path / "artifacts" / "validated").glob("*.json"))


def test_empty_left_half_after_truncation_stops_before_calling_right_half(tmp_path):
    empty_batch = BatchModel(
        suite_title="Coupon redemption",
        test_cases=[],
        clarification_questions=[],
    )
    responses = [
        OutputTruncatedError(
            "output reached token limit",
            raw_text="",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        ),
        LLMResult(
            raw_text="no scenarios",
            parsed=empty_batch,
            usage=TokenUsage(prompt_tokens=11, completion_tokens=5, total_tokens=16),
            response_model="TestCaseBatch",
        ),
    ]
    generator, client, _, _ = make_generator(
        tmp_path,
        max_test_cases=2,
        cases_per_request=2,
        responses=responses,
    )

    with pytest.raises(BusinessRuleValidationError, match="found no test cases"):
        generator.generate(request())

    assert len(client.calls) == 2


def test_generator_rejects_a_model_suite_that_exceeds_the_configured_limit(tmp_path):
    payload = suite_payload()
    second_case = dict(payload["test_cases"][0])
    second_case.update(
        case_id="above_minimum",
        title="Redeem above the minimum",
        objective="Verify redemption above the minimum spend",
        steps=[
            {
                **payload["test_cases"][0]["steps"][0],
                "action": "Submit an order above the minimum amount",
            }
        ],
    )
    payload["test_cases"].append(second_case)
    generator, _, _, _ = make_generator(
        tmp_path, payload=payload, max_test_cases=1
    )

    with pytest.raises(BusinessRuleValidationError, match="maximum allowed test case count"):
        generator.generate(request())

    assert not list((tmp_path / "artifacts" / "validated").glob("*.json"))


def test_generates_question_and_warning_for_unverifiable_business_expectations(tmp_path):
    payload = suite_payload(
        unverifiable_expectations=["Whether a failed redemption restores stock"],
    )
    payload["clarification_questions"] = [
        "Should stock be restored after a failed redemption?"
    ]
    generator, _, _, _ = make_generator(tmp_path, payload=payload)

    result = generator.generate(request())
    validated = json.loads(result.validated_path.read_text())

    assert result.validation.status == "passed_with_warnings"
    assert result.test_suite.clarification_questions
    assert validated["validation"]["unverifiable_expectations"] == [
        "Whether a failed redemption restores stock"
    ]


def test_saves_raw_output_but_never_validated_output_when_business_validation_fails(
    tmp_path,
):
    payload = suite_payload(source_refs=["UNKNOWN-SOURCE"])
    generator, _, _, raw_text = make_generator(tmp_path, payload=payload)

    with pytest.raises(BusinessRuleValidationError):
        generator.generate(request())

    raw_files = list((tmp_path / "artifacts" / "raw").glob("*.json"))
    validated_dir = tmp_path / "artifacts" / "validated"
    assert len(raw_files) == 1
    assert json.loads(raw_files[0].read_text())["raw_output"] == raw_text
    assert not validated_dir.exists() or list(validated_dir.glob("*.json")) == []


def test_requirement_version_defaults_to_content_hash_and_is_reproducible(tmp_path):
    generator, _, _, _ = make_generator(tmp_path)
    same_request = request(requirement_version=None)

    result = generator.generate(same_request)

    assert result.metadata.requirement_version.startswith("sha256:")
    assert result.metadata.requirement_version == f"sha256:{result.metadata.input_sha256}"


def test_preserves_raw_json_parse_failure_and_token_usage(tmp_path):
    parse_error = JSONParseError(
        "model response is invalid JSON",
        raw_text="{invalid response",
        usage=TokenUsage(prompt_tokens=11, completion_tokens=7, total_tokens=18),
    )
    generator, client, _, _ = make_generator(tmp_path, client_error=parse_error)

    focus = "Prioritize the same-order non-stacking scenario."
    with pytest.raises(JSONParseError):
        generator.generate(request(focus=focus))

    raw_files = list((tmp_path / "artifacts" / "raw").glob("*.json"))
    assert len(raw_files) == 1
    raw_artifact = json.loads(raw_files[0].read_text())
    assert raw_artifact["raw_output"] == "{invalid response"
    assert raw_artifact["generation_focus"] == focus
    assert raw_artifact["metadata"]["token_usage"]["total_tokens"] == 18
    assert raw_artifact["batch_outputs"][0]["status"] == "failed"
    assert raw_artifact["metadata"]["llm_call_count"] == 1
    assert len(client.calls) == 1
    assert not (tmp_path / "artifacts" / "validated").exists()


def test_generation_request_rejects_non_object_openapi_input():
    from ai_testgen.generator import GenerationInputError

    with pytest.raises(GenerationInputError, match="OpenAPI document must be an object"):
        GenerationRequest(
            business_requirements="A coupon must be redeemed by its owner.",
            api_document=[],
        )

    with pytest.raises(GenerationInputError, match="requirement version must not be empty"):
        GenerationRequest(
            business_requirements="A coupon must be redeemed by its owner.",
            requirement_version=" ",
        )


def test_persists_empty_model_output_and_usage_when_provider_returns_no_content(tmp_path):
    empty_response = JSONParseError(
        "model response is empty",
        raw_text="",
        usage=TokenUsage(prompt_tokens=5, completion_tokens=0, total_tokens=5),
    )
    generator, _, _, _ = make_generator(tmp_path, client_error=empty_response)

    with pytest.raises(JSONParseError):
        generator.generate(request())

    raw_files = list((tmp_path / "artifacts" / "raw").glob("*.json"))
    assert len(raw_files) == 1
    raw_artifact = json.loads(raw_files[0].read_text())
    assert raw_artifact["raw_output"] == ""
    assert raw_artifact["metadata"]["token_usage"]["total_tokens"] == 5
