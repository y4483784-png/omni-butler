#!/usr/bin/env python3
"""Static read-only lock for the 会前准备 slices."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PREP_PY = "backend/app/services/meeting_prep.py"
CALENDAR_PY = "backend/app/services/calendar.py"
CALENDAR_TOOL = "backend/app/agents/tools/calendar_tool.py"
SCHEDULE_CARD = "frontend/src/components/ScheduleCard.tsx"
CLIENT_TS = "frontend/src/api/client.ts"
BRIEF_CARD = "frontend/src/components/MeetingBriefCard.tsx"
BRIEF_WIRE = "frontend/src/utils/briefFromWire.ts"

FORBIDDEN = (
    "create_calendar_event", "_create_connected_event", "patch_event", "delete_event",
    "confirm_google_title_change", "rsvp_google_event", "create_event", "update_event",
    "cancel_event", "confirm_titled_change", "answer_invitation", "_insert_google_event",
    "_patch_google_event", "_restore_patch", "_delete_google_event_required",
    "_delete_google_event", "_save_local_row", "createCalendarEvent", "updateCalendarEvent",
    "cancelCalendarEvent", "confirmGoogleCalendarChange", "rsvpCalendarEvent",
    "send_mail", "send_message",
)
FORBIDDEN_TAILS = frozenset(FORBIDDEN)
# `/messages/send` is the send path. The list URL ends at `/messages`.
MAIL_MARKERS = ("gmail.send", "users/me/messages/send", "/messages/send")
STOP = frozenset({
    "complete_json", "clip_history_for_prompt", "scoped_access", "refresh_access_token",
    "startChatStream", "write_audit", "_persist_turn", "voidOpenDrafts", "mailtoHref",
})
# Classify at the call site. Entering `_call` would also enter writers that share it.
HTTP = frozenset({"_call", "_http_request", "fetch", "apiFetch"})
VISIBLE = (
    ("backend/app/services/calendar.py", "create_event", "py"),
    ("backend/app/services/calendar_google.py", "create_event", "py"),
    ("backend/app/services/calendar_local.py", "create_event", "py"),
    ("backend/app/api/calendar.py", "create_calendar_event", "py"),
    (CLIENT_TS, "createCalendarEvent", "ts"),
)
UPCOMING_ANCHORS = (
    ("backend/app/services/calendar_google.py", "GoogleCalendarProvider", "events_between"),
    ("backend/app/services/calendar_google.py", "GoogleCalendarProvider", "list_events"),
    ("backend/app/services/calendar_local.py", "LocalCalendarProvider", "list_events"),
)
_TS_SKIP = frozenset({
    "if", "for", "while", "switch", "catch", "function", "return", "typeof",
    "instanceof", "new", "void", "delete", "await", "import",
})
_TS_IMPORT = re.compile(
    r"import\s+(?:type\s+)?\{([^}]+)\}\s+from\s+[\"']([^\"']+)[\"']",
    re.S,
)
_CLOSER = {"<": ">", "(": ")", "[": "]", "{": "}"}


class Blind(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def parse_mode(argv: list[str]) -> str:
    known = {"--inject-write", "--inject-send", "--strip-lock"}
    if len(argv) > 1 or any(item not in known for item in argv):
        raise Blind("flags")
    return argv[0] if argv else "default"


def disk(path: str) -> str:
    full = REPO / path
    if not full.is_file():
        raise Blind(f"missing {path}")
    return full.read_text(encoding="utf-8")


def splice_prepare(source: str, expr: str) -> str:
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "prepare_meeting"), None)
    if fn is None or not fn.body:
        raise Blind("missing prepare_meeting")
    target = fn.body[0]
    if (
        isinstance(target, ast.Expr)
        and isinstance(target.value, ast.Constant)
        and isinstance(target.value.value, str)
        and len(fn.body) > 1
    ):
        target = fn.body[1]
    lines = source.splitlines(keepends=True)
    raw = lines[target.lineno - 1]
    indent = raw[: len(raw) - len(raw.lstrip(" \t"))]
    lines.insert(target.lineno - 1, f"{indent}{expr}\n")
    return "".join(lines)


def _marker(text: str) -> str:
    return next((item for item in MAIL_MARKERS if item in text), "")


def call_tail(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def call_dotted(func: ast.expr) -> str:
    parts: list[str] = []
    cur: ast.expr = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


def string_folds(tree: ast.AST) -> dict[str, str]:
    folds: dict[str, str] = {}
    for node in tree.body:  # type: ignore[attr-defined]
        name, value = "", None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name, value = node.target.id, node.value
        if name and isinstance(value, ast.Constant) and isinstance(value.value, str):
            folds[name] = value.value
    return folds


def index_py(tree: ast.AST) -> tuple[dict[str, ast.AST], dict[tuple[str, str], ast.AST]]:
    funcs: dict[str, ast.AST] = {}
    methods: dict[tuple[str, str], ast.AST] = {}

    def walk_func(node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        funcs.setdefault(node.name, node)
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk_func(child)

    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walk_func(node)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                methods[(node.name, item.name)] = item
                for child in item.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        walk_func(child)
    return funcs, methods


def resolve_py_module(module: str | None, level: int, frm: str) -> str | None:
    if level == 0:
        if not module or module != "app" and not module.startswith("app."):
            return None
        rel = Path("backend") / Path(*module.split("."))
    else:
        base = Path(frm).parent
        for _ in range(level - 1):
            base = base.parent
        rel = base / Path(*module.split(".")) if module else base
    for cand in (Path(str(rel) + ".py"), rel / "__init__.py"):
        if (REPO / cand).is_file():
            return cand.as_posix()
    return None


def py_imports(tree: ast.AST, frm: str) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for node in tree.body:  # type: ignore[attr-defined]
        if not isinstance(node, ast.ImportFrom):
            continue
        mod = resolve_py_module(node.module, node.level or 0, frm)
        if mod is None:
            continue
        for alias in node.names:
            table[alias.asname or alias.name] = (mod, alias.name)
    return table


def literal_expr(node: ast.expr | None, folds: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return folds.get(node.id)
    if not isinstance(node, ast.JoinedStr):
        return None
    buf = ""
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            buf += part.value
        elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) and part.value.id in folds:
            buf += folds[part.value.id]
        else:
            break
    return buf or None


def _joined_pieces(node: ast.JoinedStr, folds: dict[str, str]) -> list[str]:
    groups: list[str] = []
    buf = ""
    for part in node.values:
        piece = ""
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            piece = part.value
        elif isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name):
            piece = folds.get(part.value.id, "")
        if piece:
            buf += piece
            continue
        if buf:
            groups.append(buf)
        buf = ""
    if buf:
        groups.append(buf)
    return groups


def _cmp(test: ast.expr, name: str, op: type[ast.cmpop], values: set[object]) -> bool:
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == name
        and len(test.ops) == 1
        and isinstance(test.ops[0], op)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value in values
    )


def _parents(fn: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child)

    walk(fn)
    return parents


def _prep_cuts(fn: ast.AST) -> tuple[list[ast.stmt], list[ast.stmt], list[ast.stmt]]:
    parents = _parents(fn)
    prep = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.If) and id(node) in parents and _cmp(node.test, "action", ast.Eq, {"prep"})
    ]
    clocks = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.If) and id(node) in parents and _cmp(node.test, "clock_error", ast.IsNot, {None})
    ]
    if len(prep) != 1:
        raise Blind("missing prep")
    if not clocks:
        raise Blind("missing clock")
    clock_stmts = [
        stmt
        for node in clocks
        for stmt in list(node.body) + list(node.orelse)
        if not (isinstance(stmt, ast.If) and _cmp(stmt.test, "action", ast.Eq, {"list", "suggest"}))
    ]
    loads: list[ast.stmt] = []
    seen: set[int] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or call_tail(node.func) != "_load_upcoming" or id(node) not in parents:
            continue
        cur: ast.AST | None = node
        blocked = False
        while cur is not None:
            cur = parents.get(id(cur))
            if isinstance(cur, ast.If) and _cmp(cur.test, "action", ast.Eq, {"followup", "list", "suggest"}):
                blocked = True
                break
        if blocked:
            continue
        stmt: ast.AST | None = node
        while stmt is not None and not isinstance(stmt, ast.stmt):
            stmt = parents.get(id(stmt))
        if isinstance(stmt, ast.stmt) and id(stmt) not in seen:
            seen.add(id(stmt))
            loads.append(stmt)
    if len(loads) != 1:
        raise Blind("missing load")
    return list(prep[0].body), clock_stmts, loads


def judge_http(verb: str | None, url: str | None) -> tuple[str, str]:
    text = url or ""
    mark = _marker(text)
    if mark:
        return ("hit", mark)
    if not verb:
        return ("blind", "http verb")
    kind = verb.upper()
    if kind == "GET":
        return ("ok", "")
    allowed = (
        kind == "POST" and "oauth2.googleapis.com" in text and "/token" in text
        or kind == "POST" and "freeBusy" in text
        or kind == "POST" and "/api/chat" in text
    )
    if allowed:
        return ("ok", "")
    if kind in {"POST", "PATCH", "PUT", "DELETE"}:
        return ("hit", kind)
    return ("blind", "http verb")


def http_from_py(call: ast.Call, folds: dict[str, str]) -> tuple[str, str]:
    tail = call_tail(call.func)
    verb: str | None
    url: str | None
    if tail in {"_call", "_http_request"}:
        verb = literal_expr(call.args[0], folds) if call.args else None
        url = literal_expr(call.args[1], folds) if len(call.args) > 1 else None
    else:
        url = literal_expr(call.args[0], folds) if call.args else None
        verb = "GET"
        if len(call.args) > 1:
            verb = _method_of(call.args[1], folds)
    for kw in call.keywords:
        if kw.arg in {"method", "verb"}:
            verb = literal_expr(kw.value, folds)
        elif kw.arg in {"url", "input"}:
            url = literal_expr(kw.value, folds)
        elif kw.arg == "init":
            verb = _method_of(kw.value, folds)
    return judge_http(verb, url)


def _method_of(node: ast.expr, folds: dict[str, str]) -> str | None:
    if not isinstance(node, ast.Dict):
        return None
    for key, val in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == "method":
            return literal_expr(val, folds) or ""
    return "GET"


def _line_at(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _ident_start(ch: str) -> bool:
    return ch.isalpha() or ch in "_$"


def _ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch in "_$"


def _scan_string(source: str, i: int) -> int:
    quote = source[i]
    i += 1
    n = len(source)
    while i < n:
        if source[i] == "\\":
            i += 2
            continue
        if source[i] == quote:
            return i + 1
        i += 1
    return n


def atom(source: str, i: int) -> tuple[str, int, str]:
    """One code token. Templates stay a single backtick so the caller can enter them."""
    n = len(source)
    if i >= n:
        return ("end", i, "")
    c = source[i]
    if c in " \t\r\n":
        j = i + 1
        while j < n and source[j] in " \t\r\n":
            j += 1
        return ("ws", j, "")
    if c == "/" and i + 1 < n and source[i + 1] == "/":
        nl = source.find("\n", i)
        return ("comment", n if nl < 0 else nl + 1, "")
    if c == "/" and i + 1 < n and source[i + 1] == "*":
        end = source.find("*/", i + 2)
        return ("comment", n if end < 0 else end + 2, "")
    if c in "\"'":
        j = _scan_string(source, i)
        return ("str", j, source[i + 1 : j - 1])
    if c == "`":
        return ("tick", i, "")
    if _ident_start(c):
        j = i + 1
        while j < n and _ident_cont(source[j]):
            j += 1
        return ("ident", j, source[i:j])
    return ("op", i + 1, c)


def match_delim(source: str, start: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    i = start
    n = len(source)
    while i < n:
        kind, nxt, text = atom(source, i)
        if kind == "tick":
            i = _skip_template(source, i)
            continue
        if kind == "op" and text == open_ch:
            depth += 1
        elif kind == "op" and text == close_ch and depth:
            depth -= 1
            if depth == 0:
                return nxt
        i = nxt
    return -1


def _skip_template(source: str, i: int) -> int:
    i += 1
    n = len(source)
    while i < n:
        c = source[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and source[i + 1] == "{":
            end = match_delim(source, i + 1, "{", "}")
            i = n if end < 0 else end
            continue
        i += 1
    return n


def _skip_ws(source: str, i: int) -> int:
    while True:
        kind, nxt, _text = atom(source, i)
        if kind not in {"ws", "comment"}:
            return i
        i = nxt


def _advance(source: str, i: int) -> int:
    kind, nxt, _text = atom(source, i)
    return _skip_template(source, i) if kind == "tick" else nxt


def _body_brace(source: str, k: int) -> int:
    k = _skip_ws(source, k)
    n = len(source)
    if k < n and source[k] == "{":
        return k
    if k >= n or source[k] != ":":
        return -1
    k += 1
    while k < n:
        k = _skip_ws(source, k)
        if k >= n:
            return -1
        c = source[k]
        if c in "\"'`":
            k = _advance(source, k)
            continue
        if c == "{":
            end = match_delim(source, k, "{", "}")
            if end < 0:
                return k
            nxt = _skip_ws(source, end)
            if nxt < n and source[nxt] in "|&[":
                k = nxt
                continue
            if nxt < n and source[nxt] == "{":
                return nxt
            return k
        if c in "<([":
            end = match_delim(source, k, c, _CLOSER[c])
            if end < 0:
                return -1
            k = end
            continue
        k += 1
    return -1


def _call_chain(source: str, i: int, first: str) -> tuple[str, int | None]:
    parts = [first]
    n = len(source)
    k = i
    while True:
        m = _skip_ws(source, k)
        optional = source.startswith("?.", m)
        if not optional and not (m < n and source[m] == "."):
            k = m
            break
        m = _skip_ws(source, m + (2 if optional else 1))
        if m < n and _ident_start(source[m]):
            end = m + 1
            while end < n and _ident_cont(source[end]):
                end += 1
            parts.append(source[m:end])
            k = end
            continue
        if optional and m < n and source[m] == "(":
            return ".".join(parts), m
        break
    if k < n and source[k] == "(":
        return ".".join(parts), k
    return ".".join(parts), None


def collect_ts(source: str) -> tuple[list[tuple[str, int, int]], list[tuple[str, int]]]:
    calls: list[tuple[str, int, int]] = []
    strings: list[tuple[str, int]] = []
    i = 0
    n = len(source)
    while i < n:
        kind, nxt, text = atom(source, i)
        if kind == "str":
            strings.append((text, _line_at(source, i)))
            i = nxt
            continue
        if kind == "tick":
            i = _collect_template(source, i, calls, strings)
            continue
        if kind == "ident" and text not in _TS_SKIP:
            dotted, paren = _call_chain(source, nxt, text)
            if paren is not None:
                calls.append((dotted, _line_at(source, i), paren))
            i = nxt
            continue
        i = nxt
    return calls, strings


def _collect_template(source: str, i: int, calls: list, strings: list) -> int:
    start = i
    i += 1
    n = len(source)
    buf: list[str] = []
    while i < n:
        c = source[i]
        if c == "\\":
            if i + 1 < n:
                buf.append(source[i + 1])
            i += 2
            continue
        if c == "`":
            if buf:
                strings.append(("".join(buf), _line_at(source, start)))
            return i + 1
        if c == "$" and i + 1 < n and source[i + 1] == "{":
            if buf:
                strings.append(("".join(buf), _line_at(source, start)))
                buf = []
            end = match_delim(source, i + 1, "{", "}")
            if end < 0:
                return n
            base = _line_at(source, i + 2)
            sub_calls, sub_strings = collect_ts(source[i + 2 : end - 1])
            calls.extend((dotted, base + line - 1, i + 2 + paren) for dotted, line, paren in sub_calls)
            strings.extend((text, base + line - 1) for text, line in sub_strings)
            i = end
            continue
        buf.append(c)
        i += 1
    return n


def ts_functions(source: str) -> list[tuple[str, str, int]]:
    found: list[tuple[str, str, int]] = []
    i = 0
    n = len(source)
    while i < n:
        kind, nxt, text = atom(source, i)
        if kind == "tick":
            i = _skip_template(source, i)
            continue
        if kind != "ident" or text != "function":
            i = nxt
            continue
        k = _skip_ws(source, nxt)
        if k < n and source[k] == "*":
            k = _skip_ws(source, k + 1)
        if k >= n or not _ident_start(source[k]):
            i = nxt
            continue
        name_end = k + 1
        while name_end < n and _ident_cont(source[name_end]):
            name_end += 1
        name = source[k:name_end]
        k = _skip_ws(source, name_end)
        if k < n and source[k] == "<":
            k = match_delim(source, k, "<", ">")
            k = _skip_ws(source, k if k > 0 else name_end)
        if k > 0 and k < n and source[k] == "(":
            k = match_delim(source, k, "(", ")")
            brace = _body_brace(source, k) if k > 0 else -1
            if brace > 0:
                end = match_delim(source, brace, "{", "}")
                if end < 0:
                    raise Blind(f"missing {name}")
                found.append((name, source[brace:end], brace))
                i = end
                continue
        i = nxt
    return found


def _tag_boundary(source: str, i: int) -> bool:
    return i >= len(source) or not _ident_cont(source[i])


def _open_tag_end(source: str, i: int) -> int:
    j = i + 1
    n = len(source)
    depth = 0
    while j < n:
        kind, nxt, text = atom(source, j)
        if kind == "tick":
            j = _skip_template(source, j)
            continue
        if kind == "op" and text == "{":
            depth += 1
        elif kind == "op" and text == "}":
            depth -= 1
        elif kind == "op" and text == ">" and depth <= 0:
            return nxt
        j = nxt
    return -1


def _element_end(source: str, i: int, tag: str) -> int:
    open_end = _open_tag_end(source, i)
    if open_end < 0:
        return -1
    if open_end >= 2 and source[open_end - 2] == "/":
        return open_end
    depth = 1
    j = open_end
    n = len(source)
    while j < n:
        if source.startswith("</" + tag, j) and _tag_boundary(source, j + 2 + len(tag)):
            depth -= 1
            gt = source.find(">", j)
            if gt < 0:
                return -1
            j = gt + 1
            if depth == 0:
                return j
            continue
        if source.startswith("<" + tag, j) and _tag_boundary(source, j + 1 + len(tag)):
            depth += 1
        kind, nxt, _text = atom(source, j)
        j = _skip_template(source, j) if kind == "tick" else nxt
    return -1


def direct_text(element: str) -> str:
    open_end = _open_tag_end(element, 0)
    close_at = element.rfind("</button>")
    if open_end < 0 or close_at < open_end:
        return ""
    inner = element[open_end:close_at]
    parts: list[str] = []
    i = 0
    n = len(inner)
    while i < n:
        if inner[i] == "<" and not inner.startswith("</", i):
            name_end = i + 1
            while name_end < n and _ident_cont(inner[name_end]):
                name_end += 1
            end = _element_end(inner, i, inner[i + 1 : name_end])
            i = n if end < 0 else end
            continue
        if inner.startswith("</", i):
            gt = inner.find(">", i)
            i = n if gt < 0 else gt + 1
            continue
        if inner[i] == "{":
            end = match_delim(inner, i, "{", "}")
            i = n if end < 0 else end
            continue
        parts.append(inner[i])
        i += 1
    return "".join(parts).strip()


def prep_button(source: str) -> tuple[str, int]:
    found: tuple[str, int] | None = None
    i = 0
    n = len(source)
    while i < n:
        if source.startswith("<button", i) and _tag_boundary(source, i + 7):
            end = _element_end(source, i, "button")
            if end < 0:
                raise Blind("missing button")
            span = source[i:end]
            if direct_text(span) == "会前准备":
                if found is not None or "reply(" in span or "会后跟进" in span:
                    raise Blind("button slice")
                found = (span, i)
            i = end
            continue
        kind, nxt, _text = atom(source, i)
        i = _skip_template(source, i) if kind == "tick" else nxt
    if found is None:
        raise Blind("missing button")
    return found


def resolve_ts(frm: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    base = (REPO / frm).parent / spec
    for ext in (".ts", ".tsx"):
        cand = Path(str(base) + ext)
        if not cand.is_file():
            continue
        try:
            return cand.resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError:
            return None
    return None


def ts_import_map(frm: str, source: str) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for blob, spec in _TS_IMPORT.findall(source):
        target = resolve_ts(frm, spec)
        if target is None:
            continue
        for part in blob.split(","):
            part = part.strip()
            if not part or part.startswith("type "):
                continue
            orig, alias = [item.strip() for item in part.split(" as ", 1)] if " as " in part else (part, part)
            table[alias] = (target, orig)
    return table


def ts_folds(source: str) -> dict[str, str]:
    return {
        match.group(1): match.group(2)
        for match in re.finditer(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*[\"']([^\"']*)[\"']",
            source,
        )
    }


def http_from_ts(source: str, paren: int, tail: str, folds: dict[str, str]) -> tuple[str, str]:
    end = match_delim(source, paren, "(", ")")
    if end < 0:
        raise Blind("http verb")
    args = source[paren + 1 : end - 1]
    literals: list[str] = []
    i = 0
    while i < len(args):
        if args[i] in "\"'":
            stop = _scan_string(args, i)
            literals.append(args[i + 1 : stop - 1])
            i = stop
            continue
        i += 1
    if tail in {"_call", "_http_request"}:
        verb = literals[0] if literals else None
        url = literals[1] if len(literals) > 1 else None
        return judge_http(verb, url)
    url = literals[0] if literals else folds.get(args.strip().split(",", 1)[0].strip())
    method = re.search(r"method\s*:\s*[\"']([A-Za-z]+)[\"']", args)
    return judge_http(method.group(1) if method else "GET", url)


def _source(state: dict, path: str) -> str:
    if path not in state["sources"]:
        text = disk(path)
        overlay = state["overlays"].get(path)
        state["sources"][path] = overlay(text) if overlay else text
    return state["sources"][path]


def _py_tree(state: dict, path: str) -> ast.AST:
    source = _source(state, path)
    cached = state["py"].get(path)
    if cached and cached[0] == source:
        return cached[1]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise Blind(f"missing {path}") from exc
    state["py"][path] = (source, tree)
    return tree


def _py_symbol(state: dict, path: str, name: str) -> str:
    tree = _py_tree(state, path)
    funcs, _methods = index_py(tree)
    if name in funcs:
        return "func"
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef) and node.name == name:
            return "class"
    return "missing"


def _enqueue(state: dict, kind: str, path: str, qual: str) -> None:
    key = (kind, path, qual)
    if key in state["seen"]:
        return
    state["seen"].add(key)
    state["queue"].append(key)


def _hit(state: dict, path: str, line: int, qual: str, symbol: str) -> None:
    item = (path, line or 1, qual, symbol)
    if item not in state["hitset"]:
        state["hitset"].add(item)
        state["hits"].append(item)


def _visit_py(stmts: list[ast.stmt], folds: dict[str, str]) -> tuple[list[ast.Call], list[tuple[str, int]], set[str], set[str]]:
    calls: list[ast.Call] = []
    strings: list[tuple[str, int]] = []
    names: set[str] = set()
    attrs: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(node, ast.Call):
            calls.append(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append((node.value, getattr(node, "lineno", 1)))
        if isinstance(node, ast.JoinedStr):
            strings.extend((piece, getattr(node, "lineno", 1)) for piece in _joined_pieces(node, folds))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in folds:
            strings.append((folds[node.id], getattr(node, "lineno", 1)))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and call_tail(node.value.func) == "prepare_meeting":
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        if isinstance(node, ast.Attribute) and node.attr in {"caption", "card"}:
            value = node.value
            if isinstance(value, ast.Name) and value.id in names or (
                isinstance(value, ast.Call) and call_tail(value.func) == "prepare_meeting"
            ):
                attrs.add(node.attr)
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in stmts:
        visit(stmt)
    return calls, strings, names, attrs


def _resolve_py_call(state: dict, path: str, qual: str, cls: str, call: ast.Call, pack: tuple) -> None:
    _tree, folds, imports, funcs, methods = pack
    tail = call_tail(call.func)
    if not tail:
        return
    line = getattr(call, "lineno", 1)
    mark = _marker(call_dotted(call.func))
    if mark or tail in FORBIDDEN_TAILS:
        _hit(state, path, line, qual, mark or tail)
        return
    if tail in HTTP:
        kind, symbol = http_from_py(call, folds)
        if kind == "blind":
            raise Blind(symbol)
        if kind == "hit":
            _hit(state, path, line, qual, symbol)
        return
    if tail in STOP:
        return
    if isinstance(call.func, ast.Name):
        if tail in funcs:
            _enqueue(state, "py", path, tail)
            return
        imported = imports.get(tail)
        if imported is None:
            return
        mod, orig = imported
        kind = _py_symbol(state, mod, orig)
        if kind == "func":
            _enqueue(state, "py", mod, orig)
        elif kind == "missing":
            raise Blind(f"missing {orig}")
        return
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and cls
        and (cls, tail) in methods
    ):
        _enqueue(state, "py", path, f"{cls}.{tail}")


def _pack(state: dict, path: str) -> tuple:
    tree = _py_tree(state, path)
    funcs, methods = index_py(tree)
    return tree, string_folds(tree), py_imports(tree, path), funcs, methods


def _consider_py(state: dict, path: str, qual: str, cls: str, stmts: list[ast.stmt]) -> None:
    pack = _pack(state, path)
    calls, strings, names, attrs = _visit_py(stmts, pack[1])
    for text, line in strings:
        mark = _marker(text)
        if mark:
            _hit(state, path, line, qual, mark)
    for call in calls:
        _resolve_py_call(state, path, qual, cls, call, pack)
    if qual == "upcoming_events" and path == CALENDAR_PY:
        has_getattr = any(
            call_tail(call.func) == "getattr"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value == "events_between"
            for call in calls
        )
        has_list = any(call_tail(call.func) == "list_events" for call in calls)
        if not has_getattr or not has_list:
            raise Blind("missing upcoming anchor")
        for anchor_path, anchor_cls, anchor_method in UPCOMING_ANCHORS:
            _funcs, methods = index_py(_py_tree(state, anchor_path))
            if (anchor_cls, anchor_method) not in methods:
                raise Blind(f"missing {anchor_cls}.{anchor_method}")
            _enqueue(state, "py", anchor_path, f"{anchor_cls}.{anchor_method}")
    if "caption" in attrs and _py_symbol(state, PREP_PY, "caption") == "func":
        _enqueue(state, "py", PREP_PY, "caption")
    if "card" in attrs and _py_symbol(state, PREP_PY, "to_wire") == "func":
        _enqueue(state, "py", PREP_PY, "to_wire")


def _consider_ts(state: dict, path: str, qual: str, body: str, base_line: int, file_source: str) -> None:
    calls, strings = collect_ts(body)
    for text, line in strings:
        mark = _marker(text)
        if mark:
            _hit(state, path, base_line + line - 1, qual, mark)
    imports = ts_import_map(path, file_source)
    names = {item[0] for item in state["ts_fns"].setdefault(file_source, ts_functions(file_source))}
    folds = ts_folds(file_source)
    for dotted, line, paren in calls:
        parts = dotted.split(".")
        tail = parts[-1]
        abs_line = base_line + line - 1
        mark = _marker(dotted)
        if mark or tail in FORBIDDEN_TAILS:
            _hit(state, path, abs_line, qual, mark or tail)
            continue
        if tail in HTTP:
            kind, symbol = http_from_ts(body, paren, tail, folds)
            if kind == "blind":
                raise Blind(symbol)
            if kind == "hit":
                _hit(state, path, abs_line, qual, symbol)
            continue
        if tail in STOP or len(parts) != 1:
            continue
        if tail in names:
            _enqueue(state, "ts", path, tail)
            continue
        imported = imports.get(tail)
        if imported is None:
            continue
        mod, orig = imported
        mod_source = _source(state, mod)
        bodies = [item for item in state["ts_fns"].setdefault(mod_source, ts_functions(mod_source)) if item[0] == orig]
        if not bodies:
            raise Blind(f"missing {orig}")
        _enqueue(state, "ts", mod, orig)


def _py_body(tree: ast.AST, qual: str) -> tuple[list[ast.stmt] | None, str]:
    funcs, methods = index_py(tree)
    if "." in qual:
        cls, name = qual.split(".", 1)
        node = methods.get((cls, name))
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return None, ""
        return list(node.body), cls
    node = funcs.get(qual)
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None, ""
    return list(node.body), ""


def _load_roots(state: dict) -> None:
    card = _source(state, SCHEDULE_CARD)
    button, start = prep_button(card)
    state["spans"][("span", SCHEDULE_CARD, "会前准备")] = (button, _line_at(card, start))
    _enqueue(state, "span", SCHEDULE_CARD, "会前准备")
    client = _source(state, CLIENT_TS)
    if not any(name == "formatPointedUtterance" for name, _body, _at in ts_functions(client)):
        raise Blind("missing formatPointedUtterance")
    _enqueue(state, "ts", CLIENT_TS, "formatPointedUtterance")
    for path in (BRIEF_CARD, BRIEF_WIRE):
        fns = ts_functions(_source(state, path))
        if not fns:
            raise Blind(f"missing functions {path}")
        for name, _body, _at in fns:
            _enqueue(state, "ts", path, name)
    tool = _py_tree(state, CALENDAR_TOOL)
    fn = next((node for node in tool.body if isinstance(node, ast.FunctionDef) and node.name == "execute_calendar"), None)
    if fn is None:
        raise Blind("missing execute_calendar")
    for label, stmts in zip(("prep", "clock", "load"), _prep_cuts(fn)):
        key = ("stmts", CALENDAR_TOOL, label)
        state["seen"].add(key)
        state["cuts"][key] = stmts
        state["queue"].append(key)
    if _py_symbol(state, PREP_PY, "prepare_meeting") != "func":
        raise Blind("missing prepare_meeting")
    _enqueue(state, "py", PREP_PY, "prepare_meeting")


def walk(overlays: dict) -> list[tuple[str, int, str, str]]:
    state: dict = {
        "overlays": overlays,
        "sources": {},
        "py": {},
        "ts_fns": {},
        "hits": [],
        "hitset": set(),
        "seen": set(),
        "queue": [],
        "cuts": {},
        "spans": {},
    }
    _load_roots(state)
    while state["queue"]:
        kind, path, qual = state["queue"].pop(0)
        if kind == "py":
            stmts, cls = _py_body(_py_tree(state, path), qual)
            if stmts is None:
                raise Blind(f"missing {qual}")
            _consider_py(state, path, qual, cls, stmts)
        elif kind == "stmts":
            _consider_py(state, path, "execute_calendar", "", state["cuts"][(kind, path, qual)])
        elif kind == "ts":
            source = _source(state, path)
            match = next((item for item in ts_functions(source) if item[0] == qual), None)
            if match is None:
                raise Blind(f"missing {qual}")
            _name, body, at = match
            _consider_ts(state, path, qual, body, _line_at(source, at), source)
        else:
            body, base = state["spans"][(kind, path, qual)]
            _consider_ts(state, path, qual, body, base, _source(state, path))
    state["hits"].sort()
    return state["hits"]


def check_visible() -> None:
    for path, name, kind in VISIBLE:
        text = disk(path)
        if kind == "py":
            funcs, methods = index_py(ast.parse(text))
            if name not in funcs and all(method != name for _cls, method in methods):
                raise Blind(f"missing {name}")
        elif all(item[0] != name for item in ts_functions(text)):
            raise Blind(f"missing {name}")


def _has(hits: list[tuple[str, int, str, str]], qual: str, symbol: str) -> bool:
    return any(item[2] == qual and item[3] == symbol for item in hits)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    try:
        mode = parse_mode(list(argv))
        if mode == "--strip-lock":
            print("blind: no roots")
            return 2
        check_visible()
        overlays = {}
        if mode == "--inject-write":
            overlays[PREP_PY] = lambda text: splice_prepare(text, "create_event()")
        elif mode == "--inject-send":
            overlays[PREP_PY] = lambda text: splice_prepare(text, "send_mail()")
        hits = walk(overlays)
        if mode == "--inject-write" and not _has(hits, "prepare_meeting", "create_event"):
            print("blind: inject")
            return 2
        if mode == "--inject-send" and not _has(hits, "prepare_meeting", "send_mail"):
            print("blind: inject")
            return 2
        if hits:
            for path, line, qual, symbol in hits:
                print(f"violation: {qual} {symbol} {path}:{line}")
            return 1
        print("pass: prep closure is read-only")
        return 0
    except Blind as exc:
        print(f"blind: {exc.reason}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
