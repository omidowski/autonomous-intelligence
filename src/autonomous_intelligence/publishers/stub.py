from __future__ import annotations

from uuid import uuid4

from ..content_models import ContentBundle
from .base import PublishResult


class StubPublisher:
    """Makes NO real network calls to any platform - the user has no
    X/Instagram/LinkedIn API credentials yet. Always reports success so the
    review -> approve -> publish flow can be exercised end-to-end; swap in
    a real `Publisher` implementation via `get_publisher()` once
    credentials exist."""

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult:
        return PublishResult(
            platform=platform,
            success=True,
            external_id=f"stub-{uuid4()}",
            detail="Stub publish - no real network call was made.",
        )


__all__ = ["StubPublisher"]
