"""Encrypt, store, and refresh Google Calendar OAuth tokens.

Logs record HTTP status only. Authorization codes, access tokens, refresh
tokens, and the client secret must not appear in log lines or audit args.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models import CalendarConnection

logger = logging.getLogger(__name__)

GOOGLE_CALENDAR_SCOPES = (
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.settings.readonly",
    "https://www.googleapis.com/auth/calendar.events.freebusy",
)
# Requested with the calendar scopes, but not required when a grant is saved.
# openid makes the code exchange return an id_token. Without it, access-token
# tokeninfo often has no sub, and the connect is dropped after consent.
GOOGLE_IDENTITY_SCOPES = ("openid", "email")
# Requested on the same consent screen. Not required to save a calendar grant.
GOOGLE_MAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
# Requested on the same consent screen. Not required to save a calendar grant.
GOOGLE_DRIVE_SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)
GOOGLE_REQUESTED_SCOPES = (
    GOOGLE_CALENDAR_SCOPES + GOOGLE_MAIL_SCOPES + GOOGLE_DRIVE_SCOPES + GOOGLE_IDENTITY_SCOPES
)
OAUTH_STATE_MAX_AGE = 10 * 60
_STATE_SALT = "omni-calendar-google-oauth"
_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
_PRIMARY_CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary"
_REFRESH_LEEWAY = timedelta(seconds=60)
_ID_TOKEN_SKEW_SECONDS = 60
_GOOGLE_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}

_SECRET_QUERY = re.compile(
    r"(?i)(access_token|refresh_token|client_secret|id_token|code)=([^&\s\"']+)"
)
_BEARER = re.compile(r"(?i)(Bearer\s+)\S+")
_redact_installed = False


class CalendarOAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class CalendarSubMismatch(CalendarOAuthError):
    def __init__(self) -> None:
        super().__init__("该账号已连接其他 Google 账户，请先断开后再连接。", status_code=409)


class CalendarTokenError(CalendarOAuthError):
    def __init__(self, message: str = "Google 日历授权已失效，请重新连接。") -> None:
        super().__init__(message, status_code=401)


class CalendarTransientError(Exception):
    """Google could not be reached. The stored grant is still valid.

    Not a ``CalendarTokenError``. Callers must not ask the user to reconnect.
    """

    def __init__(self, message: str = "暂时连不上 Google 日历，请稍后再试。") -> None:
        super().__init__(message)
        self.message = message


class _RedactSecrets(logging.Filter):
    """Strip credential query values before httpx writes the request URL."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: _redact_value(value) for key, value in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_value(value) for value in record.args)
        return True


def install_log_redaction() -> None:
    global _redact_installed
    if _redact_installed:
        return
    redact = _RedactSecrets()
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(redact)
    _redact_installed = True


