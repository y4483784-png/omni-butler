from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

_WALLS = (
    "请使用App",
    "打开App",
    "仅支持App",
    "Download the app",
    "Open in the app",
)


def decide(
    page: str,
    *,
    armed: str = "",
    pay_clicked: bool = False,
    clicks_after_wall: int = 0,
    has_login: bool = True,
    has_pay: bool = True,
    asks_login: bool = False,
    asks_pay: bool = False,
    price: int | None = None,
    amount_cap: int | None = None,
    order_id: str = "",
) -> dict:
    text = page or ""
    if any(phrase in text for phrase in _WALLS) and not pay_clicked:
        return _result("app_wall", "", 0)
    if asks_login and not has_login:
        return _result("need_secret", "", 0)
    if asks_pay and not has_pay:
        return _result("need_secret", "", 0)
    if price is not None and amount_cap is not None and price > amount_cap:
        return _result("rejected", "", 0)
    key = (armed or "").strip()
    if "已下单" in text and key and key in text:
        return _result("already", order_id, clicks_after_wall)
    if order_id and pay_clicked:
        return _result("placed", order_id, clicks_after_wall)
    return _result("unknown", "", clicks_after_wall)


def allowed_start(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if parsed.port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or ":" in host:
        return False
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if len(labels) < 2:
            return False
        return all(_label(label) for label in labels)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _label(label: str) -> bool:
    if not label or label.startswith("-") or label.endswith("-"):
        return False
    return all(ch.isalnum() or ch == "-" for ch in label)


def _result(outcome: str, order_id: str, clicks: int) -> dict:
    told = "已下单" if outcome == "already" else ""
    return {
        "outcome": outcome,
        "order_id": order_id,
        "told": told,
        "clicks_after_wall": clicks,
    }
