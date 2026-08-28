"""Covers `POST /custom-content/bulk` - batch custom-topic generation with
per-topic metering and quota-aware skipping.
"""

from autonomous_intelligence import plans


def test_bulk_validates_topic_list(client, signup):
    signup(client)
    assert client.post("/custom-content/bulk", json={"topics": []}).status_code == 422
    assert client.post("/custom-content/bulk", json={"topics": ["x"]}).status_code == 422  # too short
    assert client.post(
        "/custom-content/bulk", json={"topics": [f"topic number {i}" for i in range(11)]}
    ).status_code == 422


def test_bulk_generates_each_topic(client, signup):
    signup(client)
    r = client.post(
        "/custom-content/bulk",
        json={"topics": ["The rise of small language models", "Edge AI in manufacturing"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["requested"] == 2
    assert body["generated"] == 2
    assert body["skipped"] == 0
    assert all(item["status"] == "generated" and item["run_id"].startswith("custom-") for item in body["results"])

    # Both runs are now visible in history.
    assert len(client.get("/custom-content/history").json()) == 2


def test_bulk_skips_topics_over_quota(client, signup, monkeypatch):
    monkeypatch.setitem(plans.PLAN_LIMITS["free"], "content_runs", 1)
    signup(client)

    body = client.post(
        "/custom-content/bulk",
        json={"topics": ["first topic here", "second topic here", "third topic here"]},
    ).json()
    assert body["generated"] == 1
    assert body["skipped"] == 2
    statuses = [item["status"] for item in body["results"]]
    assert statuses == ["generated", "skipped", "skipped"]
