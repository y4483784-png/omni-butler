from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

os.environ["DATABASE_URL"] = "sqlite:////tmp/loop03-owner-repro.db"
os.environ["REDIS_ENABLED"] = "false"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
os.environ["SANDBOX_RUNNER_URL"] = ""
os.environ["SECRET_KEY"] = "repro-secret"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "repro-admin-pass"
os.environ["RAG_HYBRID_ENABLED"] = "false"
os.environ["VISION_OCR_ENABLED"] = "false"
os.environ["RAG_RERANK_ENABLED"] = "false"
os.environ["LLM_API_KEY"] = ""

db_path = Path("/tmp/loop03-owner-repro.db")
if os.environ.get("LOOP03_REUSE") != "1" and db_path.exists():
    db_path.unlink()

from app.agents.dispatch import TalkReply  # noqa: E402
from app.agents.harness.types import ToolResult  # noqa: E402
from app.api.chat import _apply_talk  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import SessionLocal, init_db, set_rls_context  # noqa: E402
from app.core.llm import ModelTurn, ToolCallSpec  # noqa: E402
from app.models.models import Message, TaskRun, TaskStep, ToolCall, User  # noqa: E402
from app.models.models import Session as ChatSession  # noqa: E402
from app.services import task_run  # noqa: E402
from app.services.resume import enqueue_job  # noqa: E402
from app.storage import factory as blob_factory  # noqa: E402
from app.tasks.job import run_job, set_executor  # noqa: E402
from app.tasks.watchdog import run_sweep  # noqa: E402

settings.llm_timeout = 3600.0
init_db()
_store = MagicMock()
_store.put.side_effect = lambda key, _data: key
_store.exists.return_value = False
blob_factory._store = _store

LOG = ROOT / "verification" / "loop-owner" / "reproduction-log.txt"
HOLD_SECONDS = 20


class BarrierInvoke:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def __call__(self, name, _ctx, _args):
        with self.lock:
            row = {
                "n": len(self.calls) + 1,
                "name": name,
                "first_still_inside": not self.release.is_set(),
            }
            self.calls.append(row)
            n = row["n"]
        if n == 1:
            self.entered.set()
            self.release.wait(HOLD_SECONDS)
        return ToolResult(direct_answer=f"answer-{n}")


def _user() -> int:
    db = SessionLocal()
    try:
        external_id = f"loop03-{uuid4().hex}"
        row = User(external_id=external_id, name=external_id, is_active=1, password_hash="")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def _session(user_id: int) -> int:
    db = SessionLocal()
    try:
        row = ChatSession(user_id=user_id, title="loop03")
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def _job(user_id: int, session_id: int) -> dict:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        job = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.session_id == session_id, TaskRun.kind == "job")
            .order_by(TaskRun.generation.asc())
            .all()
        )
        jobs = []
        for row in job:
            step = (
                db.query(TaskStep)
                .filter(TaskStep.task_run_id == row.id)
                .order_by(TaskStep.seq.desc())
                .first()
            )
            calls = []
            if step is not None:
                found = (
                    db.query(ToolCall)
                    .filter(ToolCall.task_step_id == step.id)
                    .order_by(ToolCall.position.asc())
                    .all()
                )
                calls = [
                    {
                        "id": item.tool_call_id,
                        "name": item.name,
                        "state": item.state,
                        "intent_version": int(item.intent_version or 0),
                        "result": item.result,
                    }
                    for item in found
                ]
            notes = [
                item.content
                for item in db.query(Message)
                .filter(Message.session_id == row.session_id, Message.role == "assistant")
                .order_by(Message.id.asc())
                .all()
            ]
            jobs.append(
                {
                    "generation": int(row.generation),
                    "status": row.status,
                    "goal": row.goal or "",
                    "intent_version": int(row.intent_version or 0),
                    "exec_epoch": int(row.exec_epoch or 0),
                    "exec_token": row.exec_token,
                    "hold_until": None if row.hold_until is None else row.hold_until.isoformat(),
                    "web_constraint": row.web_constraint,
                    "continuation_due": bool(row.continuation_due),
                    "dispatch_token": row.dispatch_token,
                    "phase": None if step is None else step.phase,
                    "calls": calls,
                    "notes": notes,
                }
            )
        return {"jobs": jobs}
    finally:
        db.close()


