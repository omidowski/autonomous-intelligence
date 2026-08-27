from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import auth, db, review, tenancy
from .config import Settings, get_settings
from .content_models import ContentBundle, ContentStatus
from .publishers import get_publisher

router = APIRouter(prefix="/daily-content", tags=["review"])


class ReviewActionRequest(BaseModel):
    notes: str | None = None


class PublishRequest(BaseModel):
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
    status = review.get_status(
        settings, company_id=current.company_id, date=date, trend_index=trend_index
    )
    if status.status != ContentStatus.approved:
        raise HTTPException(status_code=403, detail="Bundle must be approved before publishing.")

    bundle = _load_bundle(settings, current.company_slug, date, trend_index)
    publisher = get_publisher("stub")
    selected_variants = db.get_selected_variants(
        settings.database_path, current.company_id, date, trend_index
    )
    # Resolve each platform to its one selected post (default variant "A"
    # when nothing was explicitly picked) - `bundle.social_posts` normally
    # has two entries per platform (see `SocialPost.variant`), and a
    # publisher must only ever see the one the company chose to publish.
    resolved_posts = {}
    for post in bundle.social_posts:
        if post.variant is None or post.variant == selected_variants.get(post.platform, "A"):
            resolved_posts[post.platform] = post
    bundle_for_publish = bundle.model_copy(update={"social_posts": list(resolved_posts.values())})

    platforms = body.platforms or list(resolved_posts.keys())

    results = [publisher.publish(bundle_for_publish, platform) for platform in platforms]
    for result in results:
        db.insert_publish_log(
            settings.database_path,
            company_id=current.company_id,
            date=date,
            trend_index=trend_index,
            platform=result.platform,
            success=result.success,
            external_id=result.external_id,
            detail=result.detail,
        )
    return {"results": [r.model_dump() for r in results]}


__all__ = ["router"]
