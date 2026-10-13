"""Central quality gates for every frozen evaluation dataset."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.eval.contracts import (
    CONTRACTS,
    AgentContext,
    eval_suite_ids,
    load_jsonl,
    normalize_text,
    validate_all,
)
from app.eval.jsonl_io import normalize_newlines, write_jsonl

ROOT = Path(__file__).resolve().parents[1]


def test_agent_context_defaults_mirror_dataclass_shape():
    ctx = AgentContext.model_validate({"has_kb_docs": True, "has_tabular_docs": True})
    assert ctx.document_keys == []
    assert ctx.pending_calendar is None
    with pytest.raises(Exception):
        AgentContext.model_validate({"has_kb_docs": True})  # has_tabular_docs is required


def test_agent_dataset_declares_availability_switches():
    from app.eval.contracts import DATA_ROOT

    rows, errors = load_jsonl(DATA_ROOT / "agent_tasks.jsonl")
    assert not errors
    assert rows
    for row in rows:
        assert "context" in row, f"{row.get('id')}: missing context"
        for key in ("has_kb_docs", "has_tabular_docs", "document_keys", "pending_calendar"):
            assert key in row["context"], f"{row.get('id')}: context.{key} missing"
    by_tag = {}
    for row in rows:
        for tag in ("availability_gate", "slot_fill", "doc_scope"):
            if tag in row["tags"]:
                by_tag[tag] = by_tag.get(tag, 0) + 1
    assert by_tag.get("availability_gate", 0) >= 6
    assert by_tag.get("slot_fill", 0) >= 4
    assert by_tag.get("doc_scope", 0) >= 4
    # The new scenarios only matter if they actually differ from the defaults.
    assert any(row["context"]["has_tabular_docs"] is False for row in rows)
    assert any(row["context"]["document_keys"] for row in rows)
    assert any(row["context"]["pending_calendar"] for row in rows)


def test_normalize_text_catches_punctuation_only_duplicates():
    assert normalize_text("请问，正常工作时间？") == normalize_text("请问正常工作时间")


@pytest.mark.parametrize("result", validate_all(), ids=lambda r: r.contract.name)
def test_dataset_contract(result):
    assert result.ok, "\n".join(result.errors)


def test_contracts_cover_registered_eval_datasets():
    assert set(CONTRACTS) == eval_suite_ids()


def test_lint_cli_passes_and_outputs_every_suite():
    proc = subprocess.run(
        [sys.executable, "scripts/lint_eval_datasets.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in CONTRACTS:
        assert f"[OK] {name}:" in proc.stdout


def test_jsonl_files_are_one_object_per_nonempty_line():
    for contract in CONTRACTS.values():
        for raw in contract.path.read_text(encoding="utf-8").splitlines():
            if raw.strip() and not raw.startswith("#"):
                assert isinstance(json.loads(raw), dict)


@pytest.mark.parametrize(
    ("script", "dataset"),
    [
        ("gen_rag_eval.py", "rag_retrieval.jsonl"),
        ("gen_yz_eval.py", "yz_fullchain.jsonl"),
        ("gen_office_eval.py", "office_tool_routing.jsonl"),
        ("gen_grounding_eval.py", "grounding_faithfulness.jsonl"),
        ("gen_office_tabular_eval.py", "office_tabular.jsonl"),
        ("gen_context_eval.py", "context_recall.jsonl"),
        ("gen_agent_eval.py", "agent_tasks.jsonl"),
    ],
)
def test_generators_are_byte_idempotent(script, dataset):
    """Regenerating must reproduce the committed file, aside from newline style.

    Comparing two runs against each other (the previous shape of this test) passes
    even when the committed dataset was hand-edited, so gold could drift silently.
    CRLF from an HGFS/Windows checkout is not content drift: both sides are folded
    to LF before the compare. A real gold edit still fails.
    """
    path = ROOT / "data" / "eval" / dataset
    committed = path.read_bytes()
    subprocess.run([sys.executable, f"scripts/{script}"], cwd=ROOT, check=True)
    try:
        regenerated = path.read_bytes()
        assert normalize_newlines(regenerated) == normalize_newlines(committed), (
            f"{dataset} changed when regenerated from {script}: the committed dataset "
            "is not reproducible (hand-edited gold?)"
        )
        assert b"\r" not in regenerated, f"{script} wrote a CR; generators must emit LF"
    finally:
        path.write_bytes(committed)


def test_newline_normalization_ignores_crlf_but_not_content():
    """The idempotency compare must forgive CRLF and still catch a gold edit."""
    lf = b'{"id": "a"}\n{"id": "b"}\n'
    crlf = lf.replace(b"\n", b"\r\n")
    assert b"\r\n" in crlf
    assert normalize_newlines(crlf) == normalize_newlines(lf)
    drifted = lf.replace(b'"b"', b'"c"')
    assert normalize_newlines(crlf) != normalize_newlines(drifted)


def test_crlf_checkout_still_matches_generator_output(tmp_path):
    """A shared-disk checkout that rewrote LF to CRLF is not a generator drift."""
    from scripts.gen_grounding_eval import build_cases

    path = tmp_path / "grounding_faithfulness.jsonl"
    write_jsonl(path, build_cases())
    lf = path.read_bytes()
    assert b"\r" not in lf
    crlf = lf.replace(b"\n", b"\r\n")
    assert normalize_newlines(crlf) == lf
    edited = crlf.replace(b"gf_kb_001", b"gf_kb_999", 1)
    assert normalize_newlines(edited) != lf


def test_agent_answer_specs_are_recomputable():
    """Numeric gold in the agent dataset must come from data, not from typing.

    Attendance aggregates are recomputed from the frozen seed (which the generator
    derives from the CSV), and the multi-tool counts are recomputed from the source
    CSV using the standard library only.
    """
    import csv

    from app.eval.contracts import AgentCase, normalize_text

    rows, errs = load_jsonl(ROOT / "data" / "eval" / "agent_tasks.jsonl")
    assert not errs
    models = {r["id"]: AgentCase.model_validate(r).model_dump(mode="python") for r in rows}

    # 1) attendance aggregates: the query is frozen in the yz attendance seed
    seed_rows, _ = load_jsonl(ROOT / "data" / "eval" / "seeds" / "yz_attendance.jsonl")
    by_query = {normalize_text(r["query"]): r for r in seed_rows if r.get("gold_facts")}
    checked = 0
    for row in models.values():
        if "aggregate" not in row["tags"]:
            continue
        seed = by_query.get(normalize_text(row["query"]))
        assert seed is not None, f"{row['id']}: aggregate query not found in the seed"
        expected = float(seed["gold_facts"][0])
        assert row["answer_spec"]["type"] == "number", row["id"]
        assert float(row["answer_spec"]["value"]) == pytest.approx(expected, abs=0.011), row["id"]
        checked += 1
    assert checked >= 20, f"only {checked} aggregate rows checked"

    # 2) multi-tool counts: recompute the threshold counts from the source CSV. That
    #    CSV is a local-only fixture (gitignored), so skip where it is absent rather
    #    than pretending the gold was verified.
    csv_fixture = ROOT / "tests" / "YZ测试文档" / "测试用例.csv"
    if not csv_fixture.is_file():
        pytest.skip("YZ attendance CSV fixture not present (gitignored, local-only)")
    with csv_fixture.open(encoding="utf-8-sig", newline="") as fh:
        attendance = list(csv.DictReader(fh))
    late = [int(r["迟到次数"]) for r in attendance]
    leave = [float(r["请假天数"]) for r in attendance]
    actual = [int(r["实际出勤天数"]) for r in attendance]
    expect = [int(r["应出勤天数"]) for r in attendance]
    truth = {
        sum(x > 5 for x in late),
        sum(x > 3 for x in late),
        sum(a < e for a, e in zip(actual, expect)),
        sum(x > 2 for x in leave),
    }
    got = {
        int(row["answer_spec"]["value"])
        for row in models.values()
        if row["task_type"] == "multi_tool"
    }
    assert got == truth, f"multi_tool gold {got} != recomputed {truth}"


def test_retired_yz_unanswerable_rows_are_documented():
    """The yz dataset predates the "a refusal cites nothing" rule.

    The suite is retired, so this pins the known count instead of failing the lint:
    if the rows are ever cleaned up, this test says so and the note can be removed.
    """
    rows, errs = load_jsonl(ROOT / "data" / "eval" / "yz_fullchain.jsonl")
    assert not errs
    unanswerable = [r for r in rows if r.get("expect_unanswerable")]
    with_keys = [r["id"] for r in unanswerable if r.get("gold_doc_keys")]
    assert len(with_keys) == 20, (
        "expected the known 20 unanswerable yz rows to still carry gold_doc_keys, "
        f"got {len(with_keys)}: update this note and the eval README"
    )
