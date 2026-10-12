import os
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.regression


def test_pytest_explains_how_to_start_api_when_service_is_unavailable():
    environment = os.environ.copy()
    environment["COUPON_API_URL"] = "http://127.0.0.1:1"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_coupon_flow.py::test_user_cannot_claim_nonexistent_coupon",
            "--tb=no",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    output = result.stdout + result.stderr

    assert result.returncode != 0
    assert "Start the API server" in output
