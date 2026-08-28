"""Covers `publishers/social.py` and the `AutoPublisher` routing.

No test makes a real network call: `_post_json` is monkeypatched to capture
what *would* be sent. What matters here is that a platform without
credentials never reports a successful publish, and that a configured one
sends the right payload to the right URL.
"""

import urllib.error

import pytest

from autonomous_intelligence.config import Settings
from autonomous_intelligence.content_models import ContentBundle
from autonomous_intelligence.publishers import AutoPublisher, get_publisher, social

_BUNDLE = ContentBundle.model_validate(
    {
        "trend": {
            "rank": 1, "title": "T", "summary": "S", "category": "C",
            "source_urls": [], "search_query": "q",
        },
        "social_posts": [
            {"platform": "linkedin", "text": "Pro take", "hashtags": ["#ai"], "variant": "A"},
            {"platform": "x", "text": "Short take", "hashtags": [], "variant": "A"},
            {"platform": "facebook", "text": "FB take", "hashtags": ["#news"], "variant": "A"},
        ],
        "short_script": {"hook": "h", "beats": ["b"], "cta": "c", "est_duration_seconds": 30},
        "story": {"slides": []},
        "podcast_script": {
            "title": "t", "intro": "i", "segments": ["s"], "outro": "o",
            "est_duration_minutes": 3,
        },
        "video_script": {"title": "v", "shots": []},
    }
)

_ALL_CREDS = {
    "linkedin_access_token": "li-token",
    "linkedin_author_urn": "urn:li:person:123",
    "x_access_token": "x-token",
    "facebook_page_access_token": "fb-token",
    "facebook_page_id": "999",
}


@pytest.fixture
def sent(monkeypatch):
    """Captures outbound calls instead of making them."""
    calls = []

    def fake_post_json(url, payload, headers):
        calls.append({"url": url, "payload": payload, "headers": headers})
        return {"_headers": {"x-restli-id": "li-post-1"}, "id": "fb-post-1", "data": {"id": "x-post-1"}}

    monkeypatch.setattr(social, "_post_json", fake_post_json)
    return calls


# --- copy assembly ------------------------------------------------------


def test_post_text_appends_hashtags():
    assert social.post_text(_BUNDLE, "linkedin") == "Pro take\n\n#ai"
    assert social.post_text(_BUNDLE, "x") == "Short take"  # no hashtags -> no trailing blank


def test_post_text_rejects_a_platform_the_bundle_lacks():
    with pytest.raises(LookupError):
        social.post_text(_BUNDLE, "tiktok")


# --- configuration detection -------------------------------------------


def test_configured_platforms_needs_every_required_credential():
    assert social.configured_platforms(Settings()) == set()
    # LinkedIn needs both token and author urn.
    assert social.configured_platforms(Settings(linkedin_access_token="t")) == set()
    assert social.configured_platforms(
        Settings(linkedin_access_token="t", linkedin_author_urn="urn:li:person:1")
    ) == {"linkedin"}
    # Facebook needs both token and page id.
    assert social.configured_platforms(Settings(facebook_page_access_token="t")) == set()
    assert social.configured_platforms(Settings(**_ALL_CREDS)) == {"linkedin", "x", "facebook"}


# --- real publishers ----------------------------------------------------


def test_linkedin_publishes_with_author_and_returns_header_id(sent):
    result = social.LinkedInPublisher(Settings(**_ALL_CREDS)).publish(_BUNDLE, "linkedin")
    assert result.success and result.external_id == "li-post-1"
    call = sent[0]
    assert call["url"] == social.LINKEDIN_POSTS_URL
    assert call["payload"]["author"] == "urn:li:person:123"
    assert call["payload"]["commentary"] == "Pro take\n\n#ai"
    assert call["headers"]["Authorization"] == "Bearer li-token"
    assert call["headers"]["LinkedIn-Version"]


def test_x_publishes_the_tweet_text(sent):
    result = social.XPublisher(Settings(**_ALL_CREDS)).publish(_BUNDLE, "x")
    assert result.success and result.external_id == "x-post-1"
    assert sent[0]["url"] == social.X_TWEETS_URL
    assert sent[0]["payload"] == {"text": "Short take"}


def test_facebook_posts_to_the_configured_page(sent):
    result = social.FacebookPublisher(Settings(**_ALL_CREDS)).publish(_BUNDLE, "facebook")
    assert result.success and result.external_id == "fb-post-1"
    assert sent[0]["url"].endswith("/999/feed")
    assert sent[0]["payload"]["access_token"] == "fb-token"


@pytest.mark.parametrize(
    ("publisher_cls", "platform"),
    [
        (social.LinkedInPublisher, "linkedin"),
        (social.XPublisher, "x"),
        (social.FacebookPublisher, "facebook"),
    ],
)
def test_publishers_refuse_without_credentials(publisher_cls, platform, sent):
    with pytest.raises(social.PublisherNotConfigured):
        publisher_cls(Settings()).publish(_BUNDLE, platform)
    assert sent == []  # nothing was sent


# --- AutoPublisher routing ---------------------------------------------


def test_auto_falls_back_to_stub_for_unconfigured_platforms(sent):
    auto = AutoPublisher(Settings())
    result = auto.publish(_BUNDLE, "linkedin")
    assert result.success
    assert "Stub publish" in result.detail
    assert sent == []  # the stub makes no call


def test_auto_routes_only_configured_platforms_to_the_real_api(sent):
    auto = AutoPublisher(Settings(x_access_token="x-token"))

    live = auto.publish(_BUNDLE, "x")
    assert live.external_id == "x-post-1"

    stubbed = auto.publish(_BUNDLE, "linkedin")
    assert "Stub publish" in stubbed.detail
    assert len(sent) == 1  # only X went out


def test_auto_reports_an_api_error_as_a_failed_publish(monkeypatch):
    """A dead platform must fail that one platform, not raise and abort a
    multi-platform publish."""
    def boom(url, payload, headers):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(social, "_post_json", boom)
    result = AutoPublisher(Settings(x_access_token="t")).publish(_BUNDLE, "x")
    assert result.success is False
    assert "401" in result.detail
    assert result.external_id is None


def test_auto_reports_a_network_error_as_a_failed_publish(monkeypatch):
    def boom(url, payload, headers):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(social, "_post_json", boom)
    result = AutoPublisher(Settings(x_access_token="t")).publish(_BUNDLE, "x")
    assert result.success is False


def test_get_publisher_registry():
    assert type(get_publisher("stub")).__name__ == "StubPublisher"
    assert type(get_publisher("auto")).__name__ == "AutoPublisher"
    with pytest.raises(ValueError, match="Unknown publisher"):
        get_publisher("nope")
