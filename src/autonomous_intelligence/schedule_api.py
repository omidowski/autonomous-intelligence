from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import auth, db
from .config import Settings, get_settings
from .publishers import configured_platforms

router = APIRouter(prefix="/schedule", tags=["schedule"])


def _payload(row) -> dict:
    return {
        "id": row["id"],
        "run_id": row["date"],
        "trend_index": row["trend_index"],
        "platform": row["platform"],
        "scheduled_for": row["scheduled_for"],
        "status": row["status"],
        "external_id": row["external_id"],
        "detail": row["detail"],
        "published_at": row["published_at"],
    }


@router.get("")
def list_schedule(
    status: str | None = None,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """The publishing calendar for this company, oldest slot first. Filter
    with `?status=pending|published|failed|canceled`."""
    if status is not None and status not in ("pending", "published", "failed", "canceled"):
        raise HTTPException(status_code=400, detail="Unknown status filter.")
    rows = db.list_scheduled_posts(settings.database_path, current.company_id, status=status)
    return [_payload(row) for row in rows]


@router.get("/platforms")
def platform_status(
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Which platforms publish for real versus land in the no-network stub.
    Surfaced so the UI can say so plainly - "scheduled" must not imply "will
    actually appear on LinkedIn" when no token is configured."""
    live = sorted(configured_platforms(settings))
    return {
        "live": live,
        "stubbed": sorted({"linkedin", "x", "facebook"} - set(live)),
        "note": (
            "Platforms not listed as live are published through the stub - no "
            "real network call is made. Set that platform's credentials to go live."
        ),
    }


@router.delete("/{post_id}")
def cancel(
    post_id: int,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not db.cancel_scheduled_post(settings.database_path, current.company_id, post_id):
        raise HTTPException(status_code=404, detail="No pending scheduled post with that id.")
    return {"ok": True}


__all__ = ["router"]
