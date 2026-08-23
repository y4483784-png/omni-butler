"""Smoke tests for YZ full-chain eval pipeline (no high-score gates)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest

from app.eval.yz_fullchain import (
    YZEvalCase,
    fact_containment,
    load_cases,
    run_ragas_metrics,
    run_yz_fullchain_eval,
)


class _MemoryBlobStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, key: str, data: bytes | BinaryIO) -> str:
        body = data if isinstance(data, (bytes, bytearray)) else data.read()
        self._data[key] = bytes(body)
        return key

    def exists(self, key: str) -> bool:
        return key in self._data

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def materialize(self, key: str) -> Path:
        if key not in self._data:
            raise FileNotFoundError(key)
        suffix = Path(key).suffix[:16]
        fd, temp_name = tempfile.mkstemp(prefix="omni-yz-", suffix=suffix)
        os.close(fd)
        path = Path(temp_name)
        path.write_bytes(self._data[key])
        return path

    def release_materialized(self, path: Path, key: str) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@pytest.fixture
def memory_blob_store():
    store = _MemoryBlobStore()
    patches = [
        patch("app.eval.yz_fullchain.get_blob_store", return_value=store),
        patch("app.storage.documents.get_blob_store", return_value=store),
    ]
    for p in patches:
        p.start()
    yield store
    for p in patches:
        p.stop()


def test_yz_dataset_has_enough_cases():
    cases = load_cases()
    assert len(cases) >= 380


def test_fact_containment_helper():
    assert fact_containment("月度迟到超过5次取消全勤奖", ["5次", "全勤奖"]) == 1.0
    assert fact_containment("不知道", ["5次", "全勤奖"]) == 0.0
    assert fact_containment("anything", []) == 1.0


def test_run_ragas_metrics_without_api_key():
    with patch("app.eval.ragas_judge.settings.llm_api_key", ""):
        scores = run_ragas_metrics(
            [
                {
                    "question": "q",
                    "answer": "a",
                    "contexts": ["c"],
                    "ground_truth": "g",
                }
            ]
        )
    assert scores.get("faithfulness") is None
    assert scores.get("answer_relevancy") is None
    assert scores.get("context_precision") is None
    assert scores.get("context_recall") is None


def test_yz_fullchain_smoke(memory_blob_store, monkeypatch):
    monkeypatch.setattr(
        "app.eval.yz_fullchain.complete_text",
        lambda messages, **kw: "演示回答：月度累计迟到超过5次，取消当季度全勤奖资格 [1]",
    )
    cases = load_cases()[:2]
    with (
        patch("app.rag.ingestion.settings.llm_api_key", "fake-key"),
        patch("app.rag.ingestion.vector_store.is_available", return_value=False),
        patch("app.rag.ingestion.vector_store.delete_by_document"),
        patch("app.rag.ingestion.vector_store.upsert_chunks"),
        patch("app.rag.retrieval.vector_store.is_available", return_value=False),
    ):
        report = run_yz_fullchain_eval(cases, skip_ragas=True)
    assert report.n == 2
    assert "recall_at_k" in report.retrieval
    assert report.skipped_ragas is True
    assert report.fact_containment_mean >= 0.0


def test_yz_case_from_dict_minimal():
    c = YZEvalCase.from_dict(
        {
            "id": "t1",
            "query": "hello",
            "gold_doc_keys": [],
            "gold_facts": [],
            "ground_truth": "gt",
        }
    )
    assert c.id == "t1"
    assert c.gold == {}
