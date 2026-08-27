def test_custom_content_requires_auth(client):
    assert client.post("/custom-content/run", json={"topic": "Remote work tips"}).status_code == 401
    assert client.get("/custom-content/history").status_code == 401
    assert client.get("/custom-content/custom-20260101-000000-abc123").status_code == 401


def test_custom_content_run_happy_path(client, signup):
    signup(client)

    response = client.post("/custom-content/run", json={"topic": "The benefits of a four-day work week"})
    assert response.status_code == 200
    data = response.json()

    assert data["date"].startswith("custom-")
    assert len(data["bundles"]) == 1
    assert data["trends"][0]["title"] == "The benefits of a four-day work week"
    assert data["bundles"][0]["status"]["status"] == "draft"


def test_custom_content_history_and_detail(client, signup):
    signup(client)
    run = client.post("/custom-content/run", json={"topic": "Remote work productivity tips"})
    run_id = run.json()["date"]

    history = client.get("/custom-content/history")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    assert entries[0]["run_id"] == run_id
    assert entries[0]["topic"] == "Remote work productivity tips"

    detail = client.get(f"/custom-content/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["date"] == run_id


def test_custom_content_detail_unknown_run_is_404(client, signup):
    signup(client)
    response = client.get("/custom-content/custom-20200101-000000-ffffff")
    assert response.status_code == 404


def test_custom_content_detail_rejects_non_custom_ids(client, signup):
    signup(client)
    response = client.get("/custom-content/2026-08-26")
    assert response.status_code == 400


def test_custom_content_review_workflow_reuses_daily_content_endpoints(client, signup):
    signup(client)
    run = client.post("/custom-content/run", json={"topic": "AI in healthcare"})
    run_id = run.json()["date"]

    submit = client.post(f"/daily-content/{run_id}/trend/1/submit-for-review", json={})
    assert submit.status_code == 200

    approve = client.post(f"/daily-content/{run_id}/trend/1/approve", json={})
    assert approve.status_code == 200

    detail = client.get(f"/custom-content/{run_id}")
    assert detail.json()["bundles"][0]["status"]["status"] == "approved"


def test_custom_content_isolated_per_company(client, signup):
    signup(client, company_name="Company A", email="a@a.test")
    run_a = client.post("/custom-content/run", json={"topic": "Topic from company A"})
    assert run_a.status_code == 200

    client.cookies.clear()
    signup(client, company_name="Company B", email="b@b.test")

    history_b = client.get("/custom-content/history")
    assert history_b.json() == []

    forbidden = client.get(f"/custom-content/{run_a.json()['date']}")
    assert forbidden.status_code == 404
