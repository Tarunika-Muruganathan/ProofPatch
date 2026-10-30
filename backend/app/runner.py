from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from fastapi import HTTPException

DOCKER_IMAGE = os.getenv("PROOFPATCH_RUNNER_IMAGE", "proofpatch-runner:local")


def find_docker_executable() -> Optional[str]:
    """Locate the docker binary on the host system, checking standard Windows paths if needed."""
    candidate = shutil.which("docker")
    if candidate:
        return candidate

    common_paths = [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe"),
        r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"),
    ]
    for p in common_paths:
        if os.path.isfile(p):
            return p
    return None


def analyze_test_failure(stdout: str, stderr: str, exit_code: int) -> str:
    """Extract an actionable summary from test failure output for the model."""
    combined = f"{stdout}\n{stderr}".strip()
    if not combined:
        return f"Process exited with non-zero code {exit_code} and empty output."

    summary_lines = []
    # Check for syntax errors
    syntax_match = re.search(r"(SyntaxError:.*)", combined)
    if syntax_match:
        summary_lines.append(f"Syntax Error: {syntax_match.group(1)}")

    # Check for IndentationError
    indent_match = re.search(r"(IndentationError:.*)", combined)
    if indent_match:
        summary_lines.append(f"Indentation Error: {indent_match.group(1)}")

    # Check for NameError, TypeError, AttributeError, KeyError, ValueError
    for exc in ["NameError", "TypeError", "AttributeError", "KeyError", "ValueError", "ZeroDivisionError", "IndexError"]:
        match = re.search(rf"({exc}:.*)", combined)
        if match:
            summary_lines.append(match.group(1))

    # Look for pytest FAILURES section
    fail_hunk = re.search(r"_{3,}\s*(.+?)\s*_{3,}(.*?)(?:_{3,}|$)", combined, flags=re.DOTALL)
    if fail_hunk:
        test_name = fail_hunk.group(1).strip()
        details = fail_hunk.group(2).strip()
        # Find assertion failure lines
        assert_lines = [l.strip() for l in details.splitlines() if l.strip().startswith(("assert", "E   ", ">   "))]
        summary_lines.append(f"Test '{test_name}' failed:")
        summary_lines.extend(assert_lines[-4:])

    # Look for pytest short test summary info
    short_summary = re.search(r"=+ short test summary info =+(.*?)(?:=|$)", combined, flags=re.DOTALL)
    if short_summary:
        summary_lines.append(short_summary.group(1).strip())

    if summary_lines:
        return "\n".join(summary_lines)

    # Fallback to last 15 lines of output
    lines = [l for l in combined.splitlines() if l.strip()]
    return "\n".join(lines[-15:])


class DockerTestRunner:
    """Runs tests inside an isolated, non-networked Docker container with strict resource limits."""

    def __init__(self, image: Optional[str] = None):
        self.image = image or DOCKER_IMAGE
        self.docker_bin = find_docker_executable()

    def check_availability(self) -> Tuple[bool, str]:
        if not self.docker_bin:
            return False, "Docker executable not found on host. Ensure Docker Desktop is installed and running."
        try:
            res = subprocess.run(
                [self.docker_bin, "image", "inspect", self.image],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if res.returncode != 0:
                return False, f"Runner image '{self.image}' is missing. Build it with: docker build -t {self.image} -f docker/Dockerfile.runner docker"
            return True, "Docker runner is available and healthy."
        except subprocess.TimeoutExpired:
            return False, "Docker daemon timed out responding to image inspection."
        except Exception as exc:
            return False, f"Docker check failed: {str(exc)}"

    def run_tests(
        self,
        filename: str,
        source: str,
        test_source: str,
        test_command: str = "pytest -q",
        timeout_seconds: int = 45,
    ) -> Dict[str, Any]:
        """Execute tests in isolated container. Host execution is strictly forbidden."""
        available, reason = self.check_availability()
        if not available:
            raise HTTPException(status_code=503, detail=reason)

        with tempfile.TemporaryDirectory(prefix="proofpatch-") as tmp:
            root = Path(tmp)
            (root / filename).write_text(source, encoding="utf-8")
            if test_source.strip():
                (root / "test_proofpatch.py").write_text(test_source, encoding="utf-8")

            # Format Windows paths with forward slashes for Docker volume mount
            mount_path = str(root.resolve()).replace("\\", "/")

            # Build isolated Docker command
            cmd = [
                self.docker_bin,
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "512m",
                "--cpus",
                "1",
                "--pids-limit",
                "128",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=64m",
                "-v",
                f"{mount_path}:/workspace:rw",
                "-w",
                "/workspace",
                self.image,
            ]

            # Send command as pure UTF-8 bytes with \n to avoid Windows CRLF issues
            stdin_payload = f"{test_command.strip()}\n".encode("utf-8")

            try:
                result = subprocess.run(
                    cmd,
                    input=stdin_payload,
                    capture_output=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(
                    status_code=408,
                    detail=f"Tests exceeded the {timeout_seconds}-second execution timeout in the sandbox.",
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=500,
                    detail=f"Sandbox execution error: {str(exc)}",
                )

            stdout = result.stdout.decode("utf-8", errors="replace")[-12000:]
            stderr = result.stderr.decode("utf-8", errors="replace")[-12000:]
            passed = (result.returncode == 0)
            failure_analysis = "" if passed else analyze_test_failure(stdout, stderr, result.returncode)

            return {
                "passed": passed,
                "exit_code": result.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "command": test_command,
                "sandbox": "Docker container; network disabled; capabilities dropped; resource limits applied",
                "failure_analysis": failure_analysis,
                "note": (
                    "Test suite passed completely in the isolated sandbox."
                    if passed
                    else "Test suite failed. See failure analysis and logs for evidence."
                ),
            }
