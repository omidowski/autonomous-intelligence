from autonomous_intelligence import usage
from autonomous_intelligence.config import Settings


def test_usage_starts_at_zero(client, signup):
    signup(client)

    r = client.get("/usage")
    assert r.status_code == 200
    data = r.json()
    assert data["research_calls"] == 0
    assert data["content_runs"] == 0
    assert data["trends_generated"] == 0
    assert data["estimated_cost_usd"] == 0.0


def test_research_and_content_run_are_tracked(client, signup):
    signup(client)

    client.post("/research", json={"query": "a sufficiently long test query"})
    run = client.post("/daily-content/run")
    trend_count = len(run.json()["bundles"])

    data = client.get("/usage").json()
    assert data["research_calls"] == 1
    assert data["content_runs"] == 1
    assert data["trends_generated"] == trend_count
    assert data["estimated_cost_usd"] > 0


def test_get_monthly_summary_helper_matches_endpoint(tmp_path):
    from autonomous_intelligence import db

    settings = Settings(database_path=str(tmp_path / "app.db"))
    db.init_db(settings.database_path)
    company_id = db.insert_company(settings.database_path, slug="acme", name="Acme")

    usage.record_research(settings, company_id)
    usage.record_daily_content_run(settings, company_id, trend_count=3)

    summary = usage.get_monthly_summary(settings, company_id)
    assert summary["research_calls"] == 1
    assert summary["content_runs"] == 1
    assert summary["trends_generated"] == 3
    expected_cost = round(
        usage.ESTIMATED_COST_USD_PER_RESEARCH_CALL + usage.ESTIMATED_COST_USD_PER_CONTENT_TREND * 3, 2
    )
    assert summary["estimated_cost_usd"] == expected_cost
