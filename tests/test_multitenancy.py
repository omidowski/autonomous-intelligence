def test_two_companies_get_isolated_output_dirs(client, signup):
    signup(client, company_name="Company A", email="a@a.test")
    run_a = client.post("/daily-content/run")
    assert run_a.status_code == 200

    client.cookies.clear()
    signup(client, company_name="Company B", email="b@b.test")
    run_b = client.post("/daily-content/run")
    assert run_b.status_code == 200

    dates_b = client.get("/daily-content/dates")
    assert dates_b.status_code == 200
    assert len(dates_b.json()) == 1  # only company B's own report


def test_cannot_read_other_companys_media(client, signup):
    signup(client, company_name="Company A", email="a@a.test")
    run_a = client.post("/daily-content/run")
    assert run_a.status_code == 200
    data_a = run_a.json()
    asset_url = next(
        asset["url"]
        for bundle in data_a["bundles"]
        for asset in bundle["assets"]
        if asset.get("url")
    )
    assert asset_url.split("/")[2] != ""  # sanity: /media/<slug>/...

    client.cookies.clear()
    signup(client, company_name="Company B", email="b@b.test")

    forbidden = client.get(asset_url)
    assert forbidden.status_code == 403


def test_cannot_run_or_view_daily_content_without_login(client, signup):
    signup(client, company_name="Company A", email="a@a.test")
    run_a = client.post("/daily-content/run")
    date = run_a.json()["date"]

    client.cookies.clear()
    assert client.get(f"/daily-content/{date}").status_code == 401
