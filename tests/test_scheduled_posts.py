"""Covers the publishing calendar: `POST .../schedule`, the `/schedule`
router, and `scheduler.publish_due_posts()` draining the queue.

Time is controlled by writing rows with a past `scheduled_for` directly,
rather than sleeping - the queue is time-ordered data, so a due post is
just a row whose timestamp has passed.
"""

from datetime import UTC, datetime, timedelta

from autonomous_intelligence import db, scheduler
from autonomous_intelligence.config import get_settings


def _future(minutes: int = 60) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _approved_run(client, signup) -> str:
    signup(client)
    date = client.post("/daily-content/run").json()["date"]
    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    client.post(f"/daily-content/{date}/trend/1/approve", json={})
    return date


# --- scheduling ---------------------------------------------------------


def test_schedule_requires_approval(client, signup):
    signup(client)
    date = client.post("/daily-content/run").json()["date"]
    r = client.post(
        f"/daily-content/{date}/trend/1/schedule",
        json={"scheduled_for": _future(), "platforms": ["x"]},
    )
    assert r.status_code == 403


def test_schedule_rejects_past_and_unknown_platform(client, signup):
    date = _approved_run(client, signup)

    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    assert client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": past}
    ).status_code == 400

    assert client.post(
        f"/daily-content/{date}/trend/1/schedule",
        json={"scheduled_for": _future(), "platforms": ["myspace"]},
    ).status_code == 400


def test_schedule_queues_every_platform_by_default(client, signup):
    date = _approved_run(client, signup)
    r = client.post(f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": _future()})
    assert r.status_code == 200
    body = r.json()
    assert body["queued"]
    assert body["already_queued"] == []

    calendar = client.get("/schedule").json()
    assert {row["platform"] for row in calendar} == set(body["queued"])
    assert all(row["status"] == "pending" for row in calendar)


def test_scheduling_the_same_slot_twice_is_idempotent(client, signup):
    date = _approved_run(client, signup)
    when = _future()
    first = client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": when, "platforms": ["x"]}
    ).json()
    second = client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": when, "platforms": ["x"]}
    ).json()

    assert first["queued"] == ["x"]
    assert second["queued"] == []
    assert second["already_queued"] == ["x"]
    assert len(client.get("/schedule").json()) == 1


# --- calendar router ----------------------------------------------------


def test_schedule_listing_requires_auth(client):
    assert client.get("/schedule").status_code == 401


def test_schedule_filter_and_cancel(client, signup):
    date = _approved_run(client, signup)
    client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": _future(), "platforms": ["x"]}
    )
    post_id = client.get("/schedule").json()[0]["id"]

    assert client.get("/schedule", params={"status": "pending"}).json()
    assert client.get("/schedule", params={"status": "published"}).json() == []
    assert client.get("/schedule", params={"status": "bogus"}).status_code == 400

    assert client.delete(f"/schedule/{post_id}").status_code == 200
    assert client.get("/schedule").json()[0]["status"] == "canceled"
    # A canceled post is no longer pending, so it cannot be canceled again.
    assert client.delete(f"/schedule/{post_id}").status_code == 404


def test_schedule_is_scoped_per_company(client, signup):
    date = _approved_run(client, signup)
    client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": _future(), "platforms": ["x"]}
    )

    client.cookies.clear()
    signup(client, company_name="Other Co", email="other@other.test")
    assert client.get("/schedule").json() == []


# --- the scheduler draining the queue -----------------------------------


def _make_due(settings, company_id: int, date: str, platform: str = "x") -> int:
    """Queues a post already in the past - what the scheduler will pick up."""
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    post_id = db.schedule_post(
        settings.database_path,
        company_id=company_id,
        date=date,
        trend_index=1,
        platform=platform,
        scheduled_for=past,
    )
    assert post_id is not None
    return post_id


def test_publish_due_posts_publishes_and_marks_published(client, signup):
    date = _approved_run(client, signup)
    settings = get_settings()
    _make_due(settings, 1, date)

    assert scheduler.publish_due_posts(settings) == 1

    row = client.get("/schedule").json()[0]
    assert row["status"] == "published"
    assert row["published_at"] is not None
    assert row["external_id"]


def test_publish_due_posts_ignores_future_slots(client, signup):
    date = _approved_run(client, signup)
    client.post(
        f"/daily-content/{date}/trend/1/schedule", json={"scheduled_for": _future(), "platforms": ["x"]}
    )
    assert scheduler.publish_due_posts(get_settings()) == 0
    assert client.get("/schedule").json()[0]["status"] == "pending"


def test_approval_can_be_withdrawn_while_a_post_is_queued(client, signup):
    """Approval is not terminal precisely because scheduling exists - see
    `review.ALLOWED_TRANSITIONS`."""
    date = _approved_run(client, signup)
    r = client.post(f"/daily-content/{date}/trend/1/reject", json={})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"


def test_publish_due_posts_fails_closed_when_approval_is_withdrawn(client, signup):
    """The real risk this guards: content approved when scheduled, then
    rejected before the slot comes up, must not go out."""
    date = _approved_run(client, signup)
    settings = get_settings()
    _make_due(settings, 1, date)
    assert client.post(f"/daily-content/{date}/trend/1/reject", json={}).status_code == 200

    assert scheduler.publish_due_posts(settings) == 0
    row = client.get("/schedule").json()[0]
    assert row["status"] == "failed"
    assert "approved" in (row["detail"] or "").lower()


def test_a_failed_post_is_not_retried_on_the_next_tick(client, signup):
    date = _approved_run(client, signup)
    settings = get_settings()
    _make_due(settings, 1, date)
    assert client.post(f"/daily-content/{date}/trend/1/reject", json={}).status_code == 200

    scheduler.publish_due_posts(settings)
    # Second tick sees nothing pending - no retry storm.
    assert scheduler.publish_due_posts(settings) == 0
    assert len(db.list_due_scheduled_posts(settings.database_path, now_iso=datetime.now(UTC).isoformat())) == 0


def test_canceled_posts_are_never_published(client, signup):
    date = _approved_run(client, signup)
    settings = get_settings()
    post_id = _make_due(settings, 1, date)
    client.delete(f"/schedule/{post_id}")

    assert scheduler.publish_due_posts(settings) == 0
    assert client.get("/schedule").json()[0]["status"] == "canceled"
