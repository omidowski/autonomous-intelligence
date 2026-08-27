import io

from PIL import Image

from autonomous_intelligence.brand_assets import apply_logo_watermark


def _png_bytes(size=(40, 40), color=(255, 0, 0, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_owner_can_upload_and_list_asset(client, signup):
    signup(client)

    r = client.post(
        "/assets/upload",
        files={"file": ("logo.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"] == "logo.png"
    assert body["content_type"] == "image/png"
    assert body["is_logo"] is False
    assert body["url"] == f"/assets/{body['id']}/file"

    listing = client.get("/assets").json()
    assert len(listing) == 1
    assert listing[0]["id"] == body["id"]


def test_upload_with_is_logo_true(client, signup):
    signup(client)

    r = client.post(
        "/assets/upload",
        params={"is_logo": "true"},
        files={"file": ("logo.png", _png_bytes(), "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["is_logo"] is True


def test_uploading_new_logo_demotes_previous_logo(client, signup):
    signup(client)

    first = client.post(
        "/assets/upload",
        params={"is_logo": "true"},
        files={"file": ("first.png", _png_bytes(), "image/png")},
    ).json()
    second = client.post(
        "/assets/upload",
        params={"is_logo": "true"},
        files={"file": ("second.png", _png_bytes(), "image/png")},
    ).json()

    listing = {row["id"]: row for row in client.get("/assets").json()}
    assert listing[first["id"]]["is_logo"] is False
    assert listing[second["id"]]["is_logo"] is True


def test_upload_rejects_unsupported_content_type(client, signup):
    signup(client)

    r = client.post(
        "/assets/upload",
        files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400


def test_upload_rejects_oversized_file(client, signup):
    signup(client)

    oversized = b"\x00" * (5 * 1024 * 1024 + 1)
    r = client.post(
        "/assets/upload",
        files={"file": ("huge.png", oversized, "image/png")},
    )
    assert r.status_code == 400


def test_member_cannot_upload_or_delete(client, signup):
    signup(client, email="owner@acme.test")
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})

    owner_asset = client.post(
        "/assets/upload", files={"file": ("logo.png", _png_bytes(), "image/png")}
    ).json()

    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    upload = client.post(
        "/assets/upload", files={"file": ("logo2.png", _png_bytes(), "image/png")}
    )
    assert upload.status_code == 403

    delete = client.delete(f"/assets/{owner_asset['id']}")
    assert delete.status_code == 403

    # members can still list and view.
    assert client.get("/assets").status_code == 200
    assert client.get(f"/assets/{owner_asset['id']}/file").status_code == 200


def test_upload_requires_authentication(client):
    r = client.post(
        "/assets/upload", files={"file": ("logo.png", _png_bytes(), "image/png")}
    )
    assert r.status_code == 401


def test_get_asset_file_serves_bytes(client, signup):
    signup(client)
    asset = client.post(
        "/assets/upload", files={"file": ("logo.png", _png_bytes(), "image/png")}
    ).json()

    r = client.get(f"/assets/{asset['id']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content


def test_get_asset_file_404_for_unknown_id(client, signup):
    signup(client)
    r = client.get("/assets/999999/file")
    assert r.status_code == 404


def test_owner_can_delete_asset(client, signup):
    signup(client)
    asset = client.post(
        "/assets/upload", files={"file": ("logo.png", _png_bytes(), "image/png")}
    ).json()

    r = client.delete(f"/assets/{asset['id']}")
    assert r.status_code == 200

    assert client.get("/assets").json() == []
    assert client.get(f"/assets/{asset['id']}/file").status_code == 404


def test_assets_are_isolated_per_company(client, signup):
    signup(client, company_name="Acme Inc.", email="owner@acme.test")
    asset = client.post(
        "/assets/upload", files={"file": ("logo.png", _png_bytes(), "image/png")}
    ).json()

    client.cookies.clear()
    signup(client, company_name="Other Co.", email="owner@other.test")

    assert client.get("/assets").json() == []
    assert client.get(f"/assets/{asset['id']}/file").status_code == 404
    assert client.delete(f"/assets/{asset['id']}").status_code == 404


def test_apply_logo_watermark_overlays_pixels(tmp_path):
    base_path = tmp_path / "base.png"
    logo_path = tmp_path / "logo.png"
    Image.new("RGBA", (200, 200), (255, 255, 255, 255)).save(base_path)
    Image.new("RGBA", (50, 50), (0, 0, 0, 255)).save(logo_path)

    apply_logo_watermark(base_path, logo_path)

    with Image.open(base_path) as result:
        assert result.size == (200, 200)
        corner_pixel = result.convert("RGB").getpixel((190, 190))
        assert corner_pixel != (255, 255, 255)


def test_apply_logo_watermark_noops_on_missing_file(tmp_path):
    base_path = tmp_path / "base.png"
    Image.new("RGBA", (100, 100), (255, 255, 255, 255)).save(base_path)

    apply_logo_watermark(base_path, tmp_path / "missing.png")

    with Image.open(base_path) as result:
        assert result.convert("RGB").getpixel((50, 50)) == (255, 255, 255)
