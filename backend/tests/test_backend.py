from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.providers import ModelProvider, strip_code_fence
from app.runner import DockerTestRunner, analyze_test_failure
from app.repair import compute_unified_diff, run_autonomous_repair


# --- Unit Tests: Output Parsing & Failure Analysis ---

def test_strip_code_fence_variants():
    # 1. Clean markdown fence
    raw1 = "```python\ndef add(a, b):\n    return a + b\n```"
    assert strip_code_fence(raw1) == "def add(a, b):\n    return a + b"

    # 2. Conversational preamble and trailing text
    raw2 = "Here is the fixed function:\n```python\ndef add(a, b):\n    return a + b\n```\nHope this helps!"
    assert strip_code_fence(raw2) == "def add(a, b):\n    return a + b"

    # 3. Plain code without fences
    raw3 = "def add(a, b):\n    return a + b"
    assert strip_code_fence(raw3) == "def add(a, b):\n    return a + b"

    # 4. Unclosed fence
    raw4 = "```python\ndef add(a, b):\n    return a + b\n"
    assert "def add(a, b):" in strip_code_fence(raw4)


def test_analyze_test_failure_traceback():
    sample_pytest_out = """
=================================== FAILURES ===================================
________________________________ test_discount _________________________________
    def test_discount():
>       assert apply_discount(100, 120) == 0
E       assert -20 == 0
test_proofpatch.py:4: AssertionError
=========================== short test summary info ============================
FAILED test_proofpatch.py::test_discount - assert -20 == 0
1 failed in 0.05s
"""
    analysis = analyze_test_failure(sample_pytest_out, "", 1)
    assert "AssertionError" in analysis or "assert -20 == 0" in analysis
    assert "test_discount" in analysis


def test_compute_unified_diff():
    orig = "def foo():\n    return 1\n"
    prop = "def foo():\n    return 2\n"
    diff = compute_unified_diff(orig, prop, "main.py")
    assert "-    return 1" in diff
    assert "+    return 2" in diff


# --- Mock Classes for Repair Loop Isolation ---

class MockModelProvider(ModelProvider):
    def __init__(self, responses: list[str] | None = None, should_fail: bool = False):
        self.responses = responses or ["def apply_discount(p, d):\n    return max(0, p - (p * d / 100))\n"]
        self.call_count = 0
        self.should_fail = should_fail
        self.patch_calls: list[dict] = []
        self.repair_calls: list[dict] = []

    async def get_status(self):
        return {"configured_model": "mock-model", "models": ["mock-model"], "connected": True}

    async def generate_patch(self, filename: str, source: str, bug: str) -> str:
        self.patch_calls.append({"filename": filename, "source": source, "bug": bug})
        if self.should_fail:
            raise HTTPException(status_code=503, detail="Mock model connection failure")
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return resp

    async def generate_repair(self, filename: str, source: str, bug: str, current_patch: str, failure_log: str, attempt: int) -> str:
        self.repair_calls.append({
            "filename": filename,
            "source": source,
            "bug": bug,
            "current_patch": current_patch,
            "failure_log": failure_log,
            "attempt": attempt,
        })
        if self.should_fail:
            raise HTTPException(status_code=503, detail="Mock model connection failure")
        resp = self.responses[min(self.call_count, len(self.responses) - 1)]
        self.call_count += 1
        return resp


class MockTestRunner:
    def __init__(self, outcomes: list[dict]):
        self.outcomes = outcomes
        self.call_count = 0
        self.calls: list[dict] = []

    def check_availability(self):
        return True, "Mock runner ready"

    def run_tests(self, filename: str, source: str, test_source: str, test_command: str = "pytest -q", timeout_seconds: int = 45):
        self.calls.append({
            "filename": filename,
            "source": source,
            "test_source": test_source,
            "test_command": test_command,
        })
        outcome = self.outcomes[min(self.call_count, len(self.outcomes) - 1)]
        self.call_count += 1
        if outcome.get("raise_exception"):
            raise HTTPException(status_code=500, detail="Mock Docker runner crashed")
        return outcome


# --- Repair Loop Bounded Invariants Tests ---

