def test_owner_can_set_brand_voice(client, signup):
    signup(client)

    r = client.put("/auth/me/brand-voice", json={"brand_voice": "Warm and plain-spoken."})
    assert r.status_code == 200
    assert r.json()["brand_voice"] == "Warm and plain-spoken."

    me = client.get("/auth/me").json()
    assert me["company"]["brand_voice"] == "Warm and plain-spoken."


def test_empty_brand_voice_clears_it(client, signup):
    signup(client)
    client.put("/auth/me/brand-voice", json={"brand_voice": "Something"})

    r = client.put("/auth/me/brand-voice", json={"brand_voice": "   "})
    assert r.status_code == 200
    assert r.json()["brand_voice"] is None


def test_member_cannot_set_brand_voice(client, signup):
    signup(client, email="owner@acme.test")
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})

    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    r = client.put("/auth/me/brand-voice", json={"brand_voice": "Nope"})
    assert r.status_code == 403


def test_invite_creates_teammate_in_same_company(client, signup):
    signup(client, company_name="Acme Inc.", email="owner@acme.test")

    invite = client.post("/auth/invite", json={"email": "teammate@acme.test", "password": "hunter22"})
    assert invite.status_code == 200
    assert invite.json() == {"email": "teammate@acme.test", "role": "member"}

    team = client.get("/auth/team").json()
    emails = {m["email"] for m in team}
    assert emails == {"owner@acme.test", "teammate@acme.test"}

    client.cookies.clear()
    login = client.post("/auth/login", json={"email": "teammate@acme.test", "password": "hunter22"})
    assert login.status_code == 200
    assert login.json()["company"]["name"] == "Acme Inc."


def test_invite_duplicate_email_rejected(client, signup):
    signup(client, email="owner@acme.test")
    client.post("/auth/invite", json={"email": "teammate@acme.test", "password": "hunter22"})

    dup = client.post("/auth/invite", json={"email": "teammate@acme.test", "password": "hunter22"})
    assert dup.status_code == 400


def test_member_cannot_invite(client, signup):
    signup(client, email="owner@acme.test")
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})

    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    r = client.post("/auth/invite", json={"email": "someone@acme.test", "password": "hunter22"})
    assert r.status_code == 403
