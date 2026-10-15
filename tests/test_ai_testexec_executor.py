import json
from types import SimpleNamespace

import pytest
import requests

from tests.support.ai_case_examples import contract, generate_source, mapping
from tests.support.coupon_api_client import CouponApiClient

pytestmark = pytest.mark.regression


def response(status, body):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(body).encode()
    return value


class FakeSession:
    def __init__(self, error=None, wrong_state=False, invalid_capture=False, redirect=False):
        self.calls, self.trust_env, self.proxies = [], True, {}
        self.error, self.wrong_state = error, wrong_state
        self.invalid_capture, self.redirect = invalid_capture, redirect
        self.created, self.claimed, self.redeemed = 0, 0, 0
        self.order_id = None

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise self.error
        if self.redirect:
            return response(302, {"location": "https://external.test"})
        if url.endswith("/coupons"):
            self.created += 1
            coupon_id = "bad/id" if self.invalid_capture else f"coupon-{self.created}"
            return response(201, {"coupon_id": coupon_id, "name": kwargs["json"]["name"]})
        if url.endswith("/claims"):
            self.claimed += 1
            return response(201, {"user_coupon_id": f"claim-{self.claimed}", "coupon_id": f"coupon-{self.claimed}", "status": "claimed"})
        if url.endswith("/redemptions"):
            self.redeemed += 1
            if self.redeemed == 2:
                return response(409, {"detail": "order already has a redeemed coupon"})
            self.order_id = kwargs["json"]["order_id"]
            return response(200, {"user_coupon_id": "claim-1", "order_id": kwargs["json"]["order_id"],
                                  "status": "redeemed", "applied_discount_cents": 2000, "payable_amount_cents": 8000})
        redeemed = url.endswith("/claim-1")
        return response(200, {"user_coupon_id": "claim-1" if redeemed else "claim-2", "user_id": "test-user",
                              "coupon_id": "coupon-1" if redeemed else "coupon-2",
                              "status": "claimed" if self.wrong_state else ("redeemed" if redeemed else "claimed"),
                              "claimed_at": "2030-01-01T00:00:00Z", "order_id": self.order_id if redeemed else None,
                              "redeemed_at": "2030-01-01T00:00:00Z" if redeemed else None,
                              "applied_discount_cents": 2000 if redeemed else None,
                              "payable_amount_cents": 8000 if redeemed else None})


class Cleanup:
    def __init__(self):
        self.coupons, self.claims = [], []

    def add_coupon(self, identifier):
        self.coupons.append(identifier)

    def add_user_coupon(self, identifier):
        self.claims.append(identifier)


@pytest.fixture
def approved(tmp_path):
    from ai_testexec.adapter import CaseAdapter
    from ai_testexec.review import create_review
    source = generate_source(tmp_path / "source")
    plan, schema = mapping(source), contract()
    review = create_review(source, plan, schema, reviewer="tester")
    bundle = CaseAdapter().adapt(source, plan, schema, review).bundle
    return bundle, review, schema


def run(approved, session=None, base_url="http://127.0.0.1:8000", review=True):
    from ai_testexec.executor import CaseExecutor
    bundle, receipt, schema = approved
    session = session or FakeSession()
    cleanup = Cleanup()
    client = CouponApiClient(base_url, session, cleanup)
    result = CaseExecutor(schema).execute(bundle, "order_non_stacking", receipt if review else None, client)
    return result, session, cleanup, client


def test_execute_same_order_non_stacking_with_existing_client(approved):
    result, session, cleanup, client = run(approved)
    assert result.status == "passed"
    assert len(session.calls) == 8
    assert len(cleanup.coupons) == len(cleanup.claims) == 2
    assert len(client.exchanges) == 8
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert session.trust_env is False
    assert result.generation_run_id == approved[0].source.metadata.run_id
    assert result.execution_case_id == approved[0].cases[0].execution_case_id


