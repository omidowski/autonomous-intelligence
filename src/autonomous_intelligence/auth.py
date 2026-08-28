"""Session-cookie auth backed by SQLite (`db.py`). Passwords are hashed with
stdlib PBKDF2 (`hashlib.pbkdf2_hmac`) - no passlib/python-jose/authlib
dependency. Sessions are opaque, DB-backed tokens (not JWTs) so they can be
revoked instantly by deleting the row - simpler to reason about than
JWT expiry/refresh for this scaffolding pass."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request

from . import api_keys, db
from .config import Settings, get_settings


def _pbkdf2(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_password(password: str, *, iterations: int, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = _pbkdf2(password, salt, iterations)
    return digest.hex(), salt.hex()


def verify_password(password: str, *, hash_hex: str, salt_hex: str, iterations: int) -> bool:
    salt = bytes.fromhex(salt_hex)
    candidate = _pbkdf2(password, salt, iterations)
    return secrets.compare_digest(candidate.hex(), hash_hex)


def create_session(settings: Settings, *, user_id: int, company_id: int) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.session_ttl_days)
    db.insert_session(
        settings.database_path,
        token=token,
        user_id=user_id,
        company_id=company_id,
        expires_at=expires_at.isoformat(),
    )
    return token, expires_at


@dataclass
class CurrentUser:
    user_id: int
    company_id: int
    company_slug: str
    company_name: str
    email: str
    role: str


def _load_current_user(settings: Settings, token: str | None) -> CurrentUser | None:
    if not token:
        return None
    session = db.get_session(settings.database_path, token)
    if session is None:
        return None
    if datetime.fromisoformat(session["expires_at"]) < datetime.now(UTC):
        db.delete_session(settings.database_path, token)
        return None
    company = db.get_company_by_id(settings.database_path, session["company_id"])
    if company is None:
        return None
    user_row = db.get_user_by_id(settings.database_path, session["user_id"])
    if user_row is None:
        return None
    return CurrentUser(
        user_id=user_row["id"],
        company_id=company["id"],
        company_slug=company["slug"],
        company_name=company["name"],
        email=user_row["email"],
        role=user_row["role"],
    )


def _load_api_key_user(settings: Settings, presented: str) -> CurrentUser | None:
    """Resolves an `ai_live_...` key to a company-scoped principal. The
    principal is always `member` (see `api_keys.py`): a key can run and
    review content, but never reshape the account."""
    row = db.get_api_key_by_hash(settings.database_path, api_keys.hash_key(presented))
    if row is None:
        return None
    company = db.get_company_by_id(settings.database_path, row["company_id"])
    if company is None:
        return None
    db.touch_api_key(settings.database_path, row["id"])
    return CurrentUser(
        user_id=0,
        company_id=company["id"],
        company_slug=company["slug"],
        company_name=company["name"],
        email=f"api-key:{row['name']}",
        role="member",
    )


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def get_current_user_optional(
    request: Request, settings: Settings = Depends(get_settings)
) -> CurrentUser | None:
    """Session cookie first (the browser app), then an `Authorization:
    Bearer ai_live_...` API key. Only strings carrying the key prefix are
    tried as keys, so an unrelated bearer token still falls through to
    "not authenticated" rather than costing a DB lookup."""
    token = request.cookies.get(settings.session_cookie_name)
    user = _load_current_user(settings, token)
    if user is not None:
        return user
    bearer = _bearer_token(request)
    if bearer and api_keys.looks_like_api_key(bearer):
        return _load_api_key_user(settings, bearer)
    return None


def get_current_user(
    current: CurrentUser | None = Depends(get_current_user_optional),
) -> CurrentUser:
    if current is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return current


def require_owner(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if current.role != "owner":
        raise HTTPException(status_code=403, detail="Only the company owner can do this.")
    return current


__all__ = [
    "CurrentUser",
    "create_session",
    "get_current_user",
    "get_current_user_optional",
    "hash_password",
    "require_owner",
    "verify_password",
]
