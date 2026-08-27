def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_research_requires_auth(client):
    response = client.post("/research", json={"query": "a sufficiently long test query"})
    assert response.status_code == 401


def test_daily_content_requires_auth(client):
    assert client.get("/daily-content/dates").status_code == 401
    assert client.get("/daily-content/2026-01-01").status_code == 401
    assert client.post("/daily-content/run").status_code == 401


def test_daily_content_run_happy_path_includes_draft_status(client, signup):
    signup(client)

    response = client.post("/daily-content/run")
    assert response.status_code == 200
    data = response.json()
    assert data["bundles"]
    for bundle in data["bundles"]:
        assert bundle["status"]["status"] == "draft"


def test_research_persists_and_lists_history(client, signup):
    signup(client)

    run = client.post("/research", json={"query": "How will agentic AI change enterprise software?"})
    assert run.status_code == 200

    history = client.get("/research/history")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["query"] == "How will agentic AI change enterprise software?"

    detail = client.get(f"/research/{entries[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["executive_summary"]