def _publish_held(user_id: int, session_id: int, goal: str) -> tuple:
    published: list[tuple] = []

    def capture(*args, **_kwargs):
        published.append(tuple(args))

    with patch("app.tasks.job.run_job.delay", capture):
        db = SessionLocal()
        try:
            set_rls_context(db, user_id)
            enqueue_job(db, user_id=user_id, session_id=session_id, goal=goal, web="allow")
        finally:
            db.close()
    if len(published) != 1:
        raise RuntimeError(f"expected one dispatch, saw {published!r}")
    return published[0]


def _clear_due(user_id: int, session_id: int) -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.session_id == session_id, TaskRun.kind == "job")
            .one()
        )
        row.continuation_due = False
        db.commit()
    finally:
        db.close()


def _expire_hold(user_id: int, session_id: int) -> None:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        row = (
            db.query(TaskRun)
            .filter(TaskRun.user_id == user_id, TaskRun.session_id == session_id, TaskRun.kind == "job")
            .one()
        )
        row.hold_until = datetime(2000, 1, 1)
        row.dispatch_until = datetime(2000, 1, 1)
        db.commit()
    finally:
        db.close()


def _supplement(user_id: int, session_id: int, generation: int, text: str, web: str = "allow") -> dict:
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        relation, task_id = _apply_talk(
            db,
            user_id=user_id,
            session_id=session_id,
            relation=TalkReply(relation="supplement", target_generation=generation, web=web),
            message=text,
            message_id=None,
        )
        return {"relation": relation.relation, "task_id": task_id, "web": web}
    finally:
        db.close()


def _drive_until_tool(user_id: int, session_id: int, goal: str, barrier: BarrierInvoke) -> tuple[threading.Thread, tuple]:
    args = _publish_held(user_id, session_id, goal)
    box: dict = {}

    def run() -> None:
        try:
            run_job(*args)
        except Exception as exc:
            box["error"] = f"{type(exc).__name__}: {exc}"
            box["trace"] = traceback.format_exc()

    thread = threading.Thread(target=run, name=f"worker-{goal}", daemon=True)
    thread.start()
    if not barrier.entered.wait(HOLD_SECONDS):
        barrier.release.set()
        thread.join(HOLD_SECONDS)
        detail = box.get("trace") or box.get("error") or "no worker error"
        raise RuntimeError(f"{goal} did not enter the tool\n{detail}")
    return thread, args


def _finish(thread: threading.Thread, barrier: BarrierInvoke) -> None:
    barrier.release.set()
    thread.join(HOLD_SECONDS)


def _default_executor(model_calls: list[str]):
    def executor(messages, _tools):
        text = ""
        for item in messages:
            if isinstance(item, dict) and item.get("role") == "user":
                text = str(item.get("content") or "")
        model_calls.append(text)
        if "beta-job" in text:
            return ModelTurn("other job", "", (), "stop")
        if any(isinstance(item, dict) and item.get("role") == "tool" for item in messages):
            return ModelTurn("done", "", (), "stop")
        return ModelTurn("", "", (ToolCallSpec("call-a", "kb", "{}"),), "tool_calls")

    return executor


def _quiesce_jobs() -> None:
    db = SessionLocal()
    try:
        rows = db.query(TaskRun).filter(TaskRun.kind == "job", TaskRun.status == "running").all()
        for row in rows:
            row.status = "stopped"
            row.exec_token = None
            row.hold_until = None
            row.continuation_due = False
        db.commit()
    finally:
        db.close()


def _scenario(name: str, body, executor_factory=None) -> dict:
    _quiesce_jobs()
    barrier = BarrierInvoke()
    model_calls: list[str] = []
    executor = _default_executor(model_calls) if executor_factory is None else executor_factory(model_calls)
    set_executor(executor)
    try:
        with patch("app.agents.harness.gateway.invoke", barrier):
            detail = body(barrier)
    except Exception as exc:
        detail = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()}
    finally:
        barrier.release.set()
        set_executor(None)
    detail["invokes"] = barrier.calls
    detail["model_calls"] = model_calls
    detail["name"] = name
    return detail


