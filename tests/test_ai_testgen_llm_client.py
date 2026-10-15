import json
import logging
import sys
from types import SimpleNamespace

import pytest
from openai import LengthFinishReasonError
from pydantic import ValidationError

from ai_testgen.config import ConfigurationError, LLMSettings
from ai_testgen.llm_client import (
    JSONParseError,
    LLMGenerationError,
    OpenAICompatibleClient,
    OutputTruncatedError,
    OutputSchemaError,
)
from ai_testgen.models import TestSuite as SuiteModel


pytestmark = pytest.mark.regression


def valid_suite_payload():
    return {
        "suite_title": "Coupon tests",
        "test_cases": [
            {
                "case_id": "minimum_order",
                "title": "Order reaches the minimum",
                "objective": "Verify the minimum amount can redeem a coupon",
                "priority": "P1",
                "scenario_type": "boundary",
                "preconditions": ["The user has claimed a coupon"],
                "steps": [
                    {
                        "step_number": 1,
                        "action": "Submit an order",
                        "expected_result": "The redemption succeeds",
                        "api_path": "/coupons/redeem",
                        "http_method": "POST",
                        "operation_id": "redeemCoupon",
                    }
                ],
                "expected_results": ["The coupon becomes redeemed"],
                "source_refs": ["REQ-001"],
                "unverifiable_expectations": [],
            }
        ],
        "clarification_questions": [],
    }


def completion(
    *,
    raw_text=None,
    parsed=None,
    usage=(20, 30, 50),
    finish_reason="stop",
    reasoning_content=None,
):
    prompt_tokens, completion_tokens, total_tokens = usage
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=raw_text,
                    parsed=parsed,
                    refusal=None,
                    reasoning_content=reasoning_content,
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


class FakeCompletions:
    def __init__(self, *, parse_result=None, create_result=None, parse_error=None):
        self.parse_result = parse_result
        self.create_result = create_result
        self.parse_error = parse_error
        self.parse_calls = []
        self.create_calls = []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        if self.parse_error:
            raise self.parse_error
        return self.parse_result

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if isinstance(self.create_result, Exception):
            raise self.create_result
        return self.create_result


class FakeUnsupportedSchemaError(Exception):
    status_code = 400
    body = {"error": {"message": "response_format json_schema is not supported"}}


def fake_client(completions):
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def settings(**overrides):
    values = {
        "api_key": "sk-test-secret",
        "model_name": "test-model",
        "base_url": "https://llm.example.test/v1",
        "timeout_seconds": 7.5,
        "max_retries": 1,
        "output_mode": "auto",
    }
    values.update(overrides)
    return LLMSettings(**values)


def test_settings_read_model_endpoint_key_and_retry_values_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-from-env")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("LLM_MAX_TEST_CASES", "8")
    monkeypatch.setenv("LLM_MAX_CASES_PER_REQUEST", "2")
    monkeypatch.setenv("LLM_OUTPUT_MODE", "json_object")

    configured = LLMSettings.from_env()

    assert configured.api_key == "sk-from-env"
    assert configured.model_name == "example-model"
    assert configured.base_url == "https://llm.example.test/v1"
    assert configured.timeout_seconds == 12
    assert configured.max_retries == 3
    assert configured.max_output_tokens == 4096
    assert configured.max_test_cases == 8
    assert configured.max_cases_per_request == 2
    assert configured.output_mode == "json_object"


def test_settings_default_to_bounded_model_output_and_six_test_cases(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    monkeypatch.setenv("LLM_MODEL", "example-model")
    for variable in (
        "LLM_MAX_OUTPUT_TOKENS",
        "LLM_MAX_TEST_CASES",
        "LLM_MAX_CASES_PER_REQUEST",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_RETRIES",
        "LLM_OUTPUT_MODE",
    ):
        monkeypatch.delenv(variable, raising=False)

    configured = LLMSettings.from_env()

    assert configured.max_output_tokens == 8192
    assert configured.max_test_cases == 6
    assert configured.max_cases_per_request == 2


def test_settings_require_credentials_without_echoing_the_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    with pytest.raises(ConfigurationError) as exc_info:
        LLMSettings.from_env()

    assert "LLM_API_KEY" in str(exc_info.value)
    assert "sk-" not in str(exc_info.value)


def test_settings_reject_out_of_range_retry_count_without_exposing_key():
    with pytest.raises(ConfigurationError) as exc_info:
        settings(max_retries=100)

    assert "100" not in str(exc_info.value)
    assert "sk-test-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_output_tokens", 0, "LLM_MAX_OUTPUT_TOKENS"),
        ("max_test_cases", 0, "LLM_MAX_TEST_CASES"),
        ("max_cases_per_request", 0, "LLM_MAX_CASES_PER_REQUEST"),
    ],
)
def test_settings_reject_invalid_generation_limits(field, value, message):
    with pytest.raises(ConfigurationError, match=message):
        settings(**{field: value})


