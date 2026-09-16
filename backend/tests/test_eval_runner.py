"""Regression tests for eval target resolution and preflight."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

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
    errors = cli.preflight(get_suite("tabular"), "services", "full", [])
    assert errors == []


def test_preflight_tabular_full_services_reports_runner(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setenv("SANDBOX_RUNNER_URL", "http://sandbox-runner:8002")
    monkeypatch.setattr(cli, "_http_ok", lambda _url: False)
    monkeypatch.setattr(cli, "_docker_ok", lambda: True)
    errors = cli.preflight(get_suite("tabular"), "services", "full", [])
    assert errors and "sandbox-runner" in errors[0]
    assert "omni-sandbox" not in errors[0]


def test_preflight_tabular_full_venv_still_needs_local_image(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "_docker_ok", lambda: False)
    errors = cli.preflight(get_suite("tabular"), "venv", "full", [])
    assert errors and "omni-sandbox" in errors[0]


def test_preflight_rag_full_requires_llm_from_profile(monkeypatch, tmp_path):
    cli = _load_eval_cli()
    monkeypatch.setattr(cli, "BACKEND", tmp_path)
    monkeypatch.setattr(cli, "_llm_key_ok", lambda: False)
    errors = cli.preflight(get_suite("rag"), "venv", "full", [])
    assert any("LLM_API_KEY" in e for e in errors)


def test_pick_target_honors_eval_target_for_tabular(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert cli.pick_target(get_suite("tabular"), None) == "services"


def test_pick_target_keeps_pytest_on_venv_inside_compose(monkeypatch):
    cli = _load_eval_cli()
    monkeypatch.setenv("EVAL_TARGET", "services")
    assert cli.pick_target(get_suite("pytest"), None) == "venv"


def test_bootstrap_services_isolate_keeps_sandbox_url(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x")
    monkeypatch.setenv("SANDBOX_RUNNER_URL", "http://sandbox-runner:8002")
    bootstrap("services", sqlite_path=tmp_path / "e.db", isolate_app_db=True)
    assert os.environ["DATABASE_URL"].startswith("sqlite")
    assert os.environ["SANDBOX_RUNNER_URL"] == "http://sandbox-runner:8002"


def test_hermetic_suites_isolate_app_db():
    assert get_suite("rag").isolate_app_db is True
    assert get_suite("tabular").isolate_app_db is True
    assert get_suite("context").isolate_app_db is True
    assert get_suite("yz").isolate_app_db is False
    assert get_suite("grounding").isolate_app_db is False


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
