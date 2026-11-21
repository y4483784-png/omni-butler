#!/usr/bin/env python3

"""Offline checks for the watch age model.

Product probes map to watchdog.py. Coverage probes show a state was reached.
A tautology is refused. This file does not start the product.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "Watch.tla"
CHECK = Path(__file__).resolve().parent / "check.py"


def load_check():
    spec = importlib.util.spec_from_file_location("watch_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def tlc_on(spec_text: str, cfg: str) -> subprocess.CompletedProcess[str]:
    check = load_check()
    jar = check.jar_path()
    with tempfile.TemporaryDirectory(prefix="watch-probe-") as tmp:
        meta = Path(tmp)
        (meta / "Watch.tla").write_text(spec_text, encoding="utf-8")
        (meta / "Watch.cfg").write_text(cfg, encoding="utf-8")
        return subprocess.run(
            [
                "java",
                "-XX:+UseParallelGC",
                "-cp",
                str(jar),
                "tlc2.TLC",
                "-workers",
                "1",
                "-nowarning",
                "-config",
                str(meta / "Watch.cfg"),
                "-metadir",
                str(meta),
                "Watch.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


class WatchAgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("java") is None:
            raise unittest.SkipTest("java is not on PATH")

    def test_probe_kinds_stay_apart(self) -> None:
        check = load_check()
        product = {name for name in check.HELD}
        coverage = {label for _, label in check.COVERAGE.values()}
        self.assertTrue(product.isdisjoint(coverage))
        for name in product:
            self.assertEqual(check.probe_kind(name), "product")
        for name in coverage:
            self.assertEqual(check.probe_kind(name), "coverage")
        self.assertEqual(check.probe_kind("TRUE"), "unknown")

    def test_source_map_matches_the_watchdog(self) -> None:
        watchdog = (ROOT / "backend/app/tasks/watchdog.py").read_text(encoding="utf-8")
        self.assertNotIn("created_at", watchdog)
        self.assertIn('"running": "leave"', watchdog)
        self.assertIn("stamp is not None", watchdog)
        self.assertNotIn("row.last_progress_at =", watchdog)
        spec = SPEC.read_text(encoding="utf-8")
        self.assertNotIn("createdOld = TRUE", spec)
        self.assertIn("createdOld \\in BOOLEAN", spec)
        self.assertIn("breakAge /\\ createdOld", spec)
        load_check().anchors()

    def test_source_anchor_rejects_a_created_at_read(self) -> None:
        check = load_check()
        original = check.read
        watchdog = original("backend/app/tasks/watchdog.py")
        mutated = watchdog.replace(
            "stamp = row.last_progress_at",
            "stamp = row.last_progress_at or row.created_at",
            1,
        )

        def read(relative: str) -> str:
            if relative == "backend/app/tasks/watchdog.py":
                return mutated
            return original(relative)

        check.read = read
        try:
            with self.assertRaises(SystemExit) as caught:
                check.anchors()
        finally:
            check.read = original
        self.assertEqual(caught.exception.code, 2)

    def test_held_and_breaks_and_witnesses(self) -> None:
        expect = [
            (["--age", "old"], 0, "TLC exit 0, no error."),
            (["--age", "young"], 0, "TLC exit 0, no error."),
            (
                ["--age", "old", "--break-stop"],
                1,
                "TLC exit 12 violates CreationAgeDoesNotStop.",
            ),
            (
                ["--age", "young", "--break-stop"],
                1,
                "TLC exit 12 violates CreationAgeDoesNotStop.",
            ),
            (
                ["--age", "old", "--break-double"],
                1,
                "TLC exit 12 violates OneNoticePerProgressStamp.",
            ),
            (
                ["--age", "young", "--break-double"],
                1,
                "TLC exit 12 violates OneNoticePerProgressStamp.",
            ),
            (
                ["--age", "old", "--break-wait"],
                1,
                "TLC exit 12 violates WaitingLeftAlone.",
            ),
            (
                ["--age", "young", "--break-wait"],
                1,
                "TLC exit 12 violates WaitingLeftAlone.",
            ),
            (
                ["--age", "old", "--break-age"],
                1,
                "TLC exit 12 violates CreationAgeDoesNotStop.",
            ),
            (["--age", "young", "--break-age"], 0, "TLC exit 0, no error."),
            (["--witness", "old-null"], 1, "TLC exit 12 violates WitnessOldNull."),
            (["--witness", "young-null"], 1, "TLC exit 12 violates WitnessYoungNull."),
            (["--witness", "old-sample"], 1, "TLC exit 12 violates WitnessOldSample."),
            (["--witness", "young-sample"], 1, "TLC exit 12 violates WitnessYoungSample."),
        ]
        for args, code, line in expect:
            with self.subTest(args=args):
                result = run_checker(*args)
                self.assertEqual(result.returncode, code, result.stderr[-500:])
                self.assertIn(line, result.stdout)
                if code == 1:
                    self.assertIn("Invariant ", result.stdout)
                    self.assertNotIn("Invariant TypeOK is violated.", result.stdout)

    def test_judge_rejects_a_type_or_parser_failure(self) -> None:
        check = load_check()
        type_fail = subprocess.CompletedProcess(
            args=[],
            returncode=12,
            stdout="Error: Invariant TypeOK is violated by the initial state:\n",
            stderr="",
        )
        with self.assertRaises(SystemExit) as caught:
            check.judge(type_fail, "CreationAgeDoesNotStop")
        self.assertEqual(caught.exception.code, 2)
        parser_fail = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="CreationAgeDoesNotStop\nParsing error\n",
            stderr="",
        )
        with self.assertRaises(SystemExit) as caught:
            check.judge(parser_fail, "CreationAgeDoesNotStop")
        self.assertEqual(caught.exception.code, 2)

    def test_null_guard_is_not_a_tautology(self) -> None:
        spec = SPEC.read_text(encoding="utf-8")
        mutated = spec.replace(
            """shouldTell ==
    status = "running" /\\ stamp > 0 /\\ (
        breakDouble \\/ (stamp = observed /\\ told /= stamp /\\ stable >= 1)
    )""",
            """shouldTell ==
    status = "running" /\\ (
        breakDouble \\/ (stamp = observed /\\ stable >= 1)
    )""",
            1,
        )
        self.assertNotEqual(mutated, spec)
        cfg = """SPECIFICATION Spec
