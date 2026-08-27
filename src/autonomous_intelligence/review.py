"""Review-workflow status transitions for a `ContentBundle`, keyed by
`(company_id, date, trend_index)` and persisted in SQLite - see
`content_models.ContentStatus` for why this isn't a field on the bundle
itself."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from . import db
from .config import Settings
from .content_models import ContentStatus

ALLOWED_TRANSITIONS: dict[ContentStatus, set[ContentStatus]] = {
    ContentStatus.draft: {ContentStatus.pending_review},
    ContentStatus.pending_review: {ContentStatus.approved, ContentStatus.rejected},
    ContentStatus.rejected: {ContentStatus.pending_review},
    ContentStatus.approved: set(),
}


class InvalidTransition(ValueError):
    """Raised when a status change isn't allowed from the bundle's current
    status - mapped to HTTP 409 by the API layer."""


class BundleStatus(BaseModel):
    status: ContentStatus
    reviewer_email: str | None = None
    reviewed_at: datetime | None = None
    notes: str | None = None


def get_status(settings: Settings, *, company_id: int, date: str, trend_index: int) -> BundleStatus:
    row = db.get_bundle_status_row(settings.database_path, company_id, date, trend_index)
    if row is None:
        return BundleStatus(status=ContentStatus.draft)
    return BundleStatus(
        status=ContentStatus(row["status"]),
        reviewer_email=row["reviewer_email"],
        reviewed_at=row["reviewed_at"],
        notes=row["notes"],
    )


def set_status(
    settings: Settings,
    *,
    company_id: int,
    date: str,
    trend_index: int,
    new_status: ContentStatus,
    reviewer_email: str,
    notes: str | None = None,
) -> BundleStatus:
    current = get_status(settings, company_id=company_id, date=date, trend_index=trend_index)
    if new_status not in ALLOWED_TRANSITIONS[current.status]:
        raise InvalidTransition(
            f"Cannot move from '{current.status.value}' to '{new_status.value}'."
        )
    db.upsert_bundle_status(
        settings.database_path,
        company_id=company_id,
        date=date,
        trend_index=trend_index,
        status=new_status.value,
        reviewer_email=reviewer_email,
        notes=notes,
    )
    return get_status(settings, company_id=company_id, date=date, trend_index=trend_index)


__all__ = ["ALLOWED_TRANSITIONS", "BundleStatus", "InvalidTransition", "get_status", "set_status"]
