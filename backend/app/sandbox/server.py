"""Sandbox-runner HTTP process. Only this role mounts docker.sock."""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.core.config import settings
from app.sandbox.runner import ExecutionResult, run_code_local

app = FastAPI(title=f"{settings.app_name}-sandbox")


class RunRequest(BaseModel):
    code: str
    data_path: str | None = None
    timeout_sec: int | None = None


class RunResponse(BaseModel):
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    artifacts: list[dict] = Field(default_factory=list)
    error: str = ""
    artifact_png_bytes: int = 0


@app.get("/health")
def health():
    return {"status": "ok", "role": "sandbox-runner"}


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    result: ExecutionResult = run_code_local(
        req.code,
        data_path=req.data_path,
        timeout_sec=req.timeout_sec,
    )
    return RunResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        timed_out=result.timed_out,
        artifacts=result.artifacts,
        error=result.error,
        artifact_png_bytes=result.artifact_png_bytes,
    )
