"""Load Omni constitution + optional aiming-lab AutoHarness pipeline."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONSTITUTION_PATH = Path(__file__).with_name("constitution.yaml")

_DEFAULT_OMNI: dict[str, Any] = {
    "mode": "standard",
    "allowed_tools": ["kb", "web", "calendar", "sandbox"],
    "tool_risk": {"kb": "low", "web": "medium", "calendar": "medium", "sandbox": "high"},
    "limits": {
        "web_snippet_max_chars": 400,
        "web_content_max_chars": 2000,
        "kb_content_max_chars": 4000,
        "sandbox_snippet_max_chars": 400,
        "evidence_content_max_chars": 8000,
    },
    "sandbox": {
        "require_network_none": True,
        "deny_arg_keys": ["network", "host_network", "privileged"],
        "deny_arg_substrings": ["rm -rf", "/etc/passwd", "docker.sock"],
    },
    "risk_patterns": [],
    "audit": {"enabled": True, "filename": "harness-audit.jsonl"},
}


def _read_raw() -> dict[str, Any]:
    try:
        raw = yaml.safe_load(_CONSTITUTION_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


@lru_cache(maxsize=1)
def load_constitution() -> dict[str, Any]:
    """Gateway-facing dict (omni extensions + mode)."""
    raw = _read_raw()
    omni = raw.get("omni") if isinstance(raw.get("omni"), dict) else {}
    merged = dict(_DEFAULT_OMNI)
    for key, value in omni.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    merged["mode"] = str(raw.get("mode") or merged.get("mode") or "standard")
    return merged


@lru_cache(maxsize=1)
def get_autoharness_pipeline():
    """Return ToolGovernancePipeline if aiming-lab AutoHarness is installed."""
    try:
        from autoharness import Constitution, ToolGovernancePipeline
    except ImportError:
        return None

    raw = _read_raw()
    # Strip Omni-only block so AH validation stays clean
    data = {k: v for k, v in raw.items() if k != "omni"}
    if not data.get("identity"):
        data["identity"] = {
            "name": "omni-butler",
            "description": "Office assistant tool governance",
            "boundaries": [],
        }
    data.setdefault("mode", "standard")
    try:
        constitution = Constitution.from_dict(data)
    except Exception:
        # Fall back to AH defaults + our tool allow list if YAML shape drifts
        try:
            constitution = Constitution.default()
        except Exception:
            return None
    try:
        return ToolGovernancePipeline(constitution)
    except Exception:
        return None


def autoharness_available() -> bool:
    try:
        import autoharness  # noqa: F401

        return True
    except ImportError:
        return False


def reload_constitution() -> dict[str, Any]:
    load_constitution.cache_clear()
    get_autoharness_pipeline.cache_clear()
    return load_constitution()
