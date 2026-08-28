from __future__ import annotations

import logging
import urllib.error

from ..config import Settings
from ..content_models import ContentBundle
from .base import Publisher, PublishResult
from .social import PUBLISHER_CLASSES, PublisherNotConfigured, configured_platforms
from .stub import StubPublisher

logger = logging.getLogger(__name__)

_PUBLISHERS: dict[str, Publisher] = {
    "stub": StubPublisher(),
}


def get_publisher(name: str = "stub") -> Publisher:
    """Registry lookup for the whole-pipeline publisher. `"auto"` returns
    the per-platform router below, which is what production uses; `"stub"`
    stays available so tests and demos never touch the network."""
    if name == "auto":
        return AutoPublisher()
    try:
        return _PUBLISHERS[name]
    except KeyError:
        raise ValueError(f"Unknown publisher: {name!r}") from None


class AutoPublisher:
    """Routes each platform to its real API when that platform's credentials
    are configured, and to `StubPublisher` when they are not.

    The decision is per platform, not per app: a company with only a
    LinkedIn token publishes to LinkedIn for real while X and Instagram
    stay stubbed, instead of an all-or-nothing switch.

    Network and API errors are turned into an unsuccessful `PublishResult`
    rather than an exception, so one dead platform cannot abort a multi-
    platform publish - the caller records the failure per platform in
    `publish_log` and the `/schedule` calendar.
    """

    def __init__(self, settings: Settings | None = None):
        # Imported lazily so `get_publisher()` stays usable in unit tests
        # that never build a Settings.
        from ..config import get_settings

        self.settings = settings or get_settings()
        self._live = configured_platforms(self.settings)
        self._stub = StubPublisher()

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult:
        if platform not in self._live:
            return self._stub.publish(bundle, platform)
        try:
            return PUBLISHER_CLASSES[platform](self.settings).publish(bundle, platform)
        except PublisherNotConfigured as exc:
            return PublishResult(
                platform=platform, success=False, external_id=None, detail=str(exc)
            )
        except urllib.error.HTTPError as exc:
            # The error body carries the platform's own reason (bad scopes,
            # duplicate post, rate limit) - worth surfacing, but a body that
            # cannot be read must not mask the HTTP status itself.
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except OSError:
                body = ""
            logger.warning("Publish to %s failed: HTTP %s %s", platform, exc.code, body)
            return PublishResult(
                platform=platform,
                success=False,
                external_id=None,
                detail=f"{platform} API returned HTTP {exc.code}. {body}".strip(),
            )
        except (urllib.error.URLError, OSError, ValueError, LookupError) as exc:
            logger.warning("Publish to %s failed: %r", platform, exc)
            return PublishResult(
                platform=platform, success=False, external_id=None, detail=f"{platform}: {exc}"
            )


__all__ = [
    "AutoPublisher",
    "PublishResult",
    "Publisher",
    "PublisherNotConfigured",
    "StubPublisher",
    "configured_platforms",
    "get_publisher",
]
