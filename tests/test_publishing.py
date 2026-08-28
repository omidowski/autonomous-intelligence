from autonomous_intelligence.content_models import ContentBundle
from autonomous_intelligence.publishers import get_publisher
from autonomous_intelligence.publishers.stub import StubPublisher


def _demo_bundle() -> ContentBundle:
    return ContentBundle.model_validate(
        {
            "trend": {
                "rank": 1,
                "title": "Test trend",
                "summary": "Summary",
                "category": "tech",
                "source_urls": [],
                "search_query": "test",
            },
            "social_posts": [{"platform": "x", "text": "hello", "hashtags": []}],
            "short_script": {"hook": "h", "beats": ["b"], "cta": "c", "est_duration_seconds": 30},
            "story": {"slides": []},
            "podcast_script": {
                "title": "t",
                "intro": "i",
                "segments": [],
                "outro": "o",
                "est_duration_minutes": 1,
            },
            "video_script": {"title": "t", "shots": []},
            "assets": [],
        }
    )


def test_stub_publisher_reports_success_without_network_io():
    publisher = StubPublisher()
    result = publisher.publish(_demo_bundle(), "x")

    assert result.success is True
    assert result.platform == "x"
    assert result.external_id.startswith("stub-")


def test_get_publisher_registry_returns_stub_by_default():
    assert isinstance(get_publisher(), StubPublisher)
    assert isinstance(get_publisher("stub"), StubPublisher)


def test_publish_after_approve_writes_results_per_platform(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]
    # Demo content has 2 variants (A/B) per platform - publish resolves down
    # to one post per platform (the selected variant, default "A"), so the
    # expected result count is the number of *unique* platforms, not the
    # raw social_posts count.
    platforms = {p["platform"] for p in run.json()["bundles"][0]["social_posts"]}

    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    client.post(f"/daily-content/{date}/trend/1/approve", json={})

    publish = client.post(f"/daily-content/{date}/trend/1/publish", json={})
    assert publish.status_code == 200
    results = publish.json()["results"]
    assert len(results) == len(platforms)
    assert all(r["success"] for r in results)


def test_publish_uses_selected_variant(client, signup, monkeypatch):
    from autonomous_intelligence import publishing
    from autonomous_intelligence.publishers.base import PublishResult

    published_texts = []

    class RecordingPublisher:
        def publish(self, bundle, platform):
            post = next(p for p in bundle.social_posts if p.platform == platform)
            published_texts.append(post.text)
            return PublishResult(platform=platform, success=True, external_id="rec-1", detail="")

    # `review_api.publish` delegates to `publishing.publish_bundle_platforms`,
    # which resolves the publisher through its own module-level import - so
    # `publishing` is the binding that has to be replaced, not `review_api`
    # (which never imported the name at all).
    monkeypatch.setattr(publishing, "get_publisher", lambda name="stub": RecordingPublisher())

    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]
    posts = run.json()["bundles"][0]["social_posts"]
    x_variant_a = next(p for p in posts if p["platform"] == "x" and p["variant"] == "A")
    x_variant_b = next(p for p in posts if p["platform"] == "x" and p["variant"] == "B")
    assert x_variant_a["text"] != x_variant_b["text"]

    client.put(f"/daily-content/{date}/trend/1/variant", json={"platform": "x", "variant": "B"})
    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    client.post(f"/daily-content/{date}/trend/1/approve", json={})
    publish = client.post(f"/daily-content/{date}/trend/1/publish", json={"platforms": ["x"]})

    assert publish.status_code == 200
    assert published_texts == [x_variant_b["text"]]


def test_publish_defaults_to_variant_a_when_unselected(client, signup, monkeypatch):
    from autonomous_intelligence import publishing
    from autonomous_intelligence.publishers.base import PublishResult

    published_texts = []

    class RecordingPublisher:
        def publish(self, bundle, platform):
            post = next(p for p in bundle.social_posts if p.platform == platform)
            published_texts.append(post.text)
            return PublishResult(platform=platform, success=True, external_id="rec-1", detail="")

    # `review_api.publish` delegates to `publishing.publish_bundle_platforms`,
    # which resolves the publisher through its own module-level import - so
    # `publishing` is the binding that has to be replaced, not `review_api`
    # (which never imported the name at all).
    monkeypatch.setattr(publishing, "get_publisher", lambda name="stub": RecordingPublisher())

    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]
    posts = run.json()["bundles"][0]["social_posts"]
    x_variant_a = next(p for p in posts if p["platform"] == "x" and p["variant"] == "A")

    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    client.post(f"/daily-content/{date}/trend/1/approve", json={})
    publish = client.post(f"/daily-content/{date}/trend/1/publish", json={"platforms": ["x"]})

    assert publish.status_code == 200
    assert published_texts == [x_variant_a["text"]]
