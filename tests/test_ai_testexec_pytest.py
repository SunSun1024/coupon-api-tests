import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.ai_case_examples import ROOT, contract, generate_source, mapping

pytestmark = pytest.mark.regression


def prepared(tmp_path):
    from ai_testexec.adapter import CaseAdapter
    from ai_testexec.review import create_review
    source = generate_source(tmp_path / "generated")
    plan, schema = mapping(source), contract()
    review = create_review(source, plan, schema, reviewer="pytest-fixture-human")
    bundle = CaseAdapter().adapt(source, plan, schema, review).bundle
    paths = {}
    for name, value in (("execution", bundle.model_dump(mode="json")), ("review", review.model_dump(mode="json")), ("openapi", schema)):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    return paths


def invoke(tmp_path, paths, *, missing_review=False, url=None, fail_state=False, fail_cleanup=False):
    args = ["-q", "tests/test_reviewed_ai_cases.py", "--ai-execution", str(paths["execution"]),
            "--ai-openapi", str(paths["openapi"]), "--ai-results", str(tmp_path / "results"),
            "--alluredir", str(tmp_path / "allure"), "--html", str(tmp_path / "report.html"), "--self-contained-html"]
    if not missing_review:
        args += ["--ai-review", str(paths["review"])]
    script = "import requests, pytest\nfrom tests.support.asgi_coupon_session import ASGICouponSession\nrequests.Session = ASGICouponSession\n"
    if fail_state:
        script += "original=ASGICouponSession.request\ndef wrong(self, method, url, **kwargs):\n r=original(self,method,url,**kwargs)\n if method=='GET' and not url.endswith('/docs'):\n  import json\n  data=r.json();data['status']='claimed';r._content=json.dumps(data).encode()\n return r\nASGICouponSession.request=wrong\n"
    if fail_cleanup:
        script += "from tests.support.data_cleanup import CouponDataCleanup\ncleanup=CouponDataCleanup.cleanup\ndef broken_cleanup(self):\n cleanup(self)\n raise RuntimeError('simulated cleanup failure')\nCouponDataCleanup.cleanup=broken_cleanup\n"
    script += "raise SystemExit(pytest.main(" + repr(args) + "))"
    env = os.environ.copy()
    env["COUPON_DB_PATH"] = str(tmp_path / "isolated.db")
    env["COUPON_API_URL"] = url or "http://127.0.0.1:8000"
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def test_parameterized_execution_reports_and_cleans_real_api_data_twice(tmp_path):
    paths = prepared(tmp_path)
    for _ in range(2):
        result = invoke(tmp_path, paths)
        assert result.returncode == 0, result.stdout + result.stderr
    results = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("*.json")]
    assert len(results) == 2 and all(r["status"] == "passed" for r in results)
    assert results[0]["execution_run_id"] != results[1]["execution_run_id"]
    assert all(r["source_case_id"] == "order_non_stacking" for r in results)
    assert (tmp_path / "report.html").exists()
    assert list((tmp_path / "allure").glob("*-attachment.json"))
    with sqlite3.connect(tmp_path / "isolated.db") as db:
        assert db.execute("SELECT COUNT(*) FROM coupons").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM user_coupons").fetchone()[0] == 0


def test_unreviewed_file_is_rejected_before_api_fixture_or_database_creation(tmp_path):
    paths = prepared(tmp_path)
    result = invoke(tmp_path, paths, missing_review=True)
    assert result.returncode != 0
    assert "review_required" in result.stdout + result.stderr
    assert not (tmp_path / "isolated.db").exists()
    reports = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("*.json")]
    assert reports[0]["code"] == "review_required"
    assert reports[0]["input_path"] == str(paths["execution"])
    assert reports[0]["generation_run_id"] == json.loads(paths["execution"].read_text())["source"]["metadata"]["run_id"]
    assert reports[0]["input_digest"] is not None
    assert reports[0]["trace_verified"] is False


def test_forbidden_target_is_rejected_before_readiness_request(tmp_path):
    paths = prepared(tmp_path)
    result = invoke(tmp_path, paths, url="https://example.test")
    assert result.returncode != 0
    assert "target_forbidden" in result.stdout + result.stderr
    assert not (tmp_path / "isolated.db").exists()


def test_failed_assertion_gets_existing_exchange_attachments(tmp_path):
    paths = prepared(tmp_path)
    result = invoke(tmp_path, paths, fail_state=True)
    assert result.returncode == 1, result.stdout + result.stderr
    records = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("*.json")]
    assert records[0]["status"] == "assertion_failed"
    assert records[0]["steps"][-1]["step_id"] == "state1"
    attachments = [json.loads(p.read_text()) for p in (tmp_path / "allure").glob("*-attachment.json")]
    assert any("request" in a and "response" in a for a in attachments)


def test_teardown_error_updates_previously_passed_execution_result(tmp_path):
    paths = prepared(tmp_path)
    result = invoke(tmp_path, paths, fail_cleanup=True)
    assert result.returncode == 1, result.stdout + result.stderr
    records = [json.loads(p.read_text()) for p in (tmp_path / "results").glob("*.json")]
    assert len(records) == 1
    assert records[0]["status"] == "execution_error"
    assert records[0]["code"] == "cleanup_failed"
    attachments = [json.loads(p.read_text()) for p in (tmp_path / "allure").glob("*-attachment.json")]
    assert any(a.get("code") == "cleanup_failed" for a in attachments if isinstance(a, dict))
