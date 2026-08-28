"""Real platform publishers - LinkedIn, X and Facebook Pages.

Each one posts the selected copy for its platform through the vendor's
own REST API and returns the created post's id in `PublishResult.external_id`,
so `publish_log` and the `/schedule` calendar hold a link back to the live
post rather than a stub id.

Follows the same shape as every other external integration here
(`stock_photos.py`, `leonardo_images.py`, ...): stdlib `urllib.request` for
a handful of simple JSON calls rather than a new HTTP dependency, and a
credential that is simply absent means the platform is *not configured* -
`get_publisher()` then keeps using `StubPublisher` for it. Nothing here
posts anything until real tokens are set, and a platform with no token is
reported as a failed publish with a clear reason, never as a silent success.

Getting tokens (all three are OAuth apps you register once):
  LinkedIn  https://www.linkedin.com/developers/apps  - needs `w_member_social`
            and the author URN of the member/organization posting.
  X         https://developer.x.com/en/portal/dashboard - OAuth 2.0 user
            token with `tweet.write`.
  Facebook  https://developers.facebook.com/apps - a Page access token with
            `pages_manage_posts`, plus the Page id.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from ..config import Settings
from ..content_models import ContentBundle
from .base import PublishResult

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15

LINKEDIN_POSTS_URL = "https://api.linkedin.com/rest/posts"
LINKEDIN_VERSION = "202506"
X_TWEETS_URL = "https://api.x.com/2/tweets"
FACEBOOK_GRAPH_URL = "https://graph.facebook.com/v21.0"


class PublisherNotConfigured(RuntimeError):
    """No credential for this platform - the caller reports an unsuccessful
    publish rather than pretending the post went out."""


def post_text(bundle: ContentBundle, platform: str) -> str:
    """The copy to publish for `platform`: the already variant-resolved post
    (see `publishing.resolve_selected_posts`) plus its hashtags."""
    for post in bundle.social_posts:
        if post.platform == platform:
            tags = " ".join(post.hashtags)
            return f"{post.text}\n\n{tags}".strip() if tags else post.text
    raise LookupError(f"Bundle has no post for platform {platform!r}.")


def _post_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8") or "{}"
        return {"_headers": dict(response.headers), **json.loads(body)}


class LinkedInPublisher:
    """Posts as `settings.linkedin_author_urn` (a `urn:li:person:...` or
    `urn:li:organization:...`). LinkedIn returns the new post's id in the
    `x-restli-id` response header rather than the body."""

    platform = "linkedin"

    def __init__(self, settings: Settings):
        self.settings = settings

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult:
        token = self.settings.linkedin_access_token
        author = self.settings.linkedin_author_urn
        if not token or not author:
            raise PublisherNotConfigured(
                "LinkedIn needs LINKEDIN_ACCESS_TOKEN and LINKEDIN_AUTHOR_URN."
            )
        data = _post_json(
            LINKEDIN_POSTS_URL,
            {
                "author": author,
                "commentary": post_text(bundle, platform),
                "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED"},
                "lifecycleState": "PUBLISHED",
            },
            {
                "Authorization": f"Bearer {token}",
                "LinkedIn-Version": LINKEDIN_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        external_id = data.get("_headers", {}).get("x-restli-id") or data.get("id")
        return PublishResult(
            platform=platform, success=True, external_id=external_id, detail="Posted to LinkedIn."
        )


class XPublisher:
    platform = "x"

    def __init__(self, settings: Settings):
        self.settings = settings

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult:
        token = self.settings.x_access_token
        if not token:
            raise PublisherNotConfigured("X needs X_ACCESS_TOKEN.")
        data = _post_json(
            X_TWEETS_URL,
            {"text": post_text(bundle, platform)},
            {"Authorization": f"Bearer {token}"},
        )
        return PublishResult(
            platform=platform,
            success=True,
            external_id=(data.get("data") or {}).get("id"),
            detail="Posted to X.",
        )


class FacebookPublisher:
    platform = "facebook"

    def __init__(self, settings: Settings):
        self.settings = settings

    def publish(self, bundle: ContentBundle, platform: str) -> PublishResult:
        token = self.settings.facebook_page_access_token
        page_id = self.settings.facebook_page_id
        if not token or not page_id:
            raise PublisherNotConfigured(
                "Facebook needs FACEBOOK_PAGE_ACCESS_TOKEN and FACEBOOK_PAGE_ID."
            )
        data = _post_json(
            f"{FACEBOOK_GRAPH_URL}/{page_id}/feed",
            {"message": post_text(bundle, platform), "access_token": token},
            {},
        )
        return PublishResult(
            platform=platform, success=True, external_id=data.get("id"), detail="Posted to Facebook."
        )


PUBLISHER_CLASSES = {
    "linkedin": LinkedInPublisher,
    "x": XPublisher,
    "facebook": FacebookPublisher,
}


def configured_platforms(settings: Settings) -> set[str]:
    """Which platforms have complete credentials. Used by `get_publisher()`
    to decide per platform whether to go live or stay on the stub, and
    surfaced through the API so the UI can say which are wired up."""
    live = set()
    if settings.linkedin_access_token and settings.linkedin_author_urn:
        live.add("linkedin")
    if settings.x_access_token:
        live.add("x")
    if settings.facebook_page_access_token and settings.facebook_page_id:
        live.add("facebook")
    return live


__all__ = [
    "PUBLISHER_CLASSES",
    "FacebookPublisher",
    "LinkedInPublisher",
    "PublisherNotConfigured",
    "XPublisher",
    "configured_platforms",
    "post_text",
]
