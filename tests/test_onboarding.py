def test_fresh_company_has_no_steps_done(client, signup):
    signup(client)

    r = client.get("/onboarding/status")
    assert r.status_code == 200
    data = r.json()
    assert data["all_done"] is False
    assert {s["key"]: s["done"] for s in data["steps"]} == {
        "brand_voice": False,
        "content_model": False,
        "logo": False,
        "first_run": False,
        "team": False,
    }


def test_brand_voice_step_completes_after_setting_it(client, signup):
    signup(client)
    client.put("/auth/me/brand-voice", json={"brand_voice": "Warm and plain-spoken."})

    steps = {s["key"]: s["done"] for s in client.get("/onboarding/status").json()["steps"]}
    assert steps["brand_voice"] is True


def test_first_run_step_completes_after_daily_content_run(client, signup):
    signup(client)
    client.post("/daily-content/run")

    steps = {s["key"]: s["done"] for s in client.get("/onboarding/status").json()["steps"]}
    assert steps["first_run"] is True


def test_team_step_completes_after_invite(client, signup):
    signup(client)
    client.post("/auth/invite", json={"email": "teammate@acme.test", "password": "hunter22"})

    steps = {s["key"]: s["done"] for s in client.get("/onboarding/status").json()["steps"]}
    assert steps["team"] is True


def test_all_done_true_once_every_step_is_complete(client, signup):
    signup(client)
    client.put("/auth/me/brand-voice", json={"brand_voice": "Warm and plain-spoken."})
    client.put("/auth/me/content-model", json={"content_model": "openai/gpt-5"})
    client.post("/daily-content/run")
    client.post("/auth/invite", json={"email": "teammate@acme.test", "password": "hunter22"})

    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (20, 20), (0, 0, 0, 255)).save(buf, format="PNG")
    client.post(
        "/assets/upload",
        params={"is_logo": "true"},
        files={"file": ("logo.png", buf.getvalue(), "image/png")},
    )

    data = client.get("/onboarding/status").json()
    assert data["all_done"] is True
    assert all(s["done"] for s in data["steps"])


def test_onboarding_status_requires_authentication(client):
    r = client.get("/onboarding/status")
    assert r.status_code == 401
