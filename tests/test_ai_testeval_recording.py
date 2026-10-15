from types import SimpleNamespace

import pytest

from ai_testgen.llm_client import LLMResult, JSONParseError, OutputSchemaError, OutputTruncatedError
from ai_testgen.models import TestCaseBatch as Batch, TokenUsage
from ai_testeval.recording import RecordingClient


class FakeClient:
    def __init__(self, result=None, error=None):
        self.settings = SimpleNamespace(model_name="fake-model", api_key="must-not-be-recorded")
        self.result = result
        self.error = error

    def generate_structured(self, *, messages, response_model):
        if self.error:
            raise self.error
        return self.result


def test_recording_client_captures_request_raw_response_usage_and_schema():
    messages = [{"role": "user", "content": "Generate one test case."}]
    result = LLMResult(
        raw_text='{"test_cases": []}',
        parsed=Batch.model_construct(),
        usage=TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
        response_model="TestCaseBatch",
    )
    client = RecordingClient(FakeClient(result=result))

    returned = client.generate_structured(messages=messages, response_model=Batch)

    attempt = client.attempts[0]
    assert returned is result
    assert attempt.messages == messages
    assert attempt.response_schema_name == "TestCaseBatch"
    assert attempt.raw_text == result.raw_text
    assert attempt.raw_available is True
    assert attempt.schema_valid is True
    assert attempt.token_usage.total_tokens == 20
    assert attempt.requested_model == "fake-model"
    assert "must-not-be-recorded" not in attempt.model_dump_json()
    assert "api_key" not in attempt.model_dump_json()


def test_recording_client_keeps_truncated_raw_text_and_typed_failure():
    client = RecordingClient(
        FakeClient(error=OutputTruncatedError("output ended at token limit", raw_text='{"test_cases":'))
    )

    with pytest.raises(OutputTruncatedError):
        client.generate_structured(
            messages=[{"role": "user", "content": "Generate."}],
            response_model=Batch,
        )

    attempt = client.attempts[0]
    assert attempt.outcome == "truncated"
    assert attempt.error_type == "OutputTruncatedError"
    assert attempt.raw_text == '{"test_cases":'
    assert attempt.raw_available is True
    assert attempt.schema_valid is False


def test_recording_client_does_not_serialize_untrusted_exception_text():
    secret = "token-should-not-be-copied-from-error"
    client = RecordingClient(FakeClient(error=RuntimeError(secret)))

    with pytest.raises(RuntimeError, match=secret):
        client.generate_structured(
            messages=[{"role": "user", "content": "Generate."}],
            response_model=Batch,
        )

    serialized = client.attempts[0].model_dump_json()
    assert client.attempts[0].outcome == "generation_failed"
    assert client.attempts[0].raw_available is False
    assert secret not in serialized


@pytest.mark.parametrize(
    "error,expected_outcome",
    [
        (JSONParseError("invalid JSON", raw_text="{"), "json_invalid"),
        (OutputSchemaError("missing required field", raw_text="{}"), "schema_invalid"),
    ],
)
def test_recording_client_distinguishes_json_and_schema_failures(error, expected_outcome):
    client = RecordingClient(FakeClient(error=error))

    with pytest.raises(type(error)):
        client.generate_structured(
            messages=[{"role": "user", "content": "Generate."}],
            response_model=Batch,
        )

    assert client.attempts[0].outcome == expected_outcome
