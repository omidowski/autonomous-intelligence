from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from ..content_models import ContentBundle


class PublishResult(BaseModel):
    platform: str
    success: bool
    external_id: str | None
    detail: str


class Publisher(Protocol):
    """Interface real platform integrations (X/Instagram/LinkedIn) will
    implement once API credentials exist. `get_publisher()` in
    `__init__.py` is the single place that decides which implementation is
    used - no other code needs to change when a real publisher is added."""

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult: ...


__all__ = ["PublishResult", "Publisher"]