def _redact_text(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    text = _SECRET_QUERY.sub(r"\1=[redacted]", text)
    return _BEARER.sub(r"\1[redacted]", text)


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return _redact_text(value)
    rendered = str(value)
    redacted = _redact_text(rendered)
    if redacted != rendered:
        return redacted
    return value


install_log_redaction()


def oauth_configuration_error() -> str | None:
    if not (settings.google_oauth_client_id or "").strip():
        return "未配置 GOOGLE_OAUTH_CLIENT_ID，无法连接 Google 日历。"
    if not (settings.google_oauth_client_secret or "").strip():
        return "未配置 GOOGLE_OAUTH_CLIENT_SECRET，无法连接 Google 日历。"
    if not (settings.google_oauth_redirect_uri or "").strip():
        return "未配置 GOOGLE_OAUTH_REDIRECT_URI，无法连接 Google 日历。"
    if not (settings.calendar_token_key or "").strip():
        return "未配置 CALENDAR_TOKEN_KEY，无法保存日历令牌。"
    try:
        _fernet()
    except CalendarTokenError as exc:
        return str(exc)
    return None


def app_home_url() -> str:
    raw = (settings.cors_origins or "").split(",")[0].strip()
    if not raw or raw == "*":
        return "/"
    return raw.rstrip("/") + "/"


def sign_oauth_state(user_id: int) -> str:
    exp = int(time.time()) + OAUTH_STATE_MAX_AGE
    return _state_serializer().dumps({"user_id": int(user_id), "exp": exp})


def load_oauth_state(token: str) -> int:
    data = _state_serializer().loads(token, max_age=OAUTH_STATE_MAX_AGE)
    if not isinstance(data, dict) or "user_id" not in data:
        raise BadSignature("state")
    if int(data.get("exp") or 0) < int(time.time()):
        raise SignatureExpired("state")
    return int(data["user_id"])


def build_authorize_url(state: str) -> str:
    query = urlencode(
        {
            "client_id": (settings.google_oauth_client_id or "").strip(),
            "redirect_uri": (settings.google_oauth_redirect_uri or "").strip(),
            "response_type": "code",
            "scope": " ".join(GOOGLE_REQUESTED_SCOPES),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }
    )
    return f"{_AUTHORIZE_URL}?{query}"


def _scope_granted(scopes: str, required: tuple[str, ...]) -> bool:
    granted = {part for part in (scopes or "").split() if part}
    return all(scope in granted for scope in required)


def _mail_access(scopes: str) -> bool:
    return _scope_granted(scopes, GOOGLE_MAIL_SCOPES)


def _drive_access(scopes: str) -> bool:
    return _scope_granted(scopes, GOOGLE_DRIVE_SCOPES)


def _status_body(*, connected: bool, account_email: str, timezone: str, scopes: str) -> dict:
    return {
        "connected": connected,
        "account_email": account_email,
        "timezone": timezone,
        "mail_access": _mail_access(scopes) if connected else False,
        "drive_access": _drive_access(scopes) if connected else False,
    }


def connection_status(db: Session, user_id: int) -> dict:
    row = _connection_for(db, user_id)
    if row is None:
        return _status_body(connected=False, account_email="", timezone="", scopes="")
    return _status_body(
        connected=True,
        account_email=row.account_email or "",
        timezone="",
        scopes=row.scopes or "",
    )


@dataclass(frozen=True)
class GoogleAccess:
    """A reader for one granted scope. Callers never see the token."""

    account_email: str
    _token: str

    def get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        return _http_request(
            "GET",
            url,
            params=params,
            headers={"Authorization": f"Bearer {self._token}"},
        )


def scoped_access(
    db: Session, user_id: int, scope: str
) -> GoogleAccess | Literal["not_connected", "scope_missing"]:
    """A reader only when the stored grant has scope. No HTTP when it does not."""
    row = _connection_for(db, user_id)
    if row is None:
        return "not_connected"
    granted = {part for part in (row.scopes or "").split() if part}
    if scope not in granted:
        return "scope_missing"
    token = refresh_access_token(db, row)
    return GoogleAccess(account_email=row.account_email or "", _token=token)


def disconnect_google(db: Session, user_id: int) -> dict:
    """Delete the token row only. Local events and Google events stay."""
    db.query(CalendarConnection).filter(CalendarConnection.user_id == user_id).delete()
    db.commit()
    return _status_body(connected=False, account_email="", timezone="", scopes="")


def connect_google_account(db: Session, *, user_id: int, code: str) -> CalendarConnection:
    payload = _exchange_code(code)
    access = str(payload.get("access_token") or "").strip()
    refresh = str(payload.get("refresh_token") or "").strip()
    if not access:
        logger.warning("google calendar token exchange failed status=missing-access")
        raise CalendarOAuthError("Google 授权失败，未保存连接。", 502)
    scopes = str(payload.get("scope") or " ".join(GOOGLE_CALENDAR_SCOPES)).strip()
    _require_scopes(scopes)
    sub, email = _identity(access, id_token=str(payload.get("id_token") or ""))
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    return _upsert_connection(
        db,
        user_id=user_id,
        google_sub=sub,
        account_email=email,
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        scopes=scopes,
    )


def _keep_granted_scopes(stored: str, incoming: str) -> str:
    """A refresh body may list fewer scopes than the user already granted.

    Dropping drive.readonly here would hide docs after the access token expires
    even though the refresh token still carries that grant. A later 403 is the
    signal that the user actually revoked it.
    """
    parts = [part for part in stored.split() if part]
    seen = set(parts)
    for part in incoming.split():
        if part and part not in seen:
            parts.append(part)
            seen.add(part)
    return " ".join(parts)


def refresh_access_token(db: Session, connection: CalendarConnection) -> str:
    """Return a usable access token, writing a replacement when the current one is due."""
    expiry = _as_utc(connection.token_expiry)
    now = datetime.now(timezone.utc)
    if expiry is not None and expiry - now > _REFRESH_LEEWAY:
        return decrypt_token(connection.access_token)
    refresh = decrypt_token(connection.refresh_token)
    try:
        response = _http_request(
            "POST",
            _TOKEN_URL,
            data={
                "client_id": (settings.google_oauth_client_id or "").strip(),
                "client_secret": (settings.google_oauth_client_secret or "").strip(),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
    except httpx.HTTPError:
        logger.warning("google calendar token refresh failed status=network")
        raise CalendarTransientError() from None
    if response.status_code != 200:
        logger.warning(
            "google calendar token refresh failed status=%s", response.status_code
        )
        if 500 <= response.status_code <= 599:
            raise CalendarTransientError()
        raise CalendarTokenError()
    payload = _json_object(response)
    access = str(payload.get("access_token") or "").strip()
    if not access:
        logger.warning(
            "google calendar token refresh failed status=%s", response.status_code
        )
        raise CalendarTokenError()
    connection.access_token = encrypt_token(access)
    new_refresh = str(payload.get("refresh_token") or "").strip()
    if new_refresh:
        connection.refresh_token = encrypt_token(new_refresh)
    if payload.get("scope"):
        connection.scopes = _keep_granted_scopes(connection.scopes or "", str(payload["scope"]))
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except (TypeError, ValueError):
        expires_in = 3600
    connection.token_expiry = now + timedelta(seconds=expires_in)
    connection.updated_at = now
    db.commit()
    logger.info("google calendar token refreshed user_id=%s", connection.user_id)
    return access


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_token(stored: str) -> str:
    try:
        return _fernet().decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise CalendarTokenError("日历令牌无法解密，请重新连接。") from exc


def _exchange_code(code: str) -> dict:
    try:
        response = _http_request(
            "POST",
            _TOKEN_URL,
            data={
                "code": code,
                "client_id": (settings.google_oauth_client_id or "").strip(),
                "client_secret": (settings.google_oauth_client_secret or "").strip(),
                "redirect_uri": (settings.google_oauth_redirect_uri or "").strip(),
                "grant_type": "authorization_code",
            },
        )
    except httpx.HTTPError:
        logger.warning("google calendar token exchange failed status=network")
        raise CalendarOAuthError("Google 授权失败，未保存连接。", 502) from None
    if response.status_code != 200:
        logger.warning(
            "google calendar token exchange failed status=%s", response.status_code
        )
        raise CalendarOAuthError("Google 授权失败，未保存连接。", 502)
    return _json_object(response)


def _identity(access_token: str, id_token: str = "") -> tuple[str, str]:
    """Resolve the Google account from the code-exchange id_token, then tokeninfo.

    Access-token tokeninfo omits sub and user_id when the grant never included
    openid. The id_token from the same exchange carries sub (and email, when
    that scope was granted). A tokeninfo body without sub is not fatal once
    the id_token identifies the account.
    """
    claims = _id_token_claims(id_token)
    sub_from_id = _claim_text(claims, "sub")
    info = _access_tokeninfo(access_token, required=not sub_from_id)
    sub = sub_from_id or str(info.get("sub") or info.get("user_id") or "").strip()
    if not sub:
        logger.warning("google calendar tokeninfo failed status=missing-sub")
        raise CalendarOAuthError("无法确认 Google 账户，未保存连接。", 502)
    email_hint = _claim_text(claims, "email") or str(info.get("email") or "").strip()
    return sub, _account_email(access_token, {"email": email_hint})


def _access_tokeninfo(access_token: str, *, required: bool) -> dict:
    try:
        info_response = _http_request(
            "POST",
            _TOKENINFO_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
    except httpx.HTTPError:
        if not required:
            return {}
        logger.warning("google calendar tokeninfo failed status=network")
        raise CalendarOAuthError("无法确认 Google 账户，未保存连接。", 502) from None
    if info_response.status_code != 200:
        if not required:
            return {}
        logger.warning(
            "google calendar tokeninfo failed status=%s", info_response.status_code
        )
        raise CalendarOAuthError("无法确认 Google 账户，未保存连接。", 502)
    return _json_object(info_response)


def _id_token_claims(id_token: str) -> dict:
    """Claims from the token-endpoint id_token, or {} when it cannot be used.

    The exchange uses the client secret over HTTPS, so the body is Google's.
    Issuer, audience, and expiry are still checked. An unusable id_token is
    ignored and tokeninfo remains the fallback.
    """
    token = id_token.strip()
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) != 3 or not all(parts):
        logger.warning("google calendar id_token failed status=malformed")
        return {}
    claims = _b64url_json(parts[1])
    if claims is None:
        logger.warning("google calendar id_token failed status=malformed")
        return {}
    if str(claims.get("iss") or "") not in _GOOGLE_ISSUERS:
        logger.warning("google calendar id_token failed status=bad-iss")
        return {}
    client_id = (settings.google_oauth_client_id or "").strip()
    if client_id not in _audiences(claims.get("aud")):
        logger.warning("google calendar id_token failed status=bad-aud")
        return {}
    try:
        exp = int(claims.get("exp"))
    except (TypeError, ValueError):
        logger.warning("google calendar id_token failed status=expired")
        return {}
    if exp <= int(time.time()) - _ID_TOKEN_SKEW_SECONDS:
        logger.warning("google calendar id_token failed status=expired")
        return {}
    if not _claim_text(claims, "sub"):
        logger.warning("google calendar id_token failed status=missing-sub")
        return {}
    return claims


def _audiences(audience: object) -> set[str]:
    if isinstance(audience, str):
        return {audience}
    if isinstance(audience, list):
        return {item for item in audience if isinstance(item, str) and item}
    return set()


def _claim_text(claims: dict, key: str) -> str:
    value = claims.get(key)
    if not isinstance(value, str):
        return ""
    return value.strip()


def _b64url_json(segment: str) -> dict | None:
    try:
        padded = segment + "=" * (-len(segment) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        body = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    return body if isinstance(body, dict) else None


def _account_email(access_token: str, tokeninfo: dict) -> str:
    primary_id = ""
    try:
        response = _http_request(
            "GET",
            _PRIMARY_CALENDAR_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except httpx.HTTPError:
        logger.warning("google calendar primary lookup failed status=network")
        response = None
    if response is not None and response.status_code == 200:
        primary_id = str(_json_object(response).get("id") or "").strip()
    elif response is not None:
        logger.warning("google calendar primary lookup failed status=%s", response.status_code)
    chosen = primary_id or str(tokeninfo.get("email") or "").strip()
    if not chosen:
        raise CalendarOAuthError("无法读取 Google 账户邮箱，未保存连接。", 502)
    return chosen


def _require_scopes(scopes: str) -> None:
    granted = {part for part in scopes.split() if part}
    missing = [scope for scope in GOOGLE_CALENDAR_SCOPES if scope not in granted]
    if missing:
        raise CalendarOAuthError("Google 授权范围不完整，未保存连接。", 400)


def _upsert_connection(
    db: Session,
    *,
    user_id: int,
    google_sub: str,
    account_email: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scopes: str,
) -> CalendarConnection:
    existing = _connection_for(db, user_id)
    if existing is not None and existing.google_sub != google_sub:
        raise CalendarSubMismatch()
    if existing is None and not refresh_token:
        raise CalendarOAuthError("Google 没有返回刷新令牌，请重新同意授权。", 400)
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(seconds=max(expires_in, 0))
    access_stored = encrypt_token(access_token)
    if existing is None:
        row = CalendarConnection(
            user_id=user_id,
            provider="google",
            google_sub=google_sub,
            account_email=account_email,
            access_token=access_stored,
            refresh_token=encrypt_token(refresh_token),
            token_expiry=expiry,
            scopes=scopes,
        )
        db.add(row)
    else:
        row = existing
        row.provider = "google"
        row.account_email = account_email
        row.access_token = access_stored
        if refresh_token:
            row.refresh_token = encrypt_token(refresh_token)
        row.token_expiry = expiry
        row.scopes = scopes
        row.updated_at = now
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raced = _connection_for(db, user_id)
        if raced is not None and raced.google_sub != google_sub:
            raise CalendarSubMismatch() from None
        raise CalendarOAuthError("该账号已有 Google 日历连接，请先断开后再连接。", 409) from None
    db.refresh(row)
    logger.info("google calendar connected user_id=%s", user_id)
    return row


def _connection_for(db: Session, user_id: int) -> CalendarConnection | None:
    return (
        db.query(CalendarConnection)
        .filter(CalendarConnection.user_id == user_id)
        .one_or_none()
    )


def _fernet() -> Fernet:
    raw = (settings.calendar_token_key or "").strip()
    if not raw:
        raise CalendarTokenError("未配置 CALENDAR_TOKEN_KEY，无法保存日历令牌。")
    try:
        return Fernet(raw.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise CalendarTokenError("CALENDAR_TOKEN_KEY 不是有效的 Fernet 密钥。") from exc


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=_STATE_SALT)


def _json_object(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _http_request(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    json: dict | None = None,
) -> httpx.Response:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("Google HTTP is disabled during tests")
    with httpx.Client(timeout=20.0) as client:
        return client.request(
            method, url, data=data, params=params, headers=headers, json=json
        )
