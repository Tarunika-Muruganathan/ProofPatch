from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.providers import ModelProvider, OllamaProvider
from app.runner import DockerTestRunner
from app.repair import (
    RepairSessionResult,
    compute_unified_diff,
    run_autonomous_repair,
)

MAX_SOURCE_CHARS = 60_000
MAX_BUG_CHARS = 5_000

app = FastAPI(title="ProofPatch API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

# Providers and runners
model_provider: ModelProvider = OllamaProvider()
docker_runner = DockerTestRunner()


class GenerateRequest(BaseModel):
    filename: str = Field(default="main.py", min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    bug: str = Field(min_length=3, max_length=MAX_BUG_CHARS)


class GenerateResponse(BaseModel):
    filename: str
    proposed_source: str
    diff: str
    model: str
    notes: str


class TestRequest(BaseModel):
    filename: str = Field(default="main.py", min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    test_source: str = Field(default="", max_length=MAX_SOURCE_CHARS)
    test_command: Literal["pytest -q", "python -m pytest -q", "python -m unittest"] = "pytest -q"


class RepairRequest(BaseModel):
    filename: str = Field(default="main.py", min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=MAX_SOURCE_CHARS)
    test_source: str = Field(default="", max_length=MAX_SOURCE_CHARS)
    bug: str = Field(min_length=3, max_length=MAX_BUG_CHARS)
    test_command: Literal["pytest -q", "python -m pytest -q", "python -m unittest"] = "pytest -q"
    max_attempts: int = Field(default=3, ge=1, le=3)


def validate_filename(filename: str) -> str:
    # A single basename only; no paths or traversal.
    if Path(filename).name != filename or not re.fullmatch(r"[A-Za-z0-9_.-]+\.py", filename):
        raise HTTPException(status_code=400, detail="Use a simple Python filename such as main.py.")
    return filename


@app.get("/api/health")
async def health():
    status = await model_provider.get_status()
    docker_ok, docker_msg = docker_runner.check_availability()
    return {
        "status": "ok",
        "model": status.get("configured_model", "unknown"),
        "model_connected": status.get("connected", False),
        "docker_available": docker_ok,
        "docker_status": docker_msg,
    }


@app.get("/api/models")
async def models():
    return await model_provider.get_status()


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    filename = validate_filename(req.filename)
    proposed = await model_provider.generate_patch(filename=filename, source=req.source, bug=req.bug)
    if len(proposed) > MAX_SOURCE_CHARS * 1.5:
        raise HTTPException(status_code=502, detail="The generated replacement is too large.")

    diff = compute_unified_diff(req.source, proposed, filename)
    status = await model_provider.get_status()
    return GenerateResponse(
        filename=filename,
        proposed_source=proposed,
        diff=diff,
        model=status.get("configured_model", "qwen2.5-coder:3b"),
        notes="Model-generated proposal. Review the diff before trusting or applying it.",
    )


@app.post("/api/test")
async def run_tests(req: TestRequest):
    filename = validate_filename(req.filename)
    return docker_runner.run_tests(
        filename=filename,
        source=req.source,
        test_source=req.test_source,
        test_command=req.test_command,
    )


@app.post("/api/repair", response_model=RepairSessionResult)
async def repair_loop(req: RepairRequest):
    filename = validate_filename(req.filename)
    return await run_autonomous_repair(
        filename=filename,
        source=req.source,
        test_source=req.test_source,
        bug=req.bug,
        test_command=req.test_command,
        provider=model_provider,
        runner=docker_runner,
        max_attempts=req.max_attempts,
    )
