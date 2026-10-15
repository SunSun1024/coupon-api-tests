import json
from types import SimpleNamespace

import pytest

from ai_testgen import cli
from ai_testgen.llm_client import LLMResult
from ai_testgen.models import TestCaseBatch as BatchModel
from ai_testgen.models import TestSuite as SuiteModel, TokenUsage


pytestmark = pytest.mark.regression


def suite():
    return SuiteModel.model_validate(
        {
            "suite_title": "Coupon redemption",
            "test_cases": [
                {
                    "case_id": "coupon_at_minimum",
                    "title": "Redeem at minimum spend",
                    "objective": "Verify the minimum spend boundary",
                    "priority": "P1",
                    "scenario_type": "boundary",
                    "preconditions": ["The user has claimed a coupon"],
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
            ],
            "clarification_questions": [],
        }
    )


def write_inputs(tmp_path):
    requirements = tmp_path / "requirement.md"
    requirements.write_text(
        "A coupon can be redeemed when the order meets the minimum spend.",
        encoding="utf-8",
    )
    openapi = tmp_path / "openapi.json"
    openapi.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    "/users/{user_id}/coupons/{user_coupon_id}/redemptions": {
                        "post": {"operationId": "redeemCoupon"}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return requirements, openapi


def test_help_is_available_without_model_credentials(capsys):
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0
    assert "generate" in capsys.readouterr().out

    with pytest.raises(SystemExit) as generate_help:
        cli.main(["generate", "--help"])

    assert generate_help.value.code == 0
    assert "--focus" in capsys.readouterr().out

    with pytest.raises(SystemExit) as run_help:
        cli.main(["run", "--help"])

    assert run_help.value.code == 0
    run_help_text = capsys.readouterr().out
    assert "--focus" in run_help_text
    assert "--output-dir" in run_help_text


def test_missing_configuration_returns_a_safe_actionable_error(tmp_path, monkeypatch, capsys):
    requirements, _ = write_inputs(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    exit_code = cli.main(
        ["generate", "--requirements", str(requirements), "--output-dir", str(tmp_path)]
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "LLM_API_KEY" in error
    assert "LLM_MODEL" in error
    assert "sk-" not in error


def test_run_missing_credentials_fails_before_creating_pipeline(tmp_path, monkeypatch, capsys):
    requirements, openapi = write_inputs(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    exit_code = cli.main([
        "run", "--requirements", str(requirements), "--openapi", str(openapi),
        "--output-dir", str(tmp_path / "output"),
    ])

    assert exit_code == 2
    assert "LLM_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "output").exists()


def test_generate_command_runs_offline_with_a_mock_model_client(
    tmp_path, monkeypatch, capsys
):
    requirements, openapi = write_inputs(tmp_path)
    configured = SimpleNamespace(model_name="mock-model")
    generated_suite = suite()
    focus = "Prioritize the same-order non-stacking scenario."
    captured_messages = []

    class MockClient:
        settings = configured

        def generate_structured(self, *, messages, response_model):
            captured_messages.append(messages)
            assert response_model is BatchModel
            return LLMResult(
                raw_text=generated_suite.model_dump_json(),
                parsed=BatchModel.model_validate(generated_suite.model_dump()),
                usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
                response_model=response_model.__name__,
            )

    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setattr(cli, "OpenAICompatibleClient", lambda settings: MockClient())
    output_dir = tmp_path / "generation-output"

    exit_code = cli.main(
        [
            "generate",
            "--requirements",
            str(requirements),
            "--openapi",
            str(openapi),
            "--focus",
            focus,
            "--output-dir",
            str(output_dir),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "validation=passed" in output
    assert "prompt_tokens=100" in output
    assert "llm_calls=1" in output
    assert "validated" in output
    validated_files = list((output_dir / "validated").glob("*.json"))
    raw_files = list((output_dir / "raw").glob("*.json"))
    assert len(validated_files) == 1
    assert len(raw_files) == 1
    prompt_payload = json.loads(captured_messages[0][1]["content"])
    assert prompt_payload["generation_focus"] == focus
    assert json.loads(validated_files[0].read_text())["generation_focus"] == focus
    assert json.loads(raw_files[0].read_text())["generation_focus"] == focus
    assert "sk-test-secret" not in output


def test_invalid_openapi_json_fails_before_a_model_client_is_created(
    tmp_path, monkeypatch, capsys
):
    requirements, openapi = write_inputs(tmp_path)
    openapi.write_text("{not valid", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    monkeypatch.setenv("LLM_MODEL", "mock-model")
    monkeypatch.setattr(
        cli,
        "OpenAICompatibleClient",
        lambda settings: pytest.fail("model client must not be constructed"),
    )

    exit_code = cli.main(
        [
            "generate",
            "--requirements",
            str(requirements),
            "--openapi",
            str(openapi),
        ]
    )

    assert exit_code == 2
    assert "valid JSON" in capsys.readouterr().err
