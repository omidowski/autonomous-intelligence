from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, publishing, review, tenancy, webhooks
from .config import Settings, get_settings
from .content_models import ContentBundle, ContentStatus

router = APIRouter(prefix="/daily-content", tags=["review"])

_EVENT_BY_STATUS = {
    ContentStatus.pending_review: "content.submitted_for_review",
    ContentStatus.approved: "content.approved",
    ContentStatus.rejected: "content.rejected",
}


class ReviewActionRequest(BaseModel):
    notes: str | None = None


class PublishRequest(BaseModel):
    platforms: list[str] | None = None


class ScheduleRequest(BaseModel):
    scheduled_for: datetime
    platforms: list[str] | None = None


class VariantSelectionRequest(BaseModel):
    platform: str
    variant: Literal["A", "B"]


def _load_bundle(settings: Settings, company_slug: str, date: str, trend_index: int) -> ContentBundle:
    trend_dir = tenancy.tenant_output_dir(settings, company_slug, date) / f"trend-{trend_index}"
    content_path = trend_dir / "content.json"
    if not content_path.is_file():
        raise HTTPException(status_code=404, detail="No content bundle for that date/trend index.")
    data = json.loads(content_path.read_text(encoding="utf-8"))
    return ContentBundle.model_validate(data)


def _transition(
    settings: Settings,
    current: auth.CurrentUser,
    date: str,
    trend_index: int,
    new_status: ContentStatus,
    notes: str | None,
) -> dict:
    _load_bundle(settings, current.company_slug, date, trend_index)  # 404s if it doesn't exist
    try:
        status = review.set_status(
            settings,
            company_id=current.company_id,
            date=date,
            trend_index=trend_index,
            new_status=new_status,
            reviewer_email=current.email,
            notes=notes,
        )
    except review.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    event = _EVENT_BY_STATUS.get(new_status)
    if event:
        webhooks.deliver(
            settings,
            current.company_id,
            event,
            {
                "date": date,
                "trend_index": trend_index,
                "status": new_status.value,
                "reviewer_email": current.email,
            },
        )
    return status.model_dump(mode="json")


@router.post("/{date}/trend/{trend_index}/submit-for-review")
def submit_for_review(
    date: str,
    trend_index: int,
    body: ReviewActionRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _transition(settings, current, date, trend_index, ContentStatus.pending_review, body.notes)


@router.post("/{date}/trend/{trend_index}/approve")
def approve(
    date: str,
    trend_index: int,
    body: ReviewActionRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _transition(settings, current, date, trend_index, ContentStatus.approved, body.notes)


@router.post("/{date}/trend/{trend_index}/reject")
def reject(
    date: str,
    trend_index: int,
    body: ReviewActionRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    return _transition(settings, current, date, trend_index, ContentStatus.rejected, body.notes)


@router.put("/{date}/trend/{trend_index}/variant")
def select_variant(
    date: str,
    trend_index: int,
    body: VariantSelectionRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    bundle = _load_bundle(settings, current.company_slug, date, trend_index)  # 404s if missing
    valid_platforms = {post.platform for post in bundle.social_posts}
    if body.platform not in valid_platforms:
        raise HTTPException(
            status_code=400, detail=f"Unknown platform {body.platform!r} for this bundle."
        )
    db.set_selected_variant(
        settings.database_path,
        company_id=current.company_id,
        date=date,
        trend_index=trend_index,
        platform=body.platform,
        variant=body.variant,
    )
    return db.get_selected_variants(settings.database_path, current.company_id, date, trend_index)


@router.post("/{date}/trend/{trend_index}/publish")
def publish(
    date: str,
    trend_index: int,
    body: PublishRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        results = publishing.publish_bundle_platforms(
            settings,
            company_id=current.company_id,
            company_slug=current.company_slug,
            date=date,
            trend_index=trend_index,
            platforms=body.platforms,
        )
    except publishing.NotApproved as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except publishing.BundleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"results": [r.model_dump() for r in results]}


@router.post("/{date}/trend/{trend_index}/schedule")
def schedule(
    date: str,
    trend_index: int,
    body: ScheduleRequest,
    current: auth.CurrentUser = Depends(auth.get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Queues an approved bundle to publish later. The approval gate is
    checked twice on purpose: here, so a mistake surfaces while the user is
    still looking at it, and again at send time in `publishing.py`, because
    a bundle can be rejected in between."""
    when = body.scheduled_for
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if when <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail="scheduled_for must be in the future.")

    bundle = _load_bundle(settings, current.company_slug, date, trend_index)
    status = review.get_status(
        settings, company_id=current.company_id, date=date, trend_index=trend_index
    )
    if status.status != ContentStatus.approved:
        raise HTTPException(status_code=403, detail="Bundle must be approved before scheduling.")

    known = {post.platform for post in bundle.social_posts}
    platforms = body.platforms or sorted(known)
    unknown = sorted(set(platforms) - known)
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown platform(s) for this bundle: {', '.join(unknown)}."
        )

    queued, duplicates = [], []
    for platform in platforms:
        post_id = db.schedule_post(
            settings.database_path,
            company_id=current.company_id,
            date=date,
            trend_index=trend_index,
            platform=platform,
            scheduled_for=when.isoformat(),
        )
        (queued if post_id is not None else duplicates).append(platform)

    return {
        "scheduled_for": when.isoformat(),
        "queued": queued,
        "already_queued": duplicates,
    }


__all__ = ["router"]