def test_openai_sdk_receives_configured_timeout_retries_and_base_url(monkeypatch):
    captured = {}
    sdk_module = SimpleNamespace()

    def create_openai_client(**kwargs):
        captured.update(kwargs)
        return fake_client(FakeCompletions())

    sdk_module.OpenAI = create_openai_client
    monkeypatch.setitem(sys.modules, "openai", sdk_module)

    OpenAICompatibleClient(settings())

    assert captured == {
        "api_key": "sk-test-secret",
        "base_url": "https://llm.example.test/v1",
        "timeout": 7.5,
        "max_retries": 1,
    }
    assert "sk-test-secret" not in repr(settings())


def test_native_structured_output_returns_parsed_suite_and_token_usage():
    parsed_suite = SuiteModel.model_validate(valid_suite_payload())
    endpoint = FakeCompletions(
        parse_result=completion(
            raw_text=parsed_suite.model_dump_json(), parsed=parsed_suite
        )
    )
    client = OpenAICompatibleClient(settings(), sdk_client=fake_client(endpoint))

    result = client.generate_structured(
        messages=[{"role": "user", "content": "generate cases"}],
        response_model=SuiteModel,
    )

    assert result.parsed == parsed_suite
    assert result.usage.prompt_tokens == 20
    assert result.usage.completion_tokens == 30
    assert result.usage.total_tokens == 50
    assert endpoint.parse_calls[0]["response_format"] is SuiteModel
    assert endpoint.parse_calls[0]["max_tokens"] == 8192
    assert endpoint.create_calls == []


def test_native_sdk_length_finish_error_preserves_partial_output_and_usage():
    truncated_completion = completion(
        raw_text='{"partial":',
        finish_reason="length",
        usage=(13, 27, 40),
    )
    endpoint = FakeCompletions(
        parse_error=LengthFinishReasonError(completion=truncated_completion)
    )
    client = OpenAICompatibleClient(settings(), sdk_client=fake_client(endpoint))

    with pytest.raises(OutputTruncatedError) as exc_info:
        client.generate_structured(messages=[], response_model=SuiteModel)

    assert exc_info.value.raw_text == '{"partial":'
    assert exc_info.value.usage.prompt_tokens == 13
    assert exc_info.value.usage.completion_tokens == 27
    assert exc_info.value.usage.total_tokens == 40
    assert endpoint.create_calls == []


def test_auto_mode_falls_back_to_json_object_only_when_schema_is_unsupported():
    endpoint = FakeCompletions(
        parse_error=FakeUnsupportedSchemaError(),
        create_result=completion(raw_text=json.dumps(valid_suite_payload())),
    )
    client = OpenAICompatibleClient(settings(), sdk_client=fake_client(endpoint))

    result = client.generate_structured(messages=[], response_model=SuiteModel)

    assert isinstance(result.parsed, SuiteModel)
    assert len(endpoint.parse_calls) == 1
    assert endpoint.create_calls[0]["response_format"] == {"type": "json_object"}
    assert endpoint.create_calls[0]["max_tokens"] == 8192


def test_native_service_errors_do_not_fallback_or_echo_sensitive_details(caplog):
    endpoint = FakeCompletions(
        parse_error=RuntimeError("sk-test-secret private customer prompt")
    )
    client = OpenAICompatibleClient(settings(), sdk_client=fake_client(endpoint))

    with caplog.at_level(logging.WARNING):
        with pytest.raises(LLMGenerationError) as exc_info:
            client.generate_structured(messages=[], response_model=SuiteModel)

    assert "sk-test-secret" not in str(exc_info.value)
    assert exc_info.value.raw_text is None
    assert "private customer prompt" not in caplog.text
    assert endpoint.create_calls == []