def case_same_token(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, args = _drive_until_tool(user_id, session_id, "same-token", barrier)
    during = _job(user_id, session_id)
    run_job(*args)
    after_duplicate = _job(user_id, session_id)
    _finish(thread, barrier)
    return {
        "during": during,
        "after_duplicate": after_duplicate,
        "final": _job(user_id, session_id),
    }


def case_supplement(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "supplement", barrier)
    during = _job(user_id, session_id)
    generation = during["jobs"][0]["generation"]
    talk = _supplement(user_id, session_id, generation, " plus more")
    after_talk = _job(user_id, session_id)
    run_sweep()
    after_sweep = _job(user_id, session_id)
    _finish(thread, barrier)
    return {"talk": talk, "during": during, "after_talk": after_talk, "after_sweep": after_sweep, "final": _job(user_id, session_id)}


def case_due_cleared(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "due-cleared", barrier)
    during = _job(user_id, session_id)
    generation = during["jobs"][0]["generation"]
    talk = _supplement(user_id, session_id, generation, " plus more")
    _clear_due(user_id, session_id)
    after_clear = _job(user_id, session_id)
    run_sweep()
    after_sweep = _job(user_id, session_id)
    _finish(thread, barrier)
    return {"talk": talk, "during": during, "after_clear": after_clear, "after_sweep": after_sweep, "final": _job(user_id, session_id)}


def case_expired_hold(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "expired-hold", barrier)
    during = _job(user_id, session_id)
    _expire_hold(user_id, session_id)
    expired = _job(user_id, session_id)
    run_sweep()
    after_sweep = _job(user_id, session_id)
    _finish(thread, barrier)
    return {"during": during, "expired": expired, "after_sweep": after_sweep, "final": _job(user_id, session_id)}


def case_cancel(barrier: BarrierInvoke) -> dict:
    from app.services import task_run

    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "cancel", barrier)
    during = _job(user_id, session_id)
    task_run.stop(user_id=user_id, generation=during["jobs"][0]["generation"])
    after_stop = _job(user_id, session_id)
    _finish(thread, barrier)
    return {"during": during, "after_stop": after_stop, "final": _job(user_id, session_id)}


