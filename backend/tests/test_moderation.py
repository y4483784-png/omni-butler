"""Sensitive-word filter (PRD 4.2)."""

from __future__ import annotations

import asyncio

from app.core import moderation as mod


def test_find_and_redact_extra_words(monkeypatch):
    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "forbiddenxyz,alpha")

    assert mod.contains_sensitive("please forbiddenxyz now")
    assert "forbiddenxyz" in mod.find_sensitive("please forbiddenxyz now")
    assert mod.redact_text("please forbiddenxyz now") == "please *** now"
    assert not mod.contains_sensitive("hello world")


def test_obfuscated_spacing(monkeypatch):
    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "冰毒")

    assert mod.contains_sensitive("冰 毒")
    assert "***" in mod.redact_text("涉及冰 毒的内容")


def test_disabled_skips(monkeypatch):
    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "forbiddenxyz")
    assert not mod.contains_sensitive("forbiddenxyz")
    assert mod.redact_text("forbiddenxyz") == "forbiddenxyz"


def test_redact_token_stream_holdback(monkeypatch):
    monkeypatch.setattr(mod.settings, "sensitive_filter_enabled", True)
    monkeypatch.setattr(mod.settings, "sensitive_use_builtin", False)
    monkeypatch.setattr(mod.settings, "sensitive_words", "bombword")

    async def _run():
        async def _src():
            for part in ("safe ", "bomb", "word more"):
                yield part

        chunks = [c async for c in mod.redact_token_stream(_src())]
        return "".join(chunks)

    out = asyncio.run(_run())
    assert "bombword" not in out
    assert "***" in out
    assert out.startswith("safe ")
