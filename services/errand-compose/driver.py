"""This service drives Chromium with the Playwright Python sync API. The plan did not choose this library."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from decide import allowed_start, decide

_BUY_LABELS = ("下单", "购买", "支付", "Place order", "Pay")


def run_task(task: dict) -> dict:
    kind = str(task.get("kind") or "")
    armed = str(task.get("idempotency_key") or "")
    url = str(task.get("url") or "").strip()
    if not allowed_start(url):
        return {"outcome": "unknown", "order_id": "", "told": "", "clicks_after_wall": 0}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            if kind == "lookup":
                return _observe(page, task, armed, pay_clicked=False, clicks_after_wall=0)
            early = _observe(page, task, armed, pay_clicked=False, clicks_after_wall=0)
            if early["outcome"] in {"app_wall", "need_secret", "already", "rejected"}:
                return early
            _fill(page, task)
            clicked = _click_buy(page)
            text = page.content()
            clicks = 1 if clicked and _wall(text) else 0
            return _observe(
                page,
                task,
                armed,
                pay_clicked=clicked,
                clicks_after_wall=clicks,
            )
        finally:
            context.close()
            browser.close()


def _observe(page, task: dict, armed: str, *, pay_clicked: bool, clicks_after_wall: int) -> dict:
    text = page.content()
    cap = task.get("amount_cap")
    return decide(
        text,
        armed=armed,
        pay_clicked=pay_clicked,
        clicks_after_wall=clicks_after_wall,
        has_login=bool(task.get("login")),
        has_pay=bool(task.get("pay")),
        asks_login=page.locator("input[type='password']").count() > 0,
        asks_pay=page.locator("input[autocomplete='cc-number'], input[name='pan']").count() > 0,
        price=_marked_int(text, "data-price"),
        amount_cap=cap if isinstance(cap, int) and not isinstance(cap, bool) else None,
        order_id=_marked(text, "data-order-id"),
    )


def _fill(page, task: dict) -> None:
    ship = task.get("ship_to") if isinstance(task.get("ship_to"), dict) else {}
    _type_named(page, "address", str(ship.get("address") or ""))
    _type_named(page, "name", str(ship.get("name") or ""))
    login = task.get("login")
    if login and page.locator("input[type='password']").count():
        page.locator("input[type='password']").first.fill(str(login))
    pay = task.get("pay")
    if pay and page.locator("input[autocomplete='cc-number']").count():
        page.locator("input[autocomplete='cc-number']").first.fill(str(pay))


def _type_named(page, name: str, value: str) -> None:
    if not value:
        return
    locator = page.locator(f"input[name='{name}']")
    if locator.count():
        locator.first.fill(value)


def _click_buy(page) -> bool:
    for label in _BUY_LABELS:
        locator = page.get_by_role("button", name=label)
        if locator.count():
            locator.first.click()
            return True
    return False


def _wall(text: str) -> bool:
    return decide(text, pay_clicked=False)["outcome"] == "app_wall"


def _marked(text: str, attr: str) -> str:
    marker = f'{attr}="'
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker) :]
    end = rest.find('"')
    if end < 0:
        return ""
    return rest[:end]


def _marked_int(text: str, attr: str) -> int | None:
    raw = _marked(text, attr)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None
