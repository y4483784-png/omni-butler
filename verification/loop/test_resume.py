#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "Loop.tla"
CHECK = Path(__file__).resolve().parent / "check.py"


def load_check():
    spec = importlib.util.spec_from_file_location("loop_check", CHECK)
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
    with tempfile.TemporaryDirectory(prefix="loop-probe-") as tmp:
        meta = Path(tmp)
        (meta / "Loop.tla").write_text(spec_text, encoding="utf-8")
        (meta / "Loop.cfg").write_text(cfg, encoding="utf-8")
        return subprocess.run(
            [
                "java",
                "-XX:+UseParallelGC",
                "-cp",
                str(check.jar_path()),
                "tlc2.TLC",
                "-workers",
                "1",
                "-nowarning",
                "-config",
                str(meta / "Loop.cfg"),
                "-metadir",
                str(meta),
                "Loop.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


HELD_CFG = """SPECIFICATION Spec
CONSTANT
  breakSave = FALSE
  breakResult = FALSE
  breakWrite = FALSE
  breakForbid = FALSE
  breakStop = FALSE
  breakContinuation = FALSE
  breakDuplicate = FALSE
  breakEarlyModel = FALSE
"""


class LoopResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if shutil.which("java") is None:
            raise unittest.SkipTest("java is not on PATH")

    def test_probe_kinds_stay_apart(self) -> None:
        check = load_check()
        product = set(check.HELD)
        coverage = set(check.COVERAGE.values())
        self.assertTrue(product.isdisjoint(coverage))
        for name in product:
            self.assertEqual(check.probe_kind(name), "product")
        for name in coverage:
            self.assertEqual(check.probe_kind(name), "coverage")
        self.assertEqual(check.probe_kind("TRUE"), "unknown")
        self.assertEqual(check.probe_kind("WitnessOrderedResume"), "coverage")

    def test_held_breaks_and_witnesses(self) -> None:
        expect = [
            ([], 0, "TLC exit 0, no error."),
            (["--break-save"], 1, "TLC exit 12 violates SaveBeforeExecute."),
            (["--break-result"], 1, "TLC exit 12 violates ResultBeforeNextModel."),
            (["--break-write"], 1, "TLC exit 12 violates WriteWaitsForClaim."),
            (["--break-forbid"], 1, "TLC exit 12 violates ForbidWebBlocksSearch."),
            (["--break-stop"], 1, "TLC exit 12 violates StoppedJobDoesNotExecute."),
            (["--break-continuation"], 1, "TLC exit 12 violates ContinuationDueSurvivesCrash."),
            (["--break-duplicate"], 1, "TLC exit 12 violates OneProviderInvoke."),
            (["--break-early-model"], 1, "TLC exit 12 violates SavedResultBeforeModel."),
            (["--witness", "ordered-resume"], 1, "TLC exit 12 violates WitnessOrderedResume."),
            (["--witness", "post-confirm"], 1, "TLC exit 12 violates WitnessPostConfirmCrash."),
        ]
        for args, code, line in expect:
            with self.subTest(args=args):
                result = run_checker(*args)
                self.assertEqual(result.returncode, code, (result.stderr or "")[-500:])
                self.assertIn(line, result.stdout)
                if code == 1:
                    self.assertIn("Invariant ", result.stdout)
                    self.assertNotIn("No error has been found", result.stdout)
                    self.assertNotIn("Invariant TypeOK is violated.", result.stdout)

    def test_source_anchor_rejects_a_running_save(self) -> None:
        check = load_check()
        original = check.read
        loop = original("backend/app/services/loop_round.py")
        mutated = loop.replace("state=CallState.PENDING.value", "state=CallState.RUNNING.value", 1)
        self.assertNotEqual(mutated, loop)

        def read(relative: str) -> str:
            if relative == "backend/app/services/loop_round.py":
                return mutated
            return original(relative)

        check.read = read
        try:
            with self.assertRaises(SystemExit) as caught:
                check.anchors()
        finally:
            check.read = original
        self.assertEqual(caught.exception.code, 2)

    def test_judge_rejects_wrong_code_name_type_and_liveness(self) -> None:
        check = load_check()
        cases = [
            subprocess.CompletedProcess(
                args=[],
                returncode=11,
                stdout="Error: Invariant OneProviderInvoke is violated.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=12,
                stdout="Error: Invariant SaveBeforeExecute is violated.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=12,
                stdout="Error: Invariant TypeOK is violated.\nError: Invariant OneProviderInvoke is violated.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=12,
                stdout="Error: Invariant OneProviderInvoke is violated.\nNo error has been found.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=13,
                stdout="Error: Temporal properties were violated.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout="Parsing or semantic analysis failed.\nInvariant OneProviderInvoke is violated.\n",
                stderr="",
            ),
            subprocess.CompletedProcess(
                args=[],
                returncode=12,
                stdout="Error: Invariant TRUE is violated.\n",
                stderr="",
            ),
        ]
        for result in cases:
            with self.subTest(code=result.returncode, stdout=result.stdout):
                with self.assertRaises(SystemExit) as caught:
                    check.judge(result, "OneProviderInvoke")
                self.assertEqual(caught.exception.code, 2)

    def test_duplicate_counter_is_not_a_type_error(self) -> None:
        spec = SPEC.read_text(encoding="utf-8")
        mutated = spec.replace("invoked \\in 0..2", "invoked \\in 0..1", 1)
        self.assertNotEqual(mutated, spec)
        cfg = HELD_CFG + "INVARIANT TypeOK\nINVARIANT OneProviderInvoke\n"
        mutated_cfg = cfg.replace("breakDuplicate = FALSE", "breakDuplicate = TRUE", 1)
        result = tlc_on(mutated, mutated_cfg)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 12, output[-800:])
        self.assertIn("Invariant TypeOK is violated.", output)
        self.assertNotIn("Invariant OneProviderInvoke is violated.", output)
        check = load_check()
        with self.assertRaises(SystemExit) as caught:
            check.judge(result, "OneProviderInvoke")
        self.assertEqual(caught.exception.code, 2)

    def test_true_invoke_invariant_does_not_count_as_a_break(self) -> None:
        spec = SPEC.read_text(encoding="utf-8")
        mutated = spec.replace("OneProviderInvoke == invoked <= 1", "OneProviderInvoke == TRUE", 1)
        self.assertNotEqual(mutated, spec)
        cfg = HELD_CFG + "INVARIANT OneProviderInvoke\n"
        cfg = cfg.replace("breakDuplicate = FALSE", "breakDuplicate = TRUE", 1)
        result = tlc_on(mutated, cfg)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, output[-800:])
        self.assertIn("No error has been found", output)
        check = load_check()
        with self.assertRaises(SystemExit) as caught:
            check.judge(result, "OneProviderInvoke")
        self.assertEqual(caught.exception.code, 2)

    def test_missing_preinvoke_edge_is_not_a_passing_witness(self) -> None:
        spec = SPEC.read_text(encoding="utf-8")
        mutated = spec.replace(
            """preinvokeReady ==
    /\\ step = 1
    /\\ callPhase = "pending"
    /\\ ~preCrashed
    /\\ invoked = 0""",
            "preinvokeReady == FALSE",
            1,
        )
        self.assertNotEqual(mutated, spec)
        cfg = HELD_CFG + "INVARIANT WitnessOrderedResume\n"
        result = tlc_on(mutated, cfg)
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, output[-800:])
        self.assertIn("No error has been found", output)
        check = load_check()
        with self.assertRaises(SystemExit) as caught:
            check.judge(result, "WitnessOrderedResume")
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
