from __future__ import annotations

import abc
import os
import re
from typing import Any, Dict, List, Optional
import httpx
from fastapi import HTTPException


def strip_code_fence(text: str) -> str:
    """Robustly extracts python source code from model output.
    
    Handles markdown code blocks embedded within prose, conversational text,
    unmatched fences, and plain source code.
    """
    text = text.strip()
    if not text:
        return ""

    # Look for fenced python code blocks first
    fence_pattern = re.compile(r"```(?:python)?\s*\n(.*?)\n```", flags=re.DOTALL | re.IGNORECASE)
    matches = fence_pattern.findall(text)
    if matches:
        # Return the longest code block found (to avoid snippets or tiny examples)
        return max(matches, key=len).strip()

    # Look for an unclosed fence (e.g. ```python\n... at EOF)
    unclosed_match = re.search(r"```(?:python)?\s*\n(.*)$", text, flags=re.DOTALL | re.IGNORECASE)
    if unclosed_match:
        return unclosed_match.group(1).strip()

    # If the text itself has no fences, remove leading conversational intro if present
    lines = text.splitlines()
    code_lines = []
    in_code = False
    for line in lines:
        if line.startswith(("def ", "class ", "import ", "from ", "#", "if ", "try:", "with ", "return ", "async ")):
            in_code = True
        if in_code:
            code_lines.append(line)

    return "\n".join(code_lines).strip() if code_lines else text


class ModelProvider(abc.ABC):
    """Abstract provider interface to decouple model inference from specific providers."""

    @abc.abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Check provider health and list available models."""
        pass

    @abc.abstractmethod
    async def generate_patch(self, filename: str, source: str, bug: str) -> str:
        """Generate an initial replacement patch for a bug."""
        pass

    @abc.abstractmethod
    async def generate_repair(
        self,
        filename: str,
        source: str,
        bug: str,
        current_patch: str,
        failure_log: str,
        attempt: int,
    ) -> str:
        """Generate a revised patch based on test failure evidence."""
        pass


class OllamaProvider(ModelProvider):
    """Local Ollama model provider with robust error handling and timeout controls."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: float = 180.0,
    ):
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5-coder:3b")
        self.timeout_seconds = timeout_seconds

    async def get_status(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(f"{self.base_url}/api/tags")
                res.raise_for_status()
                data = res.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            return {"configured_model": self.model, "models": models, "connected": True}
        except Exception:
            return {
                "configured_model": self.model,
                "models": [],
                "connected": False,
                "error": "Ollama is not reachable. Start Ollama and pull the configured model.",
            }

    async def _call_ollama(self, prompt: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds)) as client:
                res = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1},
                    },
                )
                res.raise_for_status()
                payload = res.json()
            raw_response = payload.get("response", "")
            proposed = strip_code_fence(raw_response)
            if not proposed.strip():
                raise HTTPException(status_code=502, detail="The model returned an empty patch.")
            return proposed
        except HTTPException:
            raise
        except httpx.ConnectError:
            raise HTTPException(
                status_code=503,
                detail="Cannot connect to Ollama. Start Ollama and ensure the model is installed.",
            )
        except httpx.TimeoutException:
            raise HTTPException(
                status_code=504,
                detail="Ollama took too long to respond. Try a smaller model or shorter source file.",
            )
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Ollama returned HTTP {exc.response.status_code}. Check the model name.",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Ollama error: {str(exc)}")

    async def generate_patch(self, filename: str, source: str, bug: str) -> str:
        prompt = f"""You are ProofPatch, a careful Python bug-fixing agent.
Analyze the bug report and source code. Return ONLY the complete replacement Python source code.
Do not use Markdown fences. Do not include explanations outside the code.
Preserve existing behavior unless it conflicts with the bug fix. Do not add network calls,
subprocess execution, file deletion, credential access, or unrelated functionality.
If information is insufficient, make the smallest reasonable fix.

BUG REPORT:
{bug}

CURRENT FILE ({filename}):
```python
{source}
```

Return the complete replacement source file now."""
        return await self._call_ollama(prompt)

    async def generate_repair(
        self,
        filename: str,
        source: str,
        bug: str,
        current_patch: str,
        failure_log: str,
        attempt: int,
    ) -> str:
        prompt = f"""You are ProofPatch, an autonomous evidence-driven Python repair agent.
Your previous patch failed the test suite in our isolated test runner.
This is repair attempt #{attempt} of 3.

Carefully examine the failure logs, tracebacks, and assertion errors below.
Identify precisely why the patch failed, and fix the implementation so the tests will pass.
Return ONLY the complete replacement Python source code.
Do not use Markdown fences. Do not include explanations outside the code.
Preserve existing behavior and tests.

BUG REPORT:
{bug}

ORIGINAL SOURCE FILE ({filename}):
```python
{source}
```

FAILED PROPOSED PATCH:
```python
{current_patch}
```

TEST RUNNER FAILURE EVIDENCE (STDOUT & STDERR):
```text
{failure_log}
```

Return the complete revised replacement source file now."""
        return await self._call_ollama(prompt)
