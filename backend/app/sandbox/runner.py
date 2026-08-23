"""
Code execution sandbox (Phase 3, PRD 3.3.3 + NFR 4.2).

Decision (ADR-004): Python code runs ONLY inside an isolated Docker container.
Hard rules:
  - no outbound network
  - no access to host filesystem beyond explicit read-only data mount
  - 30s wall-clock timeout -> force kill (PRD exception table)

Uses subprocess + docker CLI (no Docker SDK dependency).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import settings
from app.core.messages import SANDBOX_TIMEOUT_MESSAGE
from app.core.tmpdir import ephemeral_dir

_SVG_MAX_CHARS = 400_000


def _collect_chart_artifacts(out_dir: Path) -> tuple[list[dict], int]:
    """Pick up matplotlib PNG/SVG/JSON sidecars (Open WebUI-style download payloads)."""
    png = out_dir / "out.png"
    svg_path = out_dir / "out.svg"
    meta_path = out_dir / "out.json"
    png_bytes = 0
    points: list = []
    svg_text = ""
    if meta_path.is_file():
        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("points"), list):
                points = payload["points"][:200]
        except (OSError, json.JSONDecodeError, TypeError):
            points = []
    if svg_path.is_file() and svg_path.stat().st_size > 0:
        try:
            svg_text = svg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            svg_text = ""
        if len(svg_text) > _SVG_MAX_CHARS:
            svg_text = ""
    artifacts: list[dict] = []
    if png.is_file() and png.stat().st_size > 0:
        raw = png.read_bytes()
        png_bytes = len(raw)
        b64 = base64.b64encode(raw).decode("ascii")
        art: dict = {
            "kind": "image",
            "title": "分析图表",
            "language": "png",
            "image_base64": b64,
            "content": f"data:image/png;base64,{b64}",
            "png_bytes": png_bytes,
        }
        if svg_text:
            art["svg"] = svg_text
        if points:
            art["chart_points"] = points
        artifacts.append(art)
    elif svg_text:
        art = {
            "kind": "image",
            "title": "分析图表",
            "language": "svg",
            "content": svg_text,
            "svg": svg_text,
        }
        if points:
            art["chart_points"] = points
        artifacts.append(art)
    return artifacts, png_bytes


@dataclass
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    artifacts: list[dict] = field(default_factory=list)  # {kind, title, content|image_base64}
    error: str = ""
    artifact_png_bytes: int = 0


_MISSING_SANDBOX_IMAGE = (
    "未找到沙箱镜像 omni-sandbox。请在宿主机项目目录执行："
    "export DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 && docker compose build sandbox"
)


def _sandbox_image_ready() -> bool:
    image = (settings.sandbox_image or "omni-sandbox").strip()
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def docker_available() -> bool:
    if not settings.sandbox_enabled:
        return False
    runner = (settings.sandbox_runner_url or "").strip()
    if runner:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{runner.rstrip('/')}/health")
            return r.status_code == 200
        except (httpx.HTTPError, OSError):
            return False
    try:
        r = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        return r.returncode == 0 and bool((r.stdout or "").strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def run_code(
    code: str,
    *,
    data_path: str | Path | None = None,
    timeout_sec: int | None = None,
) -> ExecutionResult:
    """Run Python in omni-sandbox. Compose api delegates to sandbox-runner."""
    if not settings.sandbox_enabled:
        return ExecutionResult(stderr="sandbox disabled", error="sandbox disabled")
    runner = (settings.sandbox_runner_url or "").strip()
    if runner:
        return _run_via_http(runner, code, data_path=data_path, timeout_sec=timeout_sec)
    return run_code_local(code, data_path=data_path, timeout_sec=timeout_sec)


def _run_via_http(
    runner_url: str,
    code: str,
    *,
    data_path: str | Path | None,
    timeout_sec: int | None,
) -> ExecutionResult:
    timeout = int(timeout_sec or settings.sandbox_timeout_sec)
    payload = {
        "code": code,
        "data_path": str(Path(data_path).resolve()) if data_path else None,
        "timeout_sec": timeout,
    }
    try:
        with httpx.Client(timeout=float(timeout + 20)) as client:
            resp = client.post(f"{runner_url.rstrip('/')}/run", json=payload)
    except httpx.TimeoutException:
        return ExecutionResult(
            stderr="sandbox-runner timed out",
            timed_out=True,
            error=SANDBOX_TIMEOUT_MESSAGE,
        )
    except (httpx.HTTPError, OSError) as exc:
        return ExecutionResult(
            stderr=str(exc)[:300],
            error="沙箱服务不可达，请检查 sandbox-runner。",
        )
    if resp.status_code != 200:
        return ExecutionResult(
            stderr=resp.text[:500],
            error=f"sandbox-runner HTTP {resp.status_code}",
        )
    body = resp.json()
    return ExecutionResult(
        stdout=str(body.get("stdout") or ""),
        stderr=str(body.get("stderr") or ""),
        timed_out=bool(body.get("timed_out")),
        artifacts=list(body.get("artifacts") or []),
        error=str(body.get("error") or ""),
        artifact_png_bytes=int(body.get("artifact_png_bytes") or 0),
    )


def run_code_local(
    code: str,
    *,
    data_path: str | Path | None = None,
    timeout_sec: int | None = None,
) -> ExecutionResult:
    """Run Python via docker CLI on this host (sandbox-runner / venv)."""
    if not settings.sandbox_enabled:
        return ExecutionResult(stderr="sandbox disabled", error="sandbox disabled")
    if not shutil.which("docker"):
        return ExecutionResult(
            stderr="docker CLI not found",
            error="本机未安装 Docker，无法执行数据分析沙箱。请安装 Docker Desktop 后重试。",
        )
    if not _sandbox_image_ready():
        return ExecutionResult(
            stderr=f"sandbox image missing: {settings.sandbox_image}",
            error=_MISSING_SANDBOX_IMAGE,
        )

    timeout = int(timeout_sec or settings.sandbox_timeout_sec)
    host_data = Path(data_path).resolve() if data_path else None
    if host_data is not None and not host_data.is_file():
        return ExecutionResult(stderr=f"data file missing: {host_data}", error="数据文件不存在")

    ext = ".csv"
    if host_data is not None:
        ext = host_data.suffix.lower() if host_data.suffix.lower() in {".csv", ".xlsx"} else ".csv"

    container_data = f"/data/input{ext}"
    tmp_root = ephemeral_dir()
    staged: Path | None = None
    if host_data is not None:
        host_data, staged = _stage_for_host_docker(host_data)
    out_dir = Path(
        tempfile.mkdtemp(
            prefix="omni-sandbox-out-",
            dir=str(tmp_root) if tmp_root is not None else None,
        )
    )
    try:
        cmd = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network=none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m",
            f"--memory={settings.sandbox_memory}",
            f"--pids-limit={settings.sandbox_pids_limit}",
            "-e",
            f"SANDBOX_DATA_PATH={container_data}",
            "-e",
            "SANDBOX_ARTIFACT_PATH=/artifacts/out.png",
            "-v",
            f"{out_dir}:/artifacts:rw",
        ]
        if host_data is not None:
            # Docker Desktop on Windows accepts forward-slash absolute paths.
            # Paths must exist on the *host* (docker.sock); stage into SANDBOX_TMP_DIR.
            host_mount = str(host_data).replace("\\", "/")
            cmd.extend(["-v", f"{host_mount}:{container_data}:ro"])
        cmd.extend([settings.sandbox_image, "python", "/sandbox/run.py"])

        try:
            proc = subprocess.run(
                cmd,
                input=code,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else ""
            stderr = (e.stderr or "") if isinstance(e.stderr, str) else "execution timed out"
            return ExecutionResult(
                stdout=stdout,
                stderr=stderr or "execution timed out",
                timed_out=True,
                error=SANDBOX_TIMEOUT_MESSAGE,
            )
        except FileNotFoundError:
            return ExecutionResult(
                stderr="docker CLI not found",
                error="本机未安装 Docker，无法执行数据分析沙箱。",
            )

        artifacts, png_bytes = _collect_chart_artifacts(out_dir)

        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        error = ""
        if proc.returncode != 0:
            blob = stderr or f"sandbox exited with code {proc.returncode}"
            if "Unable to find image" in blob or "registry-1.docker.io" in blob:
                error = _MISSING_SANDBOX_IMAGE
            else:
                error = blob
        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            artifacts=artifacts,
            error=error,
            artifact_png_bytes=png_bytes,
        )
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass


def _stage_for_host_docker(src: Path) -> tuple[Path, Path | None]:
    """Copy in-container files onto SANDBOX_TMP_DIR so `docker -v` can bind a real file.

    sandbox-runner talks to the host daemon: bind-mount paths are host paths.
    A file that only exists inside this container (e.g. /app/data/eval/...) must be
    copied to the shared tmp volume. If the host path is missing, Docker creates a
    directory and pandas then raises IsADirectoryError on /data/input.csv.
    """
    tmp_root = ephemeral_dir()
    if tmp_root is None:
        return src, None
    try:
        src.resolve().relative_to(tmp_root.resolve())
        return src, None
    except ValueError:
        pass
    fd, dest_s = tempfile.mkstemp(prefix="omni-data-", suffix=src.suffix or ".csv", dir=str(tmp_root))
    dest = Path(dest_s)
    try:
        os.close(fd)
        shutil.copy2(src, dest)
    except OSError:
        dest.unlink(missing_ok=True)
        return src, None
    return dest, dest


async def run_code_async(
    code: str,
    *,
    data_path: str | Path | None = None,
    timeout_sec: int | None = None,
) -> ExecutionResult:
    """Async wrapper so callers on the event loop never block on docker run."""
    return await asyncio.to_thread(
        run_code, code, data_path=data_path, timeout_sec=timeout_sec
    )
