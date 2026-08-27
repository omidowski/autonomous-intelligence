def test_signup_creates_company_and_session_cookie(client, signup):
    data = signup(client)
    assert data["role"] == "owner"
    assert data["company"]["name"] == "Acme Inc."
    assert client.cookies.get("ai_session")


def test_signup_duplicate_email_rejected(client, signup):
    signup(client, email="dup@acme.test")
    response = client.post(
        "/auth/signup",
        json={"company_name": "Other Co", "email": "dup@acme.test", "password": "hunter22"},
    )
    assert response.status_code == 400


def test_login_success_and_failure(client, signup):
    signup(client, email="owner@acme.test", password="hunter22")
    client.cookies.clear()

    ok = client.post("/auth/login", json={"email": "owner@acme.test", "password": "hunter22"})
    assert ok.status_code == 200

    client.cookies.clear()
    bad = client.post("/auth/login", json={"email": "owner@acme.test", "password": "wrong"})
    assert bad.status_code == 401


def test_me_reflects_auth_state(client, signup):
    before = client.get("/auth/me")
    assert before.status_code == 200
    assert before.json() is None

    signup(client, email="owner@acme.test")

    after = client.get("/auth/me")
    assert after.status_code == 200
    assert after.json()["email"] == "owner@acme.test"


def test_logout_clears_session(client, signup):
    signup(client)
    assert client.get("/auth/me").json() is not None

    client.post("/auth/logout")
    assert client.get("/auth/me").json() is None

    protected = client.post("/research", json={"query": "a sufficiently long test query"})
    assert protected.status_code == 401
