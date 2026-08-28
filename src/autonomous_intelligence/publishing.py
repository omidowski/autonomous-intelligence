"""The one place a content bundle actually gets published.

Both entry points funnel through `publish_bundle_platforms()`: the manual
`POST /daily-content/{date}/trend/{i}/publish` and the scheduler draining
`scheduled_posts`. Keeping them on one path means the approval gate, the
A/B variant resolution, the `publish_log` write and the outbound webhook
cannot drift apart between "publish now" and "publish at 09:00".
"""

from __future__ import annotations

import json
import logging

from . import db, review, tenancy, webhooks
from .config import Settings
from .content_models import ContentBundle, ContentStatus
from .publishers import PublishResult, get_publisher

logger = logging.getLogger(__name__)


class BundleNotFound(LookupError):
    """No `content.json` on disk for that company/date/trend index."""


class NotApproved(PermissionError):
    """The bundle exists but hasn't been approved - publishing unreviewed
    content is the one thing this pipeline must never do on its own."""


def load_bundle(settings: Settings, company_slug: str, date: str, trend_index: int) -> ContentBundle:
    path = tenancy.tenant_output_dir(settings, company_slug, date) / f"trend-{trend_index}" / "content.json"
    if not path.is_file():
        raise BundleNotFound(f"No content bundle for {date}/trend-{trend_index}.")
    return ContentBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))


def resolve_selected_posts(
    settings: Settings, bundle: ContentBundle, company_id: int, date: str, trend_index: int
) -> dict:
    """Platform -> the single post to publish. `social_posts` normally holds
    two variants per platform (see `SocialPost.variant`); a publisher must
    only ever see the one the company picked, defaulting to "A"."""
    selected = db.get_selected_variants(settings.database_path, company_id, date, trend_index)
    resolved = {}
    for post in bundle.social_posts:
        if post.variant is None or post.variant == selected.get(post.platform, "A"):
            resolved[post.platform] = post
    return resolved


def publish_bundle_platforms(
    settings: Settings,
    *,
    company_id: int,
    company_slug: str,
    date: str,
    trend_index: int,
    platforms: list[str] | None = None,
    publisher_name: str = "auto",
    notify: bool = True,
) -> list[PublishResult]:
    """Publishes the approved bundle to `platforms` (all resolved platforms
    when `None`), logs each attempt, and fires the `content.published`
    webhook. Raises `BundleNotFound`/`NotApproved` - callers map those to
    their own error shape (HTTP status, or a failed scheduled post).

    `"auto"` routes each platform to its real API only where that
    platform's credentials are set, and to the no-network stub otherwise -
    so this default is identical to the old stub-only behaviour until
    tokens are configured (see `publishers/__init__.py`)."""
    status = review.get_status(
        settings, company_id=company_id, date=date, trend_index=trend_index
    )
    if status.status != ContentStatus.approved:
        raise NotApproved("Bundle must be approved before publishing.")

    bundle = load_bundle(settings, company_slug, date, trend_index)
    resolved = resolve_selected_posts(settings, bundle, company_id, date, trend_index)
    bundle_for_publish = bundle.model_copy(update={"social_posts": list(resolved.values())})

    publisher = get_publisher(publisher_name)
    targets = platforms if platforms is not None else list(resolved.keys())
    results = [publisher.publish(bundle_for_publish, platform) for platform in targets]

    for result in results:
        db.insert_publish_log(
            settings.database_path,
            company_id=company_id,
            date=date,
            trend_index=trend_index,
            platform=result.platform,
            success=result.success,
            external_id=result.external_id,
            detail=result.detail,
        )

    if notify:
        webhooks.deliver(
            settings,
            company_id,
            "content.published",
            {
                "date": date,
                "trend_index": trend_index,
                "platforms": [r.platform for r in results if r.success],
                "results": [r.model_dump() for r in results],
            },
        )
    return results


__all__ = [
    "BundleNotFound",
    "NotApproved",
    "load_bundle",
    "publish_bundle_platforms",
    "resolve_selected_posts",
]
