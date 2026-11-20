#!/usr/bin/env python3

from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from verification.tlc_jar import jar_path

HELD = (
    "OneSearchProvider",
    "ChatModelUnchanged",
)

BREAKS = {
    "break_provider": ("breakProvider", "OneSearchProvider"),
    "break_model": ("breakModel", "ChatModelUnchanged"),
}


def fail_model(message: str) -> NoReturn:
    print(f"modeling error: {message}", file=sys.stderr)
    raise SystemExit(2)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail_model(message)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def between(text: str, start: str, end: str) -> str:
    at = text.find(start)
    require(at != -1, f"missing {start}")
    stop = text.find(end, at + len(start))
    require(stop != -1, f"missing {end}")
    return text[at:stop]


def _call_name(node: ast.AST) -> str:
    if not isinstance(node, ast.Call):
        return ""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _overload(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "overload":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "overload":
            return True
    return False


def _function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if _overload(node):
                continue
            return node
    fail_model(f"missing {name}")


def _parse(relative: str) -> ast.AST:
    try:
        return ast.parse(read(relative))
    except SyntaxError as exc:
        fail_model(f"{relative} did not parse: {exc}")


def require_chat_query() -> None:
    found = 0
    for node in ast.walk(_parse("backend/app/agents/workflow.py")):
        test = getattr(node, "test", None)
        if not (
            isinstance(node, ast.If)
            and isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "tool"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and len(test.comparators) == 1
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value == "web"
        ):
            continue
        found += 1
        require(len(node.body) == 1, "the chat web branch is no longer only the raw query")
        stmt = node.body[0]
        value = stmt.value if isinstance(stmt, ast.Assign) else None
        key = value.keys[0] if isinstance(value, ast.Dict) and len(value.keys) == 1 else None
        query = value.values[0] if isinstance(value, ast.Dict) and len(value.values) == 1 else None
        require(
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "tool_args"
            and isinstance(key, ast.Constant)
            and key.value == "query"
            and isinstance(query, ast.Name)
            and query.id == "msg",
            "the chat web query is no longer the raw message",
        )
    require(found == 1, "the chat path does not have one web branch")


def _assigned_call(fn: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            targets: list[ast.expr] = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if value is None:
            continue
        if any(isinstance(target, ast.Name) and target.id == "call" for target in targets):
            found.append(value)
    return found


def _executor_choice(value: ast.AST) -> bool:
    return (
        isinstance(value, ast.IfExp)
        and isinstance(value.test, ast.Name)
        and value.test.id == "job"
        and _call_name(value.body) == "resolve_executor"
        and _call_name(value.orelse) == "resolve"
    )


def require_executor_calls() -> None:
    llm = _parse("backend/app/core/llm.py")
    for name in ("stream_chat", "complete_text"):
        assigned = _assigned_call(_function(llm, name))
        require(
            len(assigned) == 1 and _executor_choice(assigned[0]),
            f"{name} no longer calls resolve_executor for a job",
        )
    request = _function(_parse("backend/app/tasks/job.py"), "_request")
    names = [_call_name(node) for node in ast.walk(request) if isinstance(node, ast.Call)]
    require("resolve_executor" in names, "the job request no longer calls resolve_executor")
    require("resolve" not in names, "the job request calls resolve")


def require_no_legacy_caller() -> None:
    root = ROOT / "backend" / "app"
    own = (root / "services" / "web_search.py").resolve()
    for path in root.rglob("*.py"):
        if path.resolve() == own:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            fail_model(f"{path.relative_to(ROOT)} did not parse: {exc}")
        for node in ast.walk(tree):
            name = _call_name(node)
            if name in {"search_api", "search_planned"}:
                fail_model(f"{path.relative_to(ROOT)} calls {name}")


def _restore_env(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def require_executor_result() -> None:
    from app.core.llm import Role, resolve, resolve_executor
    from app.core.config import settings

    saved = {
        "EXECUTOR_MODEL": os.environ.get("EXECUTOR_MODEL"),
        "EXECUTOR_THINKING": os.environ.get("EXECUTOR_THINKING"),
    }
    marker = "executor-only"
    if settings.chat_model == marker:
        marker = "executor-only-2"
    try:
        os.environ.pop("EXECUTOR_MODEL", None)
        os.environ.pop("EXECUTOR_THINKING", None)
        copied = resolve_executor()
        chat = resolve(Role.CHAT)
        require(copied.model == chat.model, "resolve_executor no longer copies the chat model")
        require(copied.role is Role.CHAT, "resolve_executor no longer returns the chat role")
        os.environ["EXECUTOR_MODEL"] = marker
        os.environ["EXECUTOR_THINKING"] = "off"
        chosen = resolve_executor()
        require(chosen.model == marker, "resolve_executor no longer returns EXECUTOR_MODEL")
        require(chosen.thinking is False, "resolve_executor ignores EXECUTOR_THINKING")
        require(chosen.role is Role.CHAT, "resolve_executor no longer returns the chat role")
        require(resolve(Role.CHAT).model != marker, "the chat role reads EXECUTOR_MODEL")
    finally:
        _restore_env(saved)


class _Response:
    def __init__(self, status: int, payload: object):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Capture:
    def __init__(self) -> None:
        self.clients = 0
        self.posts: list[dict] = []
        self.queue: list[tuple[int, object]] = []
        self.raises: BaseException | None = None

    def install(self, module) -> None:
        capture = self
        original = module.httpx.Client

        class Client:
            def __init__(self, *args, **kwargs):
                capture.clients += 1

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, headers=None):
                capture.posts.append({"url": url, "json": json, "headers": headers})
                if capture.raises is not None:
                    raise capture.raises
                if not capture.queue:
                    return _Response(200, {"results": []})
                status, payload = capture.queue.pop(0)
                return _Response(status, payload)

        module.httpx.Client = Client
        self._module = module
        self._original = original

    def restore(self) -> None:
        self._module.httpx.Client = self._original


def require_search_posts() -> None:
    import httpx

    from app.agents.harness.types import ToolContext
    from app.agents.tools.web import execute_web
    from app.core.config import settings
    from app.services import deepseek_search as search_mod
    from app.services.deepseek_search import ToolError, deepseek_search
    from app.services import web_search as legacy

    called: list[str] = []

    def boom(name: str):
        def inner(*_args, **_kwargs):
            called.append(name)

        return inner

    capture = _Capture()
    capture.install(search_mod)
    original_legacy = (legacy.search_api, legacy.search_planned)
    legacy.search_api = boom("search_api")
    legacy.search_planned = boom("search_planned")
    saved_settings = {
        "llm_base_url": settings.llm_base_url,
        "llm_api_key": settings.llm_api_key,
        "llm_timeout": settings.llm_timeout,
        "web_search_count": settings.web_search_count,
    }
    try:
        settings.llm_base_url = "https://llm.example/"
        settings.llm_api_key = "llm-secret"
        settings.llm_timeout = 17.0
        settings.web_search_count = 5
        ctx = ToolContext(db=object(), message="不要把这句话当成检索词", history=[], user_id=1)
        query = "weather"
        for ori in ("x" * 129, "   ", "", 12):
            before_clients = capture.clients
            before_posts = len(capture.posts)
            capture.queue.append((200, {"results": []}))
            page = deepseek_search(query, ori_question=ori)
            require(not isinstance(page, ToolError), "an invalid ori_question cancels the query")
            require(page.results == (), "an invalid ori_question did not post the query")
            _require_one_post(capture, before_clients, before_posts, "an invalid ori_question")
            _require_posted(capture.posts[-1], query)
            before_clients = capture.clients
            before_posts = len(capture.posts)
            capture.queue.append((200, {"results": []}))
            result = execute_web(ctx, {"query": query, "ori_question": ori})
            require(result.ok is True, "an invalid ori_question cancels execute_web")
            require(result.direct_answer == "没有找到结果。", "an invalid ori_question did not post the query")
            _require_one_post(capture, before_clients, before_posts, "an invalid ori_question")
            _require_posted(capture.posts[-1], query)

        before_clients = capture.clients
        before_posts = len(capture.posts)
        capture.queue.append((200, {"results": []}))
        execute_web(ctx, {"query": "news", "ori_question": "  original"})
        _require_one_post(capture, before_clients, before_posts, "a bounded ori_question")
        body = capture.posts[-1]["json"]
        require(body.get("ori_question") == "  original", "a bounded ori_question was dropped")
        require(
            set(body) == {"query", "count", "need_content", "ori_question"},
            "a bounded ori_question changed the search body",
        )

        before_clients = capture.clients
        before_posts = len(capture.posts)
        capture.queue.append((200, {"results": []}))
        once = execute_web(ctx, {"query": "today weather"})
        require(once.ok is True, "a search query did not post")
        _require_one_post(capture, before_clients, before_posts, "a search")
        _require_posted(capture.posts[-1], "today weather")
        require(
            capture.posts[-1]["json"]["query"] != ctx.message,
            "the posted query is the chat context instead of the argument",
        )

        capture.raises = httpx.TimeoutException("slow")
        before_clients = capture.clients
        before_posts = len(capture.posts)
        timed = execute_web(ctx, {"query": "news"})
        _require_one_post(capture, before_clients, before_posts, "a timeout")
        require(timed.ok is False, "a timeout was treated as a result")
        capture.raises = None

        before_clients = capture.clients
        before_posts = len(capture.posts)
        unbounded = deepseek_search("a" * 129)
        refused = execute_web(ctx, {"query": "a" * 129})
        require(isinstance(unbounded, ToolError), "an unbounded query was posted")
        require(refused.ok is False, "an unbounded query was posted")
        require(capture.clients == before_clients, "an unbounded query posted")
        require(len(capture.posts) == before_posts, "an unbounded query posted")
        require(called == [], "execute_web calls search_api or search_planned")
    finally:
        capture.raises = None
        capture.restore()
        legacy.search_api, legacy.search_planned = original_legacy
        for name, value in saved_settings.items():
            setattr(settings, name, value)


def _require_one_post(capture: _Capture, before_clients: int, before_posts: int, what: str) -> None:
    clients = capture.clients - before_clients
    posts = len(capture.posts) - before_posts
    require(clients == 1 and posts == 1, f"{what} posted {max(clients, posts)} times")


def _require_posted(posted: dict, query: str) -> None:
    require(posted["url"] == "https://llm.example/v1/tools/search", "the search post left /v1/tools/search")
    require("web_search" not in posted["url"], "the search post uses the legacy web_search path")
    body = posted["json"]
    require(body.get("query") == query, "the posted query is not the argument")
    require(body.get("need_content") is True, "the search post dropped need_content")
    require(body.get("count") == 5, "the search post dropped the configured count")
    require(set(body) == {"query", "count", "need_content"}, "the search body gained a field")


def executed_cases() -> None:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    require_chat_query()
    require_executor_calls()
    require_no_legacy_caller()
    try:
        require_executor_result()
        require_search_posts()
    except SystemExit:
        raise
    except Exception as exc:
        fail_model(f"the executed search cases could not run: {type(exc).__name__}: {exc}")


def anchors() -> None:
    web = read("backend/app/agents/tools/web.py")
    execute = between(web, "def execute_web(", "def execute_web_fetch(")
    require("deepseek_search(" in execute, "execute_web does not call deepseek_search")
    require("_web_search_url" not in execute, "execute_web contains _web_search_url")
    job = read("backend/app/tasks/job.py")
    require("_web_search_url" not in job, "job.py contains _web_search_url")
    module = read("backend/app/services/deepseek_search.py")
    require("localization" not in module, "deepseek search sends localization")
    executed_cases()


def write_cfg(directory: Path, flags: dict[str, bool]) -> Path:
    lines = ["SPECIFICATION Spec", "CONSTANT"]
    for name, (constant, _label) in BREAKS.items():
        lines.append(f"  {constant} = {'TRUE' if flags[name] else 'FALSE'}")
    for name in HELD:
        lines.append(f"INVARIANT {name}")
    lines.append("")
    path = directory / "Search.cfg"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_tlc(flags: dict[str, bool]) -> subprocess.CompletedProcess[str]:
    if shutil.which("java") is None:
        fail_model("java is not on PATH")
    with tempfile.TemporaryDirectory(prefix="search-tlc-") as tmp:
        meta = Path(tmp)
        shutil.copy(SPEC_DIR / "Search.tla", meta / "Search.tla")
        cfg = write_cfg(meta, flags)
        return subprocess.run(
            [
                "java",
                "-XX:+UseParallelGC",
                "-cp",
                str(jar_path()),
                "tlc2.TLC",
                "-workers",
                "1",
                "-nowarning",
                "-config",
                str(cfg),
                "-metadir",
                str(meta),
                "Search.tla",
            ],
            cwd=meta,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the search model.")
    parser.add_argument("--break-provider", action="store_true")
    parser.add_argument("--break-model", action="store_true")
    args = parser.parse_args()
    flags = {name: bool(getattr(args, name)) for name in BREAKS}
    chosen = [name for name, enabled in flags.items() if enabled]
    if len(chosen) > 1:
        fail_model("pass only one break flag")
    anchors()
    try:
        result = run_tlc(flags)
    except subprocess.TimeoutExpired:
        fail_model("TLC timed out")
    output = (result.stdout or "") + (result.stderr or "")
    print(output)
    if chosen:
        label = BREAKS[chosen[0]][1]
        if label not in output or "No error has been found" in output:
            fail_model(f"the broken model did not violate {label}")
        raise SystemExit(1)
    if result.returncode != 0 or "No error has been found" not in output:
        fail_model("the held run failed")


if __name__ == "__main__":
    main()
