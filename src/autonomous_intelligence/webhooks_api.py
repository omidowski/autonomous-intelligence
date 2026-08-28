from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import auth, db, webhooks
from .config import Settings, get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookCreateRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    event_types: list[str] | None = Field(
        default=None,
        description="Subset of the supported events to receive; omit or empty for all.",
    )


def _payload(row, *, reveal_secret: bool = False) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "event_types": row["event_types"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        # The signing secret is shown once, at creation, then never again.
        "secret": row["secret"] if reveal_secret else None,
    }


@router.get("")
def list_webhooks(current: auth.CurrentUser = Depends(auth.require_owner)) -> dict:
    settings = get_settings()
    rows = db.list_company_webhooks(settings.database_path, current.company_id)
    return {
        "supported_events": list(webhooks.EVENT_TYPES),
        "webhooks": [_payload(row) for row in rows],
    }


@router.post("")
def create_webhook(
    body: WebhookCreateRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        webhooks.validate_url(body.url)
    except webhooks.InvalidWebhookURL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.event_types:
        unknown = sorted(set(body.event_types) - set(webhooks.EVENT_TYPES))
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown event types: {', '.join(unknown)}.")
        event_types = ",".join(sorted(set(body.event_types)))
    else:
        event_types = "all"

    webhook_id = db.create_company_webhook(
        settings.database_path,
        company_id=current.company_id,
        url=body.url,
        secret="whsec_" + secrets.token_urlsafe(24),
        event_types=event_types,
    )
    rows = db.list_company_webhooks(settings.database_path, current.company_id)
    row = next(r for r in rows if r["id"] == webhook_id)
    return _payload(row, reveal_secret=True)


@router.delete("/{webhook_id}")
def delete_webhook(
    webhook_id: int,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not db.delete_company_webhook(settings.database_path, current.company_id, webhook_id):
        raise HTTPException(status_code=404, detail="No such webhook.")
    return {"ok": True}


__all__ = ["router"]
