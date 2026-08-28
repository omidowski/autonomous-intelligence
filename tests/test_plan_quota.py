"""Covers `api._enforce_plan_quota` / `plans.py` - the per-plan monthly
caps on metered endpoints. Limits default to `None` (unlimited), so these
tests set one explicitly via `monkeypatch`.
"""

from autonomous_intelligence import plans


def _set_limit(monkeypatch, plan, resource, value):
    monkeypatch.setitem(plans.PLAN_LIMITS[plan], resource, value)


def test_research_is_unlimited_by_default(client, signup):
    signup(client)
    for _ in range(3):
        assert client.post("/research", json={"query": "a sufficiently long research query"}).status_code == 200


def test_research_over_free_limit_returns_402(client, signup, monkeypatch):
    _set_limit(monkeypatch, "free", "research", 2)
    signup(client)

    assert client.post("/research", json={"query": "first long enough research query"}).status_code == 200
    assert client.post("/research", json={"query": "second long enough research query"}).status_code == 200

    blocked = client.post("/research", json={"query": "third long enough research query"})
    assert blocked.status_code == 402
    assert "upgrade" in blocked.json()["detail"].lower()


def test_content_run_over_free_limit_returns_402(client, signup, monkeypatch):
    _set_limit(monkeypatch, "free", "content_runs", 1)
    signup(client)

    assert client.post("/daily-content/run").status_code == 200
    assert client.post("/daily-content/run").status_code == 402
    # custom-content shares the same counter
    assert client.post("/custom-content/run", json={"topic": "anything"}).status_code == 402


def test_paid_plan_bypasses_free_limit(client, signup, monkeypatch):
    _set_limit(monkeypatch, "free", "research", 1)
    _set_limit(monkeypatch, "pro", "research", None)
    signup(client)

    # Promote the company to pro directly (the webhook path is covered in
    # test_billing_api.py).
    from autonomous_intelligence import db
    from autonomous_intelligence.config import get_settings

    db.update_company_subscription(get_settings().database_path, 1, plan="pro", status="active")

    for _ in range(3):
        assert client.post("/research", json={"query": "a sufficiently long research query"}).status_code == 200
