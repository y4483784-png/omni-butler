from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import set_rls_context
from app.models.models import ErrandVault
from app.services.errand_order import Secret

_KINDS = frozenset({"login", "pay"})


class VaultError(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def store_secret(db: Session, *, user_id: int, merchant_key: str, kind: str, secret: str) -> None:
    merchant = merchant_key.strip()
    stored_kind = kind.strip()
    if stored_kind not in _KINDS or not merchant or not secret:
        raise VaultError("凭证无效")
    set_rls_context(db, user_id)
    ciphertext = _encrypt(secret)
    row = (
        db.query(ErrandVault)
        .filter(
            ErrandVault.user_id == user_id,
            ErrandVault.merchant_key == merchant,
            ErrandVault.kind == stored_kind,
        )
        .one_or_none()
    )
    if row is None:
        db.add(
            ErrandVault(
                user_id=user_id,
                merchant_key=merchant,
                kind=stored_kind,
                ciphertext=ciphertext,
            )
        )
    else:
        row.ciphertext = ciphertext
    db.commit()


def read_secret(db: Session, *, user_id: int, merchant_key: str, kind: str) -> Secret | None:
    merchant = merchant_key.strip()
    stored_kind = kind.strip()
    if stored_kind not in _KINDS or not merchant:
        return None
    set_rls_context(db, user_id)
    row = (
        db.query(ErrandVault)
        .filter(
            ErrandVault.user_id == user_id,
            ErrandVault.merchant_key == merchant,
            ErrandVault.kind == stored_kind,
        )
        .one_or_none()
    )
    if row is None:
        return None
    return Secret(_decrypt(row.ciphertext))


def _fernet() -> Fernet:
    raw = (settings.errand_vault_key or "").strip()
    if not raw:
        raise VaultError("未配置 ERRAND_VAULT_KEY，无法保存支付凭证。")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise VaultError("ERRAND_VAULT_KEY 不是有效的 Fernet 密钥。") from exc


def _encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def _decrypt(stored: str) -> str:
    try:
        return _fernet().decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise VaultError("支付凭证无法读取，未支付。") from exc
