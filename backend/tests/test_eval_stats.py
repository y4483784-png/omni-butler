"""Bootstrap CI helper."""

from __future__ import annotations

import pytest

from app.eval.stats import bootstrap_ci, ci_overlap


@pytest.mark.unit
def test_bootstrap_ci_deterministic():
    a = bootstrap_ci([1, 0, 1, 1, 0], seed=0)
    b = bootstrap_ci([1, 0, 1, 1, 0], seed=0)
    assert a == b
    assert a["n"] == 5
    assert 0.0 <= a["lo"] <= a["mean"] <= a["hi"] <= 1.0


@pytest.mark.unit
def test_ci_overlap():
    a = {"lo": 0.4, "hi": 0.6}
    b = {"lo": 0.55, "hi": 0.7}
    c = {"lo": 0.8, "hi": 0.9}
    assert ci_overlap(a, b)
    assert not ci_overlap(a, c)