def case_concurrent_delivery(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    args = _publish_held(user_id, session_id, "concurrent")
    errors: list[str] = []

    def run() -> None:
        try:
            run_job(*args)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    first = threading.Thread(target=run, name="worker-concurrent-a", daemon=True)
    second = threading.Thread(target=run, name="worker-concurrent-b", daemon=True)
    first.start()
    second.start()
    if not barrier.entered.wait(HOLD_SECONDS):
        barrier.release.set()
        first.join(HOLD_SECONDS)
        second.join(HOLD_SECONDS)
        raise RuntimeError("concurrent did not enter the tool " + " ".join(errors))
    second.join(1)
    during = _job(user_id, session_id)
    barrier.release.set()
    first.join(HOLD_SECONDS)
    second.join(HOLD_SECONDS)
    return {"during": during, "final": _job(user_id, session_id), "errors": errors}


def _wait_until(predicate, seconds: float) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def case_pulse_tick(barrier: BarrierInvoke) -> dict:
    settings.llm_timeout = 0.4
    renews: list[int] = []
    real_renew = task_run.renew_hold

    def wrapped(*args, **kwargs):
        taken = real_renew(*args, **kwargs)
        renews.append(int(taken))
        return taken

    try:
        with patch.object(task_run, "renew_hold", wrapped):
            user_id = _user()
            session_id = _session(user_id)
            thread, _args = _drive_until_tool(user_id, session_id, "pulse-tick", barrier)
            saw_live = _wait_until(lambda: 1 in renews, 5)
            during = _job(user_id, session_id)
            generation = during["jobs"][0]["generation"]
            talk = _supplement(user_id, session_id, generation, " plus more")
            saw_miss = _wait_until(lambda: 0 in renews, 5)
            after_talk = _job(user_id, session_id)
            run_sweep()
            after_sweep = _job(user_id, session_id)
            _finish(thread, barrier)
            return {
                "talk": talk,
                "saw_live_renew": saw_live,
                "saw_missed_renew": saw_miss,
                "llm_timeout": 0.4,
                "renews": list(renews),
                "during": during,
                "after_talk": after_talk,
                "after_sweep": after_sweep,
                "final": _job(user_id, session_id),
            }
    finally:
        settings.llm_timeout = 3600.0


def _two_call_executor(model_calls: list[str]):
    def executor(messages, _tools):
        text = ""
        for item in messages:
            if isinstance(item, dict) and item.get("role") == "user":
                text = str(item.get("content") or "")
        model_calls.append(text)
        if any(isinstance(item, dict) and item.get("role") == "tool" for item in messages):
            return ModelTurn("done", "", (), "stop")
        return ModelTurn(
            "",
            "",
            (ToolCallSpec("call-a", "kb", "{}"), ToolCallSpec("call-b", "web", "{}")),
            "tool_calls",
        )

    return executor


def case_two_call_forbid(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "two-call", barrier)
    during = _job(user_id, session_id)
    generation = during["jobs"][0]["generation"]
    talk = _supplement(user_id, session_id, generation, " plus forbid", web="forbid")
    after_talk = _job(user_id, session_id)
    run_sweep()
    after_sweep = _job(user_id, session_id)
    _finish(thread, barrier)
    return {
        "talk": talk,
        "during": during,
        "after_talk": after_talk,
        "after_sweep": after_sweep,
        "final": _job(user_id, session_id),
    }


def _death_child(args: tuple, ready: str, hold: str) -> None:
    def executor(messages, _tools):
        if any(isinstance(item, dict) and item.get("role") == "tool" for item in messages):
            return ModelTurn("done", "", (), "stop")
        return ModelTurn("", "", (ToolCallSpec("call-a", "kb", "{}"),), "tool_calls")

    def invoke(name, _ctx, _args):
        Path(ready).write_text(name, encoding="utf-8")
        deadline = time.time() + 30
        while time.time() < deadline and not Path(hold).exists():
            time.sleep(0.05)
        return ToolResult(direct_answer="answer-dead")

    set_executor(executor)
    with patch("app.agents.harness.gateway.invoke", invoke):
        run_job(*args)


def case_process_death(barrier: BarrierInvoke) -> dict:
    import multiprocessing as mp

    user_id = _user()
    session_id = _session(user_id)
    args = _publish_held(user_id, session_id, "process-death")
    token = uuid4().hex
    ready = f"/tmp/loop03-death-{token}.ready"
    hold = f"/tmp/loop03-death-{token}.hold"
    os.environ["LOOP03_REUSE"] = "1"
    proc = mp.get_context("spawn").Process(target=_death_child, args=(args, ready, hold))
    proc.start()
    entered = _wait_until(lambda: Path(ready).exists(), HOLD_SECONDS)
    if not entered:
        proc.kill()
        proc.join(5)
        raise RuntimeError(f"child did not enter the tool, exitcode {proc.exitcode}")
    during = _job(user_id, session_id)
    proc.kill()
    proc.join(5)
    after_kill = _job(user_id, session_id)
    published: list[tuple] = []

    def capture(*capture_args, **_kwargs):
        published.append(tuple(capture_args))

    with patch("app.tasks.job.run_job.delay", capture):
        run_sweep()
    after_live_sweep = _job(user_id, session_id)
    barrier.release.set()
    _expire_hold(user_id, session_id)
    run_sweep()
    return {
        "exitcode": proc.exitcode,
        "signaled": proc.exitcode is not None and proc.exitcode < 0,
        "signal": None if proc.exitcode is None or proc.exitcode >= 0 else -int(proc.exitcode),
        "during": during,
        "after_kill": after_kill,
        "live_sweep_published": len(published),
        "after_live_sweep": after_live_sweep,
        "after_lease_expire_sweep": _job(user_id, session_id),
        "lease_is_not_death": True,
    }


def case_new_job(barrier: BarrierInvoke) -> dict:
    user_id = _user()
    session_id = _session(user_id)
    thread, _args = _drive_until_tool(user_id, session_id, "first-job", barrier)
    db = SessionLocal()
    try:
        set_rls_context(db, user_id)
        enqueue_job(db, user_id=user_id, session_id=session_id, goal="beta-job", web="allow")
    finally:
        db.close()
    during = _job(user_id, session_id)
    _finish(thread, barrier)
    return {"during": during, "final": _job(user_id, session_id)}


def _overlap(result: dict) -> bool:
    return any(item.get("n", 0) > 1 and item.get("first_still_inside") for item in result.get("invokes", []))


def _invoke_count(result: dict) -> int:
    return len(result.get("invokes", []))


def main() -> int:
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cases = [
        ("same_token_second_delivery", case_same_token, None),
        ("concurrent_same_token", case_concurrent_delivery, None),
        ("supplement_then_sweep", case_supplement, None),
        ("supplement_due_forced_false_then_sweep", case_due_cleared, None),
        ("expired_hold_then_sweep", case_expired_hold, None),
        ("explicit_cancel_during_tool", case_cancel, None),
        ("new_job_during_tool", case_new_job, None),
        ("pulse_tick_then_supplement", case_pulse_tick, None),
        ("two_call_forbid_sibling", case_two_call_forbid, _two_call_executor),
        ("process_death_sigkill", case_process_death, None),
    ]
    results = []
    control_failed = False
    defect = False
    for name, body, factory in cases:
        result = _scenario(name, body, factory)
        result["overlapped"] = _overlap(result)
        result["invoke_count"] = _invoke_count(result)
        results.append(result)
        if result.get("error"):
            control_failed = True
        elif name in {
            "same_token_second_delivery",
            "concurrent_same_token",
            "explicit_cancel_during_tool",
            "new_job_during_tool",
        }:
            if result["overlapped"] or result["invoke_count"] != 1:
                control_failed = True
        elif name == "pulse_tick_then_supplement":
            if not result.get("saw_live_renew") or not result.get("saw_missed_renew"):
                control_failed = True
            elif result["overlapped"]:
                defect = True
            else:
                control_failed = True
        elif name == "two_call_forbid_sibling":
            names = [item.get("name") for item in result.get("invokes", [])]
            if not names or names[0] != "kb":
                control_failed = True
            else:
                defect = True
        elif name == "process_death_sigkill":
            if result.get("exitcode") != -9 or result.get("live_sweep_published") != 0:
                control_failed = True
            else:
                killed = (result.get("after_kill") or {}).get("jobs") or []
                if not killed or killed[0].get("status") != "running":
                    control_failed = True
                else:
                    defect = True
        elif result["overlapped"]:
            defect = True
        else:
            control_failed = True

    lines = [
        f"started {started}",
        "base 19867c51ac4e29c1ca16174668925133e4d76f9f",
        "command python verification/loop-owner/reproduce_overlap.py",
        "cwd backend, database sqlite:////tmp/loop03-owner-repro.db",
        f"llm_timeout {settings.llm_timeout}",
        "pulse_tick_then_supplement sets llm_timeout 0.4 for that case only",
        "expired_hold_then_sweep leaves the first worker alive",
        "process_death_sigkill kills the child with SIGKILL and records exitcode",
        "",
    ]
    for result in results:
        lines.append(f"CASE {result['name']} invoke_count={result['invoke_count']} overlapped={result['overlapped']}")
        lines.append(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        lines.append("")
    if control_failed:
        code = 2
        lines.append("RESULT control_failed")
    elif defect:
        code = 1
        lines.append("RESULT defect_reproduced")
    else:
        code = 0
        lines.append("RESULT no_defect")
    db = SessionLocal()
    try:
        stored = [
            {
                "task_run_id": int(step.task_run_id),
                "seq": int(step.seq),
                "phase": step.phase,
                "call": call.tool_call_id,
                "state": call.state,
                "result": call.result,
            }
            for step in db.query(TaskStep).order_by(TaskStep.id.asc()).all()
            for call in db.query(ToolCall).filter(ToolCall.task_step_id == step.id).all()
        ]
    finally:
        db.close()
    lines.append("STORED " + json.dumps(stored, ensure_ascii=False))
    text = "\n".join(lines) + "\n"
    LOG.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
