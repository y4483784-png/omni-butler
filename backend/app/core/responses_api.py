"""DeepSeek Responses bodies for the user-facing answer stream.

The official web_search tool is a server tool on POST /responses. It runs on
deepseek-v4-pro and is ignored on deepseek-flash. tool_choice is omitted so
the model chooses. Chat Completions has no such field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.prompts import native_web_note

NATIVE_WEB_MODEL = "deepseek-v4-pro"
MAX_FUNCTION_ROUNDS = 3


def web_offered(allowed_tools: Any) -> bool:
    """Default capability is on. A turn list that omits ``web`` is the existing deny."""
    if allowed_tools is None:
        return True
    return "web" in {str(item) for item in allowed_tools}


def message_input(messages: list[dict]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            items.append({"role": "user", "content": ""})
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in ("system", "user", "assistant", "developer"):
            role = "user"
        items.append({"role": role, "content": content if isinstance(content, str) else ""})
    return items


def _function_tool(spec: Any) -> dict[str, Any] | None:
    if not isinstance(spec, dict):
        return None
    if spec.get("type") == "web_search":
        return None
    if spec.get("type") == "function" and isinstance(spec.get("name"), str):
        name = spec["name"]
        source = spec
    else:
        source = spec.get("function") if isinstance(spec.get("function"), dict) else None
        name = source.get("name") if isinstance(source, dict) else None
    if not isinstance(name, str) or not name or name == "web":
        return None
    parameters = source.get("parameters") if isinstance(source, dict) else None
    tool: dict[str, Any] = {"type": "function", "name": name}
    description = source.get("description") if isinstance(source, dict) else None
    if isinstance(description, str) and description:
        tool["description"] = description
    if isinstance(parameters, dict):
        tool["parameters"] = parameters
    return tool


def build_responses_body(
    *,
    model: str,
    thinking: bool,
    items: list[dict],
    stream: bool,
    offer_web: bool,
    function_tools: Any = None,
) -> dict[str, Any]:
    """One Responses request. ``tool_choice`` is left unset on purpose."""
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in function_tools or []:
        tool = _function_tool(spec)
        if tool is None or tool["name"] in seen:
            continue
        seen.add(tool["name"])
        tools.append(tool)
    if offer_web:
        tools.append({"type": "web_search"})
    body: dict[str, Any] = {
        "model": NATIVE_WEB_MODEL if offer_web else model,
        "input": items,
        "stream": stream,
        "reasoning": {"effort": "high" if thinking else "none"},
    }
    if offer_web:
        body["instructions"] = native_web_note()
    if tools:
        body["tools"] = tools
    return body


def replay_input(
    prior: list[dict],
    calls: list[FunctionCall],
    outputs: list[str],
    web_items: list[dict],
) -> list[dict]:
    items = list(prior)
    items.extend(web_items)
    for call, output in zip(calls, outputs):
        items.append(
            {
                "type": "function_call",
                "call_id": call.call_id,
                "name": call.name,
                "arguments": call.arguments,
            }
        )
        items.append(
            {
                "type": "function_call_output",
                "call_id": call.call_id,
                "output": output,
            }
        )
    return items


@dataclass
class FunctionCall:
    call_id: str
    name: str
    arguments: str


@dataclass
class RoundState:
    calls: dict[str, FunctionCall] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    web_items: list[dict] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    failed: str | None = None
    usage: dict[str, int] | None = None

    def record_call(self, call_id: str, name: str, arguments: str) -> None:
        if not call_id or not name or name == "web":
            return
        current = self.calls.get(call_id)
        if current is None:
            self.order.append(call_id)
            self.calls[call_id] = FunctionCall(call_id, name, arguments or "")
            return
        if arguments:
            current.arguments = arguments

    def ready_calls(self, offered_names: set[str]) -> list[FunctionCall]:
        return [
            self.calls[call_id]
            for call_id in self.order
            if self.calls[call_id].name in offered_names
        ]


def _read(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if value is None or isinstance(value, (str, int, float, bool)):
        return None
    data: dict[str, Any] = {}
    for name in ("type", "id", "call_id", "name", "arguments", "status", "action", "url", "title"):
        found = getattr(value, name, None)
        if found is not None:
            data[name] = _as_dict(found) if name == "action" else found
    return data or None


def citations_from(value: Any, found: list[dict] | None = None, seen: set[str] | None = None) -> list[dict]:
    """Keep http(s) values stored under a url key. A completed search is not a url."""
    if found is None:
        found = []
        seen = set()
    assert seen is not None
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            title = value.get("title") if isinstance(value.get("title"), str) else ""
            found.append(
                {
                    "title": title,
                    "url": url,
                    "filename": title or url,
                    "source_type": "web",
                    "snippet": title or url,
                }
            )
        for child in value.values():
            citations_from(child, found, seen)
    elif isinstance(value, list):
        for child in value:
            citations_from(child, found, seen)
    return found


def search_status_text(phase: str, query: str) -> str:
    if phase == "completed":
        return "网页搜索已结束"
    if query:
        return f"正在搜索：{query}"
    if phase == "in_progress":
        return "正在准备网页搜索"
    return "正在网页搜索"


def _query_of(event: Any) -> str:
    action = _read(event, "action")
    query = _read(action, "query") if action is not None else None
    if isinstance(query, str):
        return query
    item = _read(event, "item")
    action = _read(item, "action")
    query = _read(action, "query") if action is not None else None
    return query if isinstance(query, str) else ""


def _usage_of(response: Any) -> dict[str, int] | None:
    usage = _read(response, "usage")
    if usage is None:
        return None
    raw_in = _read(usage, "input_tokens")
    raw_out = _read(usage, "output_tokens")
    parsed: dict[str, int] = {}
    if raw_in is not None:
        try:
            parsed["prompt_tokens"] = int(raw_in)
        except (TypeError, ValueError):
            pass
    if raw_out is not None:
        try:
            parsed["completion_tokens"] = int(raw_out)
        except (TypeError, ValueError):
            pass
    return parsed or None


def _remember_item(state: RoundState, item: Any) -> None:
    if item is None:
        return
    kind = _read(item, "type")
    if kind == "web_search_call":
        stored = _as_dict(item)
        if stored is not None:
            state.web_items.append(stored)
        citations_from(stored if stored is not None else item, state.citations, _seen(state))
        return
    if kind == "function_call":
        call_id = _read(item, "call_id") or _read(item, "id") or ""
        name = _read(item, "name") or ""
        arguments = _read(item, "arguments") or ""
        if isinstance(call_id, str) and isinstance(name, str):
            state.record_call(call_id, name, arguments if isinstance(arguments, str) else "")
    if kind == "message":
        citations_from(_as_dict(item) or item, state.citations, _seen(state))


def _seen(state: RoundState) -> set[str]:
    return {item["url"] for item in state.citations if isinstance(item.get("url"), str)}


def observe(event: Any, state: RoundState) -> list[tuple[str, str]]:
    """Return (kind, text) pairs. kind is reasoning, answer, or search.

    A failed response sets ``state.failed`` and yields nothing from that event.
    Citations stay on the state until the caller asks for them.
    """
    kind = _read(event, "type")
    if kind == "response.reasoning_text.delta":
        text = _read(event, "delta")
        return [("reasoning", text)] if isinstance(text, str) and text else []
    if kind == "response.output_text.delta":
        text = _read(event, "delta")
        return [("answer", text)] if isinstance(text, str) and text else []
    if kind in (
        "response.web_search_call.in_progress",
        "response.web_search_call.searching",
        "response.web_search_call.completed",
    ):
        phase = kind.rsplit(".", 1)[-1]
        return [("search", search_status_text(phase, _query_of(event)))]
    if kind == "response.function_call_arguments.done":
        call_id = _read(event, "call_id") or _read(event, "item_id") or ""
        name = _read(event, "name") or ""
        arguments = _read(event, "arguments") or ""
        if isinstance(call_id, str) and isinstance(name, str):
            state.record_call(call_id, name, arguments if isinstance(arguments, str) else "")
        return []
    if kind in ("response.output_item.done", "response.output_item.added"):
        _remember_item(state, _read(event, "item"))
        return []
    if kind == "response.failed":
        error = _read(_read(event, "response"), "error")
        message = _read(error, "message") if error is not None else None
        state.failed = message if isinstance(message, str) and message else "response.failed"
        usage = _usage_of(_read(event, "response"))
        if usage is not None:
            state.usage = usage
        return []
    if kind in ("response.completed", "response.incomplete"):
        response = _read(event, "response")
        usage = _usage_of(response)
        if usage is not None:
            state.usage = usage
        output = _read(response, "output")
        if isinstance(output, list):
            for item in output:
                _remember_item(state, item)
        return []
    return []
