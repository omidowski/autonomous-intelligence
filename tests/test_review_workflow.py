def test_submit_approve_flow_updates_status(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    submit = client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    assert submit.status_code == 200
    assert submit.json()["status"] == "pending_review"

    approve = client.post(f"/daily-content/{date}/trend/1/approve", json={"notes": "looks good"})
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    assert approve.json()["reviewer_email"] == "owner@acme.test"

    detail = client.get(f"/daily-content/{date}")
    bundle_status = detail.json()["bundles"][0]["status"]
    assert bundle_status["status"] == "approved"


def test_illegal_transition_returns_409(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    # approving straight from draft (skipping submit-for-review) is illegal.
    approve = client.post(f"/daily-content/{date}/trend/1/approve", json={})
    assert approve.status_code == 409


def test_publish_before_approved_is_forbidden(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    publish = client.post(f"/daily-content/{date}/trend/1/publish", json={})
    assert publish.status_code == 403

    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    still_forbidden = client.post(f"/daily-content/{date}/trend/1/publish", json={})
    assert still_forbidden.status_code == 403


def test_transition_on_missing_bundle_is_404(client, signup):
    signup(client)
    client.post("/daily-content/run")

    response = client.post("/daily-content/2099-01-01/trend/1/submit-for-review", json={})
    assert response.status_code == 404
