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

from . import db
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


def get_current_user_optional(
    request: Request, settings: Settings = Depends(get_settings)
) -> CurrentUser | None:
    token = request.cookies.get(settings.session_cookie_name)
    return _load_current_user(settings, token)


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
