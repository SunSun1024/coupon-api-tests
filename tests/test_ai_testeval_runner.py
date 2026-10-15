import json
from types import SimpleNamespace

from ai_testgen.llm_client import LLMResult
from ai_testgen.models import TestCaseBatch as Batch, TokenUsage
from ai_testeval.dataset import create_approval, dataset_digest
from ai_testeval.models import ExperimentConfig, GoldenDataset
from ai_testeval.runner import run_experiment


def dataset_payload():
    samples = []
    for index in (1, 2):
        samples.append({
            "sample_id": f"coupon-{index}",
            "requirement_id": f"REQ-ID-{index}",
            "requirement_version": "v1",
            "requirements": f"Requirement {index}: claimed coupon redemption is supported.",
            "business_rules": [{
                "rule_id": f"RULE-ID-{index}",
                "text": f"Rule {index}: minimum is 10000 cents.",
                "source_refs": [f"REQ-ID-{index}"],
            }],
            "api_constraints": [{
                "constraint_id": f"API-CONSTRAINT-{index}",
                "text": "POST /redeem returns a redemption result.",
            }],
            "openapi_document": {
                "openapi": "3.1.0",
                "paths": {"/redeem": {"post": {"operationId": "redeemCoupon"}}},
            },
            "risks": [{
                "risk_id": f"RISK-{index}",
                "description": f"PRIVATE-GOLD-RISK-SENTINEL-{index}",
                "severity": "high",
                "source_refs": [f"RULE-ID-{index}"],
                "rationale": "A human reviewed this expected failure mode.",
            }],
            "expected_scenarios": [{
                "scenario_id": f"SCENARIO-{index}",
                "scenario_type": "positive",
                "risk_ids": [f"RISK-{index}"],
                "preconditions": ["The coupon is claimed."],
                "actions": ["Redeem at the minimum amount."],
                "assertions": ["The request succeeds."],
            }],
            "rubric_version": "v1",
        })
    return {
        "schema_version": 1,
        "dataset_id": "coupon-evaluation",
        "dataset_version": "1.0.0",
        "description": "Two synthetic samples for runner tests.",
        "samples": samples,
    }


def successful_batch():
    return Batch.model_validate({
        "suite_title": "Coupon redemption",
        "test_cases": [{
            "case_id": "redeem_minimum",
            "title": "Redeem at minimum order amount",
            "objective": "Check successful redemption at the minimum.",
            "priority": "P1",
            "scenario_type": "positive",
            "preconditions": ["The user has claimed a coupon."],
            "steps": [{
                "step_number": 1,
                "action": "POST /redeem with 10000 cents.",
                "expected_result": "The response is successful.",
                "api_path": "/redeem",
                "http_method": "POST",
                "operation_id": "redeemCoupon",
            }],
            "expected_results": ["The coupon is redeemed."],
            "source_refs": ["REQ-001", "RULE-001", "CONSTRAINT-001", "API-001"],
            "unverifiable_expectations": [],
        }],
        "clarification_questions": [],
    })


class FakeClient:
    def __init__(self, *, result=None, error=None):
        self.settings = SimpleNamespace(
            model_name="fake-model",
            output_mode="json_object",
            timeout_seconds=10.0,
            max_retries=0,
            max_output_tokens=4096,
        )
        self.result = result
        self.error = error

    def generate_structured(self, *, messages, response_model):
        if self.error is not None:
            raise self.error
        return self.result


def test_runner_keeps_failed_slots_and_continues_without_sending_gold_risks(tmp_path):
    dataset = GoldenDataset.model_validate(dataset_payload())
    approval = create_approval(dataset, reviewer="test-reviewer")
    config = ExperimentConfig(
        experiment_id="mock-evaluation",
        provider="mock",
        requested_model="fake-model",
        dataset_sha256=dataset_digest(dataset),
        sample_ids=["coupon-1", "coupon-2"],
        repetitions=1,
        max_test_cases=1,
        cases_per_request=1,
        output_mode="json_object",
        max_output_tokens=4096,
        timeout_seconds=10,
        max_retries=0,
    )
    batch = successful_batch()
    successful_result = LLMResult(
        raw_text=batch.model_dump_json(),
        parsed=batch,
        usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        response_model="TestCaseBatch",
    )
    clients = iter([
        FakeClient(error=RuntimeError("simulated timeout")),
        FakeClient(result=successful_result),
    ])

    manifest = run_experiment(
        dataset,
        approval,
        config,
        client_factory=lambda _config: next(clients),
        output_dir=tmp_path / "artifacts",
    )

    assert [(slot.sample_id, slot.status) for slot in manifest.slots] == [
        ("coupon-1", "failed"),
        ("coupon-2", "complete"),
    ]
    assert len(manifest.slots[0].attempts) == 1
    assert manifest.slots[0].attempts[0].raw_available is False
    assert manifest.slots[1].attempts[0].schema_valid is True
    prompt_text = json.dumps(manifest.slots[1].attempts[0].messages, ensure_ascii=False)
    assert "PRIVATE-GOLD-RISK-SENTINEL" not in prompt_text
    assert "Requirement 2" in prompt_text

    saved_manifest = json.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    assert [slot["status"] for slot in saved_manifest["slots"]] == ["failed", "complete"]
    assert saved_manifest["config"]["max_retries"] == 0