@pytest.mark.asyncio
async def test_repair_loop_stops_on_first_pass():
    provider = MockModelProvider(responses=["def foo(): return 1\n"])
    runner = MockTestRunner(outcomes=[
        {"passed": True, "exit_code": 0, "stdout": "1 passed in 0.01s", "stderr": "", "failure_analysis": "", "note": "Passed"}
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="from main import foo\ndef test(): assert foo() == 1\n",
        bug="Return 1 instead of 0",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    assert result.verified is True
    assert result.final_status == "VERIFIED_PASSED"
    assert result.total_attempts == 1
    assert len(result.attempts) == 1
    assert result.attempts[0].passed is True


@pytest.mark.asyncio
async def test_repair_loop_budget_exhaustion_at_three():
    provider = MockModelProvider(responses=[
        "def foo(): return -1\n",
        "def foo(): return -2\n",
        "def foo(): return -3\n",
    ])
    runner = MockTestRunner(outcomes=[
        {"passed": False, "exit_code": 1, "stdout": "assert -1 == 1", "stderr": "", "failure_analysis": "AssertionError", "note": "Failed"},
        {"passed": False, "exit_code": 1, "stdout": "assert -2 == 1", "stderr": "", "failure_analysis": "AssertionError", "note": "Failed"},
        {"passed": False, "exit_code": 1, "stdout": "assert -3 == 1", "stderr": "", "failure_analysis": "AssertionError", "note": "Failed"},
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="from main import foo\ndef test(): assert foo() == 1\n",
        bug="Return 1 instead of 0",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    assert result.verified is False
    assert result.final_status == "REPAIR_BUDGET_EXHAUSTED"
    assert result.total_attempts == 3
    assert len(result.attempts) == 3
    assert all(not a.passed for a in result.attempts)


@pytest.mark.asyncio
async def test_repair_loop_passes_on_second_attempt():
    provider = MockModelProvider(responses=[
        "def foo(): return 0\n",  # Attempt 1 fails
        "def foo(): return 1\n",  # Attempt 2 passes
    ])
    runner = MockTestRunner(outcomes=[
        {"passed": False, "exit_code": 1, "stdout": "assert 0 == 1", "stderr": "", "failure_analysis": "assert 0 == 1", "note": "Failed"},
        {"passed": True, "exit_code": 0, "stdout": "1 passed in 0.02s", "stderr": "", "failure_analysis": "", "note": "Passed"},
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="from main import foo\ndef test(): assert foo() == 1\n",
        bug="Return 1",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    assert result.verified is True
    assert result.final_status == "VERIFIED_PASSED"
    assert result.total_attempts == 2
    assert len(result.attempts) == 2
    assert result.attempts[0].passed is False
    assert result.attempts[1].passed is True


@pytest.mark.asyncio
async def test_repair_loop_handles_model_error():
    provider = MockModelProvider(should_fail=True)
    runner = MockTestRunner(outcomes=[])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="from main import foo\ndef test(): assert foo() == 1\n",
        bug="Fix bug",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    assert result.verified is False
    assert result.final_status == "MODEL_ERROR"
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "ERROR"


@pytest.mark.asyncio
async def test_repair_loop_handles_docker_error():
    provider = MockModelProvider(responses=["def foo(): return 1\n"])
    runner = MockTestRunner(outcomes=[{"raise_exception": True}])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="from main import foo\ndef test(): assert foo() == 1\n",
        bug="Fix bug",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    assert result.verified is False
    assert result.final_status == "DOCKER_ERROR"
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "ERROR"


# --- Deterministic Autonomous Repair Regression Tests ---

@pytest.mark.asyncio
async def test_deterministic_repair_retry_with_failure_evidence_in_second_request():
    """Verify that a test failure triggers a genuine retry containing the failure logs and traceback."""
    bug = "When the discount is greater than 100%, the total becomes negative. Clamp the discount so the final price cannot be below zero."
    orig_source = (
        "def apply_discount(price: float, discount_percent: float) -> float:\n"
        "    discount = price * discount_percent / 100\n"
        "    return price - discount\n"
    )
    test_fixture = (
        "from main import apply_discount\n\n"
        "def test_discount_cannot_make_price_negative():\n"
        "    assert apply_discount(100, 120) == 0\n\n"
        "def test_normal_discount():\n"
        "    assert apply_discount(100, 20) == 80\n"
    )

    # Patch 1 is intentionally incomplete (rounds discount but still produces negative price)
    patch_1_incomplete = (
        "def apply_discount(price: float, discount_percent: float) -> float:\n"
        "    discount = round(price * discount_percent / 100, 2)\n"
        "    return price - discount\n"
    )

    # Patch 2 is the correct fix (clamps price to >= 0)
    patch_2_correct = (
        "def apply_discount(price: float, discount_percent: float) -> float:\n"
        "    discount = price * discount_percent / 100\n"
        "    return max(0.0, price - discount)\n"
    )

    # Realistic pytest failure output with traceback and exit code 1
    failure_traceback_stdout = """
=================================== FAILURES ===================================
_________________ test_discount_cannot_make_price_negative _________________
    def test_discount_cannot_make_price_negative():
>       assert apply_discount(100, 120) == 0
E       assert -20.0 == 0
test_proofpatch.py:4: AssertionError
=========================== short test summary info ============================
FAILED test_proofpatch.py::test_discount_cannot_make_price_negative - assert -20.0 == 0
1 failed, 1 passed in 0.04s
"""
    failure_analysis = "Test 'test_discount_cannot_make_price_negative' failed:\nassert -20.0 == 0\nAssertionError"

    # Realistic pytest passing output with exit code 0
    success_stdout = """
..                                                                       [100%]
2 passed in 0.02s
"""

    provider = MockModelProvider(responses=[patch_1_incomplete, patch_2_correct])
    runner = MockTestRunner(outcomes=[
        {
            "passed": False,
            "exit_code": 1,
            "stdout": failure_traceback_stdout,
            "stderr": "",
            "failure_analysis": failure_analysis,
            "note": "Test suite failed.",
        },
        {
            "passed": True,
            "exit_code": 0,
            "stdout": success_stdout,
            "stderr": "",
            "failure_analysis": "",
            "note": "Test suite passed completely in the isolated sandbox.",
        },
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source=orig_source,
        test_source=test_fixture,
        bug=bug,
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    # 1. Assert exactly two attempts occurred
    assert result.total_attempts == 2
    assert len(result.attempts) == 2

    # 2. Assert attempt 1 failed with realistic nonzero exit code and failure traceback
    att1 = result.attempts[0]
    assert att1.attempt == 1
    assert att1.passed is False
    assert att1.exit_code == 1
    assert att1.status == "FAILED"
    assert "assert -20.0 == 0" in att1.stdout
    assert "AssertionError" in att1.stdout
    assert att1.proposed_source == patch_1_incomplete

    # 3. Assert failure output was fed into the second model request
    assert len(provider.repair_calls) == 1
    repair_req = provider.repair_calls[0]
    assert repair_req["attempt"] == 2
    assert repair_req["current_patch"] == patch_1_incomplete
    assert "assert -20.0 == 0" in repair_req["failure_log"]
    assert "AssertionError" in repair_req["failure_log"]

    # 4. Assert fresh test fixture and latest patch were executed in runner without caching
    assert len(runner.calls) == 2
    assert runner.calls[0]["source"] == patch_1_incomplete
    assert runner.calls[1]["source"] == patch_2_correct
    assert runner.calls[0]["test_source"] == test_fixture
    assert runner.calls[1]["test_source"] == test_fixture

    # 5. Assert attempt 2 passed with exit code 0
    att2 = result.attempts[1]
    assert att2.attempt == 2
    assert att2.passed is True
    assert att2.exit_code == 0
    assert att2.status == "PASSED"
    assert "2 passed" in att2.stdout
    assert att2.proposed_source == patch_2_correct

    # 6. Assert final verified status is True
    assert result.verified is True
    assert result.final_status == "VERIFIED_PASSED"
    assert result.final_source == patch_2_correct


@pytest.mark.asyncio
async def test_deterministic_three_attempt_budget_exhaustion_with_evidence_chain():
    """Verify that when all three attempts fail, the loop halts at exactly 3 attempts and is not verified."""
    bug = "Clamp price so it never becomes negative"
    orig_source = "def apply_discount(p, d): return p - d\n"
    test_fixture = "from main import apply_discount\ndef test(): assert apply_discount(100, 150) == 0\n"

    patch1 = "def apply_discount(p, d): return p - d - 1\n"
    patch2 = "def apply_discount(p, d): return p - d - 2\n"
    patch3 = "def apply_discount(p, d): return p - d - 3\n"

    provider = MockModelProvider(responses=[patch1, patch2, patch3])
    runner = MockTestRunner(outcomes=[
        {"passed": False, "exit_code": 1, "stdout": "AssertionError: assert -51 == 0", "stderr": "", "failure_analysis": "assert -51 == 0", "note": "Failed"},
        {"passed": False, "exit_code": 1, "stdout": "AssertionError: assert -52 == 0", "stderr": "", "failure_analysis": "assert -52 == 0", "note": "Failed"},
        {"passed": False, "exit_code": 1, "stdout": "AssertionError: assert -53 == 0", "stderr": "", "failure_analysis": "assert -53 == 0", "note": "Failed"},
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source=orig_source,
        test_source=test_fixture,
        bug=bug,
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=3,
    )

    # 1. Assert loop stopped after exactly 3 attempts
    assert result.total_attempts == 3
    assert len(result.attempts) == 3

    # 2. Assert not verified
    assert result.verified is False
    assert result.final_status == "REPAIR_BUDGET_EXHAUSTED"
    assert all(a.passed is False for a in result.attempts)
    assert all(a.exit_code == 1 for a in result.attempts)

    # 3. Assert evidence chain: Attempt 2 got failure 1, Attempt 3 got failure 2
    assert len(provider.repair_calls) == 2
    assert "assert -51 == 0" in provider.repair_calls[0]["failure_log"]
    assert "assert -52 == 0" in provider.repair_calls[1]["failure_log"]

    # 4. Assert each runner call received the latest proposed source
    assert len(runner.calls) == 3
    assert runner.calls[0]["source"] == patch1
    assert runner.calls[1]["source"] == patch2
    assert runner.calls[2]["source"] == patch3


@pytest.mark.asyncio
async def test_verified_status_strictly_requires_zero_exit_code():
    """Invariant test: even if passed flag is True, nonzero exit code must block verified status."""
    provider = MockModelProvider(responses=["def foo(): return 1\n"])
    runner = MockTestRunner(outcomes=[
        # Inconsistent outcome: passed=True but exit_code=1
        {"passed": True, "exit_code": 1, "stdout": "Error occurred", "stderr": "", "failure_analysis": "", "note": "Inconsistent"}
    ])

    result = await run_autonomous_repair(
        filename="main.py",
        source="def foo(): return 0\n",
        test_source="def test(): assert 1 == 1\n",
        bug="Fix bug",
        test_command="pytest -q",
        provider=provider,
        runner=runner,
        max_attempts=1,
    )

    # Must NOT be verified because exit_code != 0
    assert result.verified is False
    assert result.attempts[0].passed is False
    assert result.final_status != "VERIFIED_PASSED"


# --- Real Isolated Docker Runner Integration Tests ---

def test_docker_runner_real_pass():
    runner = DockerTestRunner()
    available, msg = runner.check_availability()
    if not available:
        pytest.skip(f"Docker not available: {msg}")

    code = "def apply_discount(p, d):\n    return max(0, p - (p * d / 100))\n"
    tests = "from main import apply_discount\ndef test_ok():\n    assert apply_discount(100, 20) == 80\n    assert apply_discount(100, 150) == 0\n"
    res = runner.run_tests("main.py", code, tests, "pytest -q")

    assert res["passed"] is True
    assert res["exit_code"] == 0
    assert "passed" in res["stdout"]


def test_docker_runner_real_fail_with_evidence():
    runner = DockerTestRunner()
    available, msg = runner.check_availability()
    if not available:
        pytest.skip(f"Docker not available: {msg}")

    code = "def apply_discount(p, d):\n    return p - (p * d / 100)\n"
    tests = "from main import apply_discount\ndef test_fail():\n    assert apply_discount(100, 150) == 0\n"
    res = runner.run_tests("main.py", code, tests, "pytest -q")

    assert res["passed"] is False
    assert res["exit_code"] != 0
    assert "assert -50.0 == 0" in res["stdout"] or "assert -50 == 0" in res["stdout"]
    assert res["failure_analysis"] != ""


# --- API Endpoint Integration Tests ---

def test_api_health_endpoint():
    client = TestClient(app)
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "docker_available" in data