def test_native_schema_error_without_response_text_is_not_recorded_as_empty_output():
    with pytest.raises(ValidationError) as model_error:
        SuiteModel.model_validate({})
    endpoint = FakeCompletions(parse_error=model_error.value)
    client = OpenAICompatibleClient(settings(), sdk_client=fake_client(endpoint))

    with pytest.raises(OutputSchemaError) as exc_info:
        client.generate_structured(messages=[], response_model=SuiteModel)

    assert exc_info.value.raw_text is None


def test_json_object_mode_parses_json_and_distinguishes_invalid_json():
    endpoint = FakeCompletions(
        create_result=completion(raw_text=json.dumps(valid_suite_payload()))
    )
    client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(endpoint)
    )

    result = client.generate_structured(messages=[], response_model=SuiteModel)

    assert isinstance(result.parsed, SuiteModel)
    assert endpoint.create_calls[0]["max_tokens"] == 8192
    assert endpoint.parse_calls == []

    invalid_endpoint = FakeCompletions(
        create_result=completion(raw_text="{not valid json")
    )
    invalid_client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(invalid_endpoint)
    )
    with pytest.raises(JSONParseError) as parse_error:
        invalid_client.generate_structured(messages=[], response_model=SuiteModel)
    assert parse_error.value.raw_text == "{not valid json"
    assert parse_error.value.usage.total_tokens == 50


def test_json_object_mode_reports_when_response_hits_the_token_limit():
    endpoint = FakeCompletions(
        create_result=completion(raw_text='{"suite_title":', finish_reason="length")
    )
    client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(endpoint)
    )

    with pytest.raises(OutputTruncatedError, match="LLM_MAX_OUTPUT_TOKENS"):
        client.generate_structured(messages=[], response_model=SuiteModel)


def test_empty_content_at_token_limit_reports_finish_reason_and_usage():
    endpoint = FakeCompletions(
        create_result=completion(
            raw_text="",
            finish_reason="length",
            usage=(1217, 8192, 9409),
        )
    )
    client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(endpoint)
    )

    with pytest.raises(OutputTruncatedError) as error:
        client.generate_structured(messages=[], response_model=SuiteModel)

    assert "LLM_MAX_OUTPUT_TOKENS" in str(error.value)
    assert "finish_reason=length" in str(error.value)
    assert error.value.raw_text == ""
    assert error.value.usage.completion_tokens == 8192


def test_reasoning_only_empty_content_error_does_not_expose_reasoning_text():
    private_reasoning = "private chain of thought"
    endpoint = FakeCompletions(
        create_result=completion(raw_text="", reasoning_content=private_reasoning)
    )
    client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(endpoint)
    )

    with pytest.raises(JSONParseError) as error:
        client.generate_structured(messages=[], response_model=SuiteModel)

    assert "reasoning_content" in str(error.value)
    assert private_reasoning not in str(error.value)


def test_schema_errors_are_distinct_and_do_not_include_invalid_response_values():
    invalid = {"unexpected_private_value": "customer@example.test"}
    endpoint = FakeCompletions(create_result=completion(raw_text=json.dumps(invalid)))
    client = OpenAICompatibleClient(
        settings(output_mode="json_object"), sdk_client=fake_client(endpoint)
    )

    with pytest.raises(OutputSchemaError) as exc_info:
        client.generate_structured(messages=[], response_model=SuiteModel)

    assert "customer@example.test" not in str(exc_info.value)
    assert exc_info.value.raw_text == json.dumps(invalid)
    assert exc_info.value.usage.total_tokens == 50


def test_refusal_is_a_generation_error_and_empty_output_retains_usage():
    refusal = completion(raw_text=None, parsed=None)
    refusal.choices[0].message.refusal = "I cannot generate this content"
    refusal_client = OpenAICompatibleClient(
        settings(output_mode="json_object"),
        sdk_client=fake_client(FakeCompletions(create_result=refusal)),
    )

    with pytest.raises(LLMGenerationError) as refusal_error:
        refusal_client.generate_structured(messages=[], response_model=SuiteModel)
    assert "拒绝" in str(refusal_error.value)
    assert refusal_error.value.raw_text == "I cannot generate this content"
    assert refusal_error.value.usage.total_tokens == 50

    empty_client = OpenAICompatibleClient(
        settings(output_mode="json_object"),
        sdk_client=fake_client(
            FakeCompletions(create_result=completion(raw_text=None, parsed=None))
        ),
    )
    with pytest.raises(JSONParseError) as empty_error:
        empty_client.generate_structured(messages=[], response_model=SuiteModel)
    assert empty_error.value.raw_text == ""
    assert empty_error.value.usage.total_tokens == 50