CONSTANT
  breakStop = FALSE
  breakDouble = FALSE
  breakWait = FALSE
  breakAge = FALSE
  createdOld = TRUE
INVARIANT NullSampleStaysQuiet
INVARIANT CreationAgeDoesNotStop
"""
        result = tlc_on(mutated, cfg)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 12, output[-800:])
        self.assertIn("Invariant NullSampleStaysQuiet is violated.", output)
        self.assertNotIn("Invariant TypeOK is violated.", output)
        self.assertNotIn("Invariant CreationAgeDoesNotStop is violated.", output)

    def test_true_age_invariant_does_not_count_as_a_break(self) -> None:
        spec = SPEC.read_text(encoding="utf-8")
        mutated = spec.replace(
            """CreationAgeDoesNotStop ==
    status \\in {"running", "waiting_person", "waiting_resource"}""",
            "CreationAgeDoesNotStop == TRUE",
            1,
        )
        self.assertNotEqual(mutated, spec)
        cfg = """SPECIFICATION Spec
CONSTANT
  breakStop = FALSE
  breakDouble = FALSE
  breakWait = FALSE
  breakAge = TRUE
  createdOld = TRUE
INVARIANT CreationAgeDoesNotStop
"""
        result = tlc_on(mutated, cfg)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, output[-800:])
        self.assertIn("No error has been found", output)
        check = load_check()
        with self.assertRaises(SystemExit) as caught:
            check.judge(result, "CreationAgeDoesNotStop")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
