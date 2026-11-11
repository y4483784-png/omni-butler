from __future__ import annotations

from dataclasses import dataclass

_FORBIDDEN = frozenset({"password", "pan", "cvv", "cookie", "secret"})


class ErrandOrderError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class Secret:
    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return "Secret()"

    def __format__(self, spec: str) -> str:
        return ""

    def reveal(self) -> str:
        return self._value


@dataclass(frozen=True)
class Line:
    sku: str
    qty: int
    query: str


@dataclass(frozen=True)
class ShipTo:
    name: str
    phone: str
    region: str
    address: str


@dataclass(frozen=True)
class ErrandOrder:
    merchant_key: str
    lines: tuple[Line, ...]
    ship_to: ShipTo
    pay_method: str
    amount_cap: int

    def claim_parts(self) -> dict:
        return {
            "merchant_key": self.merchant_key,
            "lines": [
                {"sku": line.sku, "qty": line.qty, "query": line.query} for line in self.lines
            ],
            "ship_to": {
                "name": self.ship_to.name,
                "phone": self.ship_to.phone,
                "region": self.ship_to.region,
                "address": self.ship_to.address,
            },
            "pay_method": self.pay_method,
            "amount_cap": self.amount_cap,
        }

    @classmethod
    def parse(cls, body: object) -> ErrandOrder:
        if not isinstance(body, dict):
            raise ErrandOrderError("订单无效")
        _reject_secrets(body)
        merchant_key = _text(body.get("merchant_key"))
        if not merchant_key:
            raise ErrandOrderError("订单无效")
        amount_cap = _whole(body.get("amount_cap"))
        if amount_cap < 0:
            raise ErrandOrderError("订单无效")
        ship = body.get("ship_to")
        if not isinstance(ship, dict):
            raise ErrandOrderError("订单无效")
        return cls(
            merchant_key=merchant_key,
            lines=_lines(body.get("lines")),
            ship_to=ShipTo(
                name=_text(ship.get("name")),
                phone=_text(ship.get("phone")),
                region=_text(ship.get("region")),
                address=_text(ship.get("address")),
            ),
            pay_method=_text(body.get("pay_method")),
            amount_cap=amount_cap,
        )


def _reject_secrets(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).strip().casefold() in _FORBIDDEN:
                raise ErrandOrderError("订单不能携带凭证")
            _reject_secrets(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_secrets(item)


def _lines(value: object) -> tuple[Line, ...]:
    if not isinstance(value, list) or not value:
        raise ErrandOrderError("订单无效")
    rows: list[Line] = []
    for item in value:
        if not isinstance(item, dict):
            raise ErrandOrderError("订单无效")
        qty = _whole(item.get("qty"))
        if qty < 1:
            raise ErrandOrderError("订单无效")
        rows.append(Line(sku=_text(item.get("sku")), qty=qty, query=_text(item.get("query"))))
    return tuple(rows)


def _whole(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ErrandOrderError("订单无效")
    return value


def _text(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ErrandOrderError("订单无效")
    return value.strip()
