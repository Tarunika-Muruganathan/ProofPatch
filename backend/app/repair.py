from __future__ import annotations

import difflib
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.providers import ModelProvider
from app.runner import DockerTestRunner


def compute_unified_diff(original: str, proposed: str, filename: str) -> str:
    """Generate a clean unified diff between original and proposed code."""
    orig_lines = original.splitlines(keepends=True)
    prop_lines = (proposed + ("" if proposed.endswith("\n") else "\n")).splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            orig_lines,
            prop_lines,
            fromfile=f"original/{filename}",
            tofile=f"proposed/{filename}",
        )
    )
    return diff or "# No textual changes generated."


class RepairAttempt(BaseModel):
    attempt: int
    proposed_source: str
    diff: str
    passed: bool
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    failure_analysis: str = ""
    status: str  # "PASSED", "FAILED", "ERROR"
    note: str = ""


class RepairSessionResult(BaseModel):
    filename: str
    verified: bool
    final_status: str  # "VERIFIED_PASSED", "REPAIR_BUDGET_EXHAUSTED", "MODEL_ERROR", "DOCKER_ERROR"
    total_attempts: int
    attempts: List[RepairAttempt]
    final_source: str
    final_diff: str
    model: str
    summary: str


async def run_autonomous_repair(
    filename: str,
    source: str,
    test_source: str,
    bug: str,
    test_command: str,
    provider: ModelProvider,
    runner: DockerTestRunner,
    max_attempts: int = 3,
) -> RepairSessionResult:
    """Execute the evidence-gated autonomous repair loop bounded to max_attempts.
    
    Invariants:
    1. Stops immediately when tests pass.
    2. Bounded to a maximum of 3 attempts.
    3. Never marks verified=True unless tests actually executed with exit_code == 0.
    4. Records complete evidence (stdout, stderr, diffs) for every attempt.
    """
    attempts: List[RepairAttempt] = []
    current_source = source
    current_patch = ""
    last_failure_log = ""
    provider_status = await provider.get_status()
    model_name = provider_status.get("configured_model", "unknown")

    for attempt_idx in range(1, max_attempts + 1):
        # 1. Generate patch
        try:
            if attempt_idx == 1:
                current_patch = await provider.generate_patch(filename=filename, source=source, bug=bug)
            else:
                current_patch = await provider.generate_repair(
                    filename=filename,
                    source=source,
                    bug=bug,
                    current_patch=current_patch,
                    failure_log=last_failure_log,
                    attempt=attempt_idx,
                )
        except Exception as exc:
            # Model inference error
            attempt_record = RepairAttempt(
                attempt=attempt_idx,
                proposed_source=current_patch or source,
                diff="# Model generation failed.",
                passed=False,
                exit_code=None,
                status="ERROR",
                note=f"Model error during attempt {attempt_idx}: {str(exc)}",
            )
            attempts.append(attempt_record)
            return RepairSessionResult(
                filename=filename,
                verified=False,
                final_status="MODEL_ERROR",
                total_attempts=attempt_idx,
                attempts=attempts,
                final_source=current_patch or source,
                final_diff=attempt_record.diff,
                model=model_name,
                summary=f"Repair loop halted due to model error on attempt {attempt_idx}: {str(exc)}",
            )

        diff = compute_unified_diff(source, current_patch, filename)

        # 2. Run isolated Docker tests
        try:
            test_res = runner.run_tests(
                filename=filename,
                source=current_patch,
                test_source=test_source,
                test_command=test_command,
            )
        except Exception as exc:
            # Docker/runner error
            attempt_record = RepairAttempt(
                attempt=attempt_idx,
                proposed_source=current_patch,
                diff=diff,
                passed=False,
                exit_code=None,
                status="ERROR",
                note=f"Docker runner error: {str(exc)}",
            )
            attempts.append(attempt_record)
            return RepairSessionResult(
                filename=filename,
                verified=False,
                final_status="DOCKER_ERROR",
                total_attempts=attempt_idx,
                attempts=attempts,
                final_source=current_patch,
                final_diff=diff,
                model=model_name,
                summary=f"Repair loop halted due to test runner error: {str(exc)}",
            )

        # Invariant: A test run is only passed if passed is True AND exit_code is exactly 0
        passed = bool(test_res.get("passed", False)) and (test_res.get("exit_code") == 0)
        attempt_record = RepairAttempt(
            attempt=attempt_idx,
            proposed_source=current_patch,
            diff=diff,
            passed=passed,
            exit_code=test_res.get("exit_code"),
            stdout=test_res.get("stdout", ""),
            stderr=test_res.get("stderr", ""),
            failure_analysis=test_res.get("failure_analysis", ""),
            status="PASSED" if passed else "FAILED",
            note=test_res.get("note", ""),
        )
        attempts.append(attempt_record)

        # Invariant: Stop immediately when tests pass! Never verify without exit_code == 0
        if passed and test_res.get("exit_code") == 0:
            return RepairSessionResult(
                filename=filename,
                verified=True,
                final_status="VERIFIED_PASSED",
                total_attempts=attempt_idx,
                attempts=attempts,
                final_source=current_patch,
                final_diff=diff,
                model=model_name,
                summary=f"Patch verified! All tests passed on attempt {attempt_idx} of {max_attempts}.",
            )

        # If failed, prepare failure evidence for next iteration
        last_failure_log = f"{test_res.get('stdout', '')}\n{test_res.get('stderr', '')}\nAnalysis:\n{test_res.get('failure_analysis', '')}".strip()

    # Budget exhausted after max_attempts
    return RepairSessionResult(
        filename=filename,
        verified=False,
        final_status="REPAIR_BUDGET_EXHAUSTED",
        total_attempts=max_attempts,
        attempts=attempts,
        final_source=current_patch,
        final_diff=compute_unified_diff(source, current_patch, filename),
        model=model_name,
        summary=f"Repair budget exhausted ({max_attempts} attempts). Tests failed to pass.",
    )
