"""Regression tests for eval target resolution and preflight."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from app.eval.env import bootstrap, resolve_target
from app.eval.suites import get_suite


def _load_eval_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "eval.py"
    spec = importlib.util.spec_from_file_location("omni_eval_cli", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resolve_target_env_beats_suite_default(monkeypatch):
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert resolve_target(None, default="venv") == "services"


def test_resolve_target_cli_beats_env(monkeypatch):
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert resolve_target("venv", default="services") == "venv"


def test_bootstrap_venv_overwrites_compose_leftovers(monkeypatch, tmp_path):
    monkeypatch.setenv("REDIS_ENABLED", "true")
    monkeypatch.setenv("SANDBOX_RUNNER_URL", "http://sandbox-runner:8002")
    monkeypatch.setenv("SANDBOX_TMP_DIR", "/var/omni-tmp")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    bootstrap("venv", sqlite_path=tmp_path / "e.db")
    assert os.environ["REDIS_ENABLED"] == "false"
    assert os.environ["SANDBOX_RUNNER_URL"] == ""
    assert os.environ["SANDBOX_TMP_DIR"] == ""
    assert os.environ["DATABASE_URL"].startswith("sqlite")


def test_preflight_tabular_full_services_checks_runner_not_docker(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "_http_ok", lambda _url: True)
    monkeypatch.setattr(cli, "_docker_ok", lambda: False)
    # These cases are about the sandbox probe, not whether a key happens to be set.
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: True)
    errors = cli.preflight(get_suite("tabular"), "services", "full", [])
    assert errors == []


def test_preflight_tabular_full_services_reports_runner(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setenv("SANDBOX_RUNNER_URL", "http://sandbox-runner:8002")
    monkeypatch.setattr(cli, "_http_ok", lambda _url: False)
    monkeypatch.setattr(cli, "_docker_ok", lambda: True)
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: True)
    errors = cli.preflight(get_suite("tabular"), "services", "full", [])
    assert errors and "sandbox-runner" in errors[0]
    assert "omni-sandbox" not in errors[0]


def test_preflight_tabular_full_venv_still_needs_local_image(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "_docker_ok", lambda: False)
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: True)
    errors = cli.preflight(get_suite("tabular"), "venv", "full", [])
    assert errors and "omni-sandbox" in errors[0]


def test_preflight_rag_full_requires_llm_from_profile(monkeypatch, tmp_path):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "BACKEND", tmp_path)
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: False)
    errors = cli.preflight(get_suite("rag"), "venv", "full", [])
    assert any("LLM_API_KEY" in e for e in errors)


def test_preflight_rag_full_venv_refuses_instead_of_degrading(monkeypatch):
    """--production must be a blocker on venv, not a silent keyword fallback."""
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: True)
    monkeypatch.setattr(cli, "_tcp_ok", lambda *a, **k: False)
    errors = cli.preflight(get_suite("rag"), "venv", "full", [])
    assert errors
    assert any("MinIO" in e or "Qdrant" in e for e in errors)


def test_preflight_rag_full_services_needs_middleware(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: True)
    monkeypatch.setattr(cli, "_tcp_ok", lambda *a, **k: False)
    errors = cli.preflight(get_suite("rag"), "services", "full", [])
    assert any("MinIO" in e for e in errors)
    assert any("Qdrant" in e for e in errors)


def test_degradation_reason_detects_keyword_fallback():
    cli = _load_eval_cli()
    rag = {"metrics": {"mode": "keyword_paragraph", "degraded": True}}
    assert "degraded" in cli._degradation_reason(rag, "full")
    assert cli._degradation_reason({"metrics": {"mode": "production_hybrid", "degraded": False}}, "full") == ""
    midrun = {"metrics": {"mode": "production_degraded", "degraded": True}}
    assert "production_degraded" in cli._degradation_reason(midrun, "full")
    tabular = {"degraded": True, "planner_error_rate": 0.5, "planner_error_count": 3}
    reason = cli._degradation_reason(tabular, "full")
    assert "planner" in reason and "3" in reason
    assert cli._degradation_reason({"degraded": False}, "full") == ""


def test_smoke_profile_is_never_flagged_as_degraded():
    """The smoke profile *is* the deterministic replay: keyword retrieval and
    plan-only compile are its definition, not a fallback."""
    cli = _load_eval_cli()
    rag_smoke = {"metrics": {"mode": "keyword_paragraph", "degraded": True}}
    assert cli._degradation_reason(rag_smoke, "smoke") == ""
    tabular_smoke = {"degraded": True, "planner_error_rate": 0.0, "planner_error_count": 0}
    assert cli._degradation_reason(tabular_smoke, "smoke") == ""


def test_router_cache_miss_returns_blocked_report(monkeypatch):
    """A stale smoke cache must produce a readable exit-2 report, not a traceback."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "run_office_eval.py"
    spec = importlib.util.spec_from_file_location("omni_run_office_eval", path)
    assert spec and spec.loader
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    from app.eval.tool_routing import CacheMissError

    def _boom(*_a, **_k):
        raise CacheMissError("route_0001: router cache miss/stale")

    monkeypatch.setattr(runner, "run_tool_routing_eval", _boom)
    result = runner.run(runner.build_parser().parse_args(["--require-cache"]))
    assert result.exit_code == 2
    assert result.report["blocked"] == "router_cache_miss"
    assert result.samples and result.samples[0]["status"] == "blocked"


def test_pick_target_honors_eval_target_for_tabular(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert cli.pick_target(get_suite("tabular"), None) == "services"


def test_pick_target_follows_eval_target_when_suite_allows_it(monkeypatch):
    """A suite that accepts services runs there, so a test image can run pytest."""
    cli = _load_eval_cli()
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert cli.pick_target(get_suite("pytest"), None) == "services"
    assert get_suite("pytest").default_target == "venv"


def test_bootstrap_services_isolate_keeps_sandbox_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    monkeypatch.setenv("SANDBOX_RUNNER_URL", "http://sandbox-runner:8002")
    bootstrap("services", sqlite_path=tmp_path / "e.db", isolate_app_db=True)
    assert os.environ["DATABASE_URL"].startswith("sqlite")
    assert os.environ["SANDBOX_RUNNER_URL"] == "http://sandbox-runner:8002"


def test_eval_cli_has_diff_and_profile_last():
    cli = _load_eval_cli()
    sub = cli.build_cli()._subparsers._group_actions[0].choices
    assert "diff" in sub
    assert "last" in sub


def test_hermetic_suites_isolate_app_db():
    assert get_suite("rag").isolate_app_db is True
    assert get_suite("tabular").isolate_app_db is True
    assert get_suite("context").isolate_app_db is True
    assert get_suite("grounding").isolate_app_db is False
    assert get_suite("agent").isolate_app_db is False
    assert get_suite("agent").default_target == "services"


def test_retired_yz_suite_is_not_registered():
    """The yz side path is superseded by agent; it must not be runnable or gated."""
    from app.eval.suites import SUITES

    with pytest.raises(KeyError):
        get_suite("yz")
    assert "yz" not in SUITES


def test_ensure_eval_user_idempotent():
    from app.core.db import SessionLocal
    from app.eval.tenant import ensure_eval_user
    from app.models.models import User

    db = SessionLocal()
    try:
        ensure_eval_user(db, 92001, "rag_eval_92001", "RAG Eval Fixture")
        ensure_eval_user(db, 92001, "rag_eval_92001", "RAG Eval Fixture")
        db.commit()
        assert db.query(User).filter(User.id == 92001).count() == 1
    finally:
        db.query(User).filter(User.id == 92001).delete()
        db.commit()
        db.close()
