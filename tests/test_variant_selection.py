def test_select_variant_persists_and_reflects_in_detail(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    select = client.put(
        f"/daily-content/{date}/trend/1/variant", json={"platform": "x", "variant": "B"}
    )
    assert select.status_code == 200
    assert select.json() == {"x": "B"}

    detail = client.get(f"/daily-content/{date}")
    assert detail.json()["bundles"][0]["variant_selections"] == {"x": "B"}


def test_select_variant_unknown_platform_is_400(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    r = client.put(
        f"/daily-content/{date}/trend/1/variant", json={"platform": "myspace", "variant": "A"}
    )
    assert r.status_code == 400


def test_select_variant_invalid_variant_letter_is_422(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    r = client.put(
        f"/daily-content/{date}/trend/1/variant", json={"platform": "x", "variant": "C"}
    )
    assert r.status_code == 422


def test_select_variant_missing_bundle_is_404(client, signup):
    signup(client)
    client.post("/daily-content/run")

    r = client.put(
        "/daily-content/2099-01-01/trend/1/variant", json={"platform": "x", "variant": "A"}
    )
    assert r.status_code == 404


def test_select_variant_requires_authentication(client):
    r = client.put(
        "/daily-content/2099-01-01/trend/1/variant", json={"platform": "x", "variant": "A"}
    )
    assert r.status_code == 401


def test_unselected_bundle_defaults_to_no_explicit_selection(client, signup):
    signup(client)
    run = client.post("/daily-content/run")
    date = run.json()["date"]

    detail = client.get(f"/daily-content/{date}")
    assert detail.json()["bundles"][0]["variant_selections"] == {}