def test_wrong_json_assertion_records_actual_expected_and_step(approved):
    result, _, _, _ = run(approved, FakeSession(wrong_state=True))
    assert result.status == "assertion_failed"
    assert result.steps[-1].step_id == "state1"
    assert result.steps[-1].expected == "redeemed"
    assert result.steps[-1].actual == "claimed"
    assert result.steps[-1].request_id == "query1"


def test_timeout_is_execution_error_and_write_not_retried(approved):
    result, session, _, client = run(approved, FakeSession(error=requests.Timeout("timeout")))
    assert result.status == "execution_error"
    assert result.code == "request_timeout"
    assert len(session.calls) == 1
    assert client.exchanges[0]["error"]["type"] == "Timeout"


@pytest.mark.parametrize("url", ["https://example.test", "http://localhost:8000", "http://127.0.0.1:9999", "http://127.0.0.1:8000@external.test"])
def test_external_targets_refused_before_any_request(approved, url):
    result, session, _, _ = run(approved, base_url=url)
    assert result.code == "target_forbidden"
    assert session.calls == []


def test_unreviewed_execution_makes_zero_requests(approved):
    result, session, _, _ = run(approved, review=False)
    assert result.status == "review_required"
    assert session.calls == []
    assert result.generation_run_id == approved[0].source.metadata.run_id
    assert result.input_digest is not None
    assert result.trace_verified is False


def test_tampered_input_remains_traceable_without_becoming_trusted(approved):
    from ai_testexec.executor import CaseExecutor
    from ai_testexec.io import digest
    bundle, receipt, schema = approved
    edited = bundle.model_dump(mode="json")
    edited["mapping"]["cases"][0]["operations"][0]["json_body"]["discount_amount_cents"]["value"] = 1000
    session = FakeSession()
    result = CaseExecutor(schema).execute(edited, "order_non_stacking", receipt,
                                         CouponApiClient("http://127.0.0.1:8000", session, Cleanup()))
    assert result.status == "review_invalid"
    assert session.calls == []
    assert result.trace_verified is False
    assert result.input_digest == digest(edited)
    assert result.mapping_digest == digest(edited["mapping"])
    assert result.generation_run_id == bundle.source.metadata.run_id
    assert result.execution_case_id == bundle.cases[0].execution_case_id


def test_runtime_response_reference_cannot_inject_url(approved):
    result, session, _, _ = run(approved, FakeSession(invalid_capture=True))
    assert result.status == "execution_error"
    assert len(session.calls) == 1


def test_redirect_is_not_followed(approved):
    result, session, _, _ = run(approved, FakeSession(redirect=True))
    assert result.status == "assertion_failed"
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False


def test_separate_runs_get_separate_fixture_data(approved):
    first, session1, _, _ = run(approved)
    second, session2, _, _ = run(approved)
    assert first.status == second.status == "passed"
    assert session1.calls[0][2]["json"]["name"] != session2.calls[0][2]["json"]["name"]
    assert session1.calls[4][2]["json"]["order_id"] != session2.calls[4][2]["json"]["order_id"]


@pytest.mark.parametrize("changed", ["source", "openapi", "derived_mapping"])
def test_changed_review_inputs_are_rejected_before_http(approved, changed):
    import copy
    from ai_testexec.executor import CaseExecutor
    bundle, receipt, schema = approved
    raw, schema = bundle.model_dump(mode="json"), copy.deepcopy(schema)
    if changed == "source":
        raw["source"]["test_suite"]["test_cases"][0]["objective"] = "经过修改的目标"
    elif changed == "openapi":
        schema["info"]["description"] = "new contract version"
    else:
        raw["cases"][0]["mapping"]["operations"][1]["expected"] = 200
    session = FakeSession()
    result = CaseExecutor(schema).execute(raw, "order_non_stacking", receipt,
                                         CouponApiClient("http://127.0.0.1:8000", session, Cleanup()))
    assert result.status == "review_invalid"
    assert session.calls == []
    assert result.input_digest and result.trace_verified is False


def test_proxy_configuration_is_rejected_before_http(approved):
    session = FakeSession()
    session.proxies = {"http": "http://proxy.example.test"}
    result, session, _, _ = run(approved, session)
    assert result.code == "target_forbidden"
    assert session.calls == []
