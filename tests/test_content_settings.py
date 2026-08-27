def test_owner_can_set_content_settings(client, signup):
    signup(client)

    r = client.put(
        "/auth/me/content-settings",
        json={
            "content_language": "German",
            "target_duration_seconds": 30,
            "image_size": "1024x1024",
            "image_quality": "high",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["content_language"] == "German"
    assert data["target_duration_seconds"] == 30
    assert data["image_size"] == "1024x1024"
    assert data["image_quality"] == "high"

    me = client.get("/auth/me").json()
    assert me["company"]["content_language"] == "German"
    assert me["company"]["target_duration_seconds"] == 30


def test_empty_content_settings_clear_fields(client, signup):
    signup(client)
    client.put(
        "/auth/me/content-settings",
        json={
            "content_language": "German",
            "target_duration_seconds": 30,
            "image_size": "1024x1024",
            "image_quality": "high",
        },
    )

    r = client.put(
        "/auth/me/content-settings",
        json={"content_language": "", "target_duration_seconds": None, "image_size": "", "image_quality": ""},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["content_language"] is None
    assert data["target_duration_seconds"] is None
    assert data["image_size"] is None
    assert data["image_quality"] is None


def test_invalid_image_size_rejected(client, signup):
    signup(client)
    r = client.put("/auth/me/content-settings", json={"image_size": "not-a-size"})
    assert r.status_code == 422


def test_invalid_image_quality_rejected(client, signup):
    signup(client)
    r = client.put("/auth/me/content-settings", json={"image_quality": "ultra"})
    assert r.status_code == 422


def test_duration_out_of_range_rejected(client, signup):
    signup(client)
    too_short = client.put("/auth/me/content-settings", json={"target_duration_seconds": 5})
    assert too_short.status_code == 422

    too_long = client.put("/auth/me/content-settings", json={"target_duration_seconds": 500})
    assert too_long.status_code == 422


def test_member_cannot_set_content_settings(client, signup):
    signup(client, email="owner@acme.test")
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})

    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    r = client.put("/auth/me/content-settings", json={"content_language": "French"})
    assert r.status_code == 403


def test_content_settings_choices_endpoint(client, signup):
    signup(client)
    r = client.get("/auth/content-settings-choices")
    assert r.status_code == 200
    data = r.json()
    assert "1024x1024" in data["image_size"]
    assert "high" in data["image_quality"]
