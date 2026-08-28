from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import api_keys, auth, db
from .config import Settings, get_settings

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


def _payload(row, *, secret: str | None = None) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "prefix": row["prefix"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "revoked": bool(row["revoked"]),
        # Only ever non-null in the response that created the key.
        "key": secret,
    }


@router.get("")
def list_keys(current: auth.CurrentUser = Depends(auth.require_owner)) -> dict:
    settings = get_settings()
    rows = db.list_api_keys(settings.database_path, current.company_id)
    return {
        "keys": [_payload(row) for row in rows],
        "usage": (
            "Send the key as an Authorization header: "
            "`Authorization: Bearer <key>`. Keys act with member permissions - "
            "they can run and review content, but cannot change billing, team, "
            "webhooks, or keys."
        ),
    }


@router.post("")
def create_key(
    body: ApiKeyCreateRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    full_key, prefix, key_hash = api_keys.generate_key()
    key_id = db.create_api_key(
        settings.database_path,
        company_id=current.company_id,
        name=body.name.strip(),
        prefix=prefix,
        key_hash=key_hash,
    )
    rows = db.list_api_keys(settings.database_path, current.company_id)
    row = next(r for r in rows if r["id"] == key_id)
    return _payload(row, secret=full_key)


@router.delete("/{key_id}")
def revoke_key(
    key_id: int,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not db.revoke_api_key(settings.database_path, current.company_id, key_id):
        raise HTTPException(status_code=404, detail="No such active API key.")
    return {"ok": True}


__all__ = ["router"]
