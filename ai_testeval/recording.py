"""Record generation evidence without logging credentials or exception messages."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from pydantic import BaseModel

from ai_testgen.llm_client import JSONParseError, LLMGenerationError, OutputSchemaError, OutputTruncatedError
from ai_testgen.models import TokenUsage
from ai_testeval.io import canonical_json
from ai_testeval.models import AttemptRecord


class RecordingClient:
    """Drop-in client wrapper that records each call before returning or re-raising."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.attempts: list[AttemptRecord] = []
        self.settings = getattr(inner, "settings", None)

    def generate_structured(
        self,
        *,
        messages: Sequence[dict[str, Any]],
        response_model: type[BaseModel],
    ) -> Any:
        started_at = datetime.now(timezone.utc)
        schema = response_model.model_json_schema()
        schema_hash = hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()
        try:
            result = self.inner.generate_structured(
                messages=messages,
                response_model=response_model,
            )
        except Exception as error:
            ended_at = datetime.now(timezone.utc)
            raw_text = getattr(error, "raw_text", None)
            usage = getattr(error, "usage", None)
            outcome = _error_outcome(error)
            self.attempts.append(
                AttemptRecord(
                    attempt_id=uuid.uuid4().hex,
                    started_at=started_at,
                    ended_at=ended_at,
                    requested_model=_requested_model(self.settings),
                    response_schema_name=response_model.__name__,
                    response_schema_sha256=schema_hash,
                    response_schema=schema,
                    messages=[dict(message) for message in messages],
                    call_settings=_safe_settings(self.settings),
                    outcome=outcome,
                    raw_text=raw_text,
                    raw_available=raw_text is not None,
                    schema_valid=False,
                    error_type=type(error).__name__,
                    finish_reason="length" if isinstance(error, OutputTruncatedError) else _finish_reason(error),
                    token_usage=_token_usage(usage),
                )
            )
            raise

        ended_at = datetime.now(timezone.utc)
        self.attempts.append(
            AttemptRecord(
                attempt_id=uuid.uuid4().hex,
                started_at=started_at,
                ended_at=ended_at,
                requested_model=_requested_model(self.settings),
                response_schema_name=response_model.__name__,
                response_schema_sha256=schema_hash,
                response_schema=schema,
                messages=[dict(message) for message in messages],
                call_settings=_safe_settings(self.settings),
                outcome="schema_valid",
                raw_text=result.raw_text,
                raw_available=result.raw_text is not None,
                schema_valid=True,
                error_type=None,
                finish_reason=_finish_reason(result),
                token_usage=_token_usage(getattr(result, "usage", None)),
            )
        )
        return result


def _error_outcome(error: Exception) -> str:
    if isinstance(error, OutputTruncatedError):
        return "truncated"
    if isinstance(error, JSONParseError):
        return "json_invalid"
    if isinstance(error, OutputSchemaError):
        return "schema_invalid"
    if isinstance(error, LLMGenerationError):
        return "generation_failed"
    return "generation_failed"


def _requested_model(settings: Any) -> str | None:
    model_name = getattr(settings, "model_name", None)
    return model_name if isinstance(model_name, str) else None


def _safe_settings(settings: Any) -> dict[str, Any]:
    """Whitelist non-secret call settings; never serialize the settings object itself."""
    if settings is None:
        return {}
    allowed = ("output_mode", "timeout_seconds", "max_retries", "max_output_tokens")
    return {
        name: getattr(settings, name)
        for name in allowed
        if getattr(settings, name, None) is not None
    }


def _token_usage(value: Any) -> TokenUsage:
    if isinstance(value, TokenUsage):
        return value
    if isinstance(value, dict):
        try:
            return TokenUsage.model_validate(value)
        except Exception:
            return TokenUsage()
    return TokenUsage()


def _finish_reason(value: Any) -> str | None:
    reason = getattr(value, "finish_reason", None)
    return reason if isinstance(reason, str) else None
