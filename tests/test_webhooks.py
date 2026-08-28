"""Covers `webhooks.py` (URL guard, signing, delivery selection) and the
owner-only `/webhooks` CRUD router.
"""

import socket
from urllib.parse import urlparse

import pytest

from autonomous_intelligence import db, webhooks
from autonomous_intelligence.config import get_settings


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/hook",
        "example.com/hook",
        "http://127.0.0.1/hook",
        "http://10.0.0.5/hook",
        "http://[::1]/hook",
        "http://169.254.1.1/hook",
        "http://192.168.1.10/hook",
    ],
)
def test_validate_url_rejects_non_public(url):
    # Literal IPs and bad schemes need no DNS.
    with pytest.raises(webhooks.InvalidWebhookURL):
        webhooks.validate_url(url)


def test_validate_url_accepts_public_ip(monkeypatch):
    monkeypatch.setattr(
        webhooks.socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))],
    )
    webhooks.validate_url("https://hooks.example.com/ai")


def test_sign_is_hmac_sha256_prefixed():
    sig = webhooks.sign("secret", b"body")
    assert sig.startswith("sha256=")
    assert webhooks.sign("secret", b"body") == sig
    assert webhooks.sign("other", b"body") != sig


def test_deliver_filters_by_subscription(client, monkeypatch):
    settings = get_settings()
    calls = []
    monkeypatch.setattr(webhooks, "validate_url", lambda url: None)
    monkeypatch.setattr(
        webhooks, "_post", lambda url, secret, body, event: calls.append((url, event))
    )

    company_id = db.insert_company(settings.database_path, slug="hooksco", name="Hooks Co")
    db.create_company_webhook(
        settings.database_path, company_id=company_id, url="https://a.example/1", secret="s1",
        event_types="content.approved",
    )
    db.create_company_webhook(
        settings.database_path, company_id=company_id, url="https://a.example/2", secret="s2",
        event_types="all",
    )

    assert webhooks.deliver(settings, company_id, "content.approved", {"x": 1}) == 2
    assert webhooks.deliver(settings, company_id, "content.rejected", {"x": 1}) == 1
    assert {c[0] for c in calls} == {"https://a.example/1", "https://a.example/2"}


def test_deliver_never_raises_on_transport_error(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(webhooks, "validate_url", lambda url: None)
    monkeypatch.setattr(webhooks, "_post", _boom)

    company_id = db.insert_company(settings.database_path, slug="hooksco2", name="Hooks Co 2")
    db.create_company_webhook(
        settings.database_path, company_id=company_id, url="https://a.example/x", secret="s",
    )
    assert webhooks.deliver(settings, company_id, "content.approved", {}) == 0


def _boom(*a, **k):
    raise OSError("connection refused")


# --- endpoints -------------------------------------------------------


def _hermetic_validate(url):
    """Stand-in for `webhooks.validate_url` in endpoint tests: same
    accept/reject shape, no DNS. Real resolution logic is covered by the
    unit tests above."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise webhooks.InvalidWebhookURL("Webhook URL must be an absolute http(s) URL.")
    host = parsed.hostname
    if host in ("localhost", "127.0.0.1", "::1") or host.startswith(("10.", "192.168.")):
        raise webhooks.InvalidWebhookURL(f"non-public host {host!r}")


@pytest.fixture
def hermetic_url_guard(monkeypatch):
    monkeypatch.setattr(webhooks, "validate_url", _hermetic_validate)


def test_webhooks_crud_requires_owner(client, signup, hermetic_url_guard):
    signup(client)
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})
    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    assert client.get("/webhooks").status_code == 403
    assert client.post("/webhooks", json={"url": "https://x.example/h"}).status_code == 403


def test_webhook_create_list_delete(client, signup, hermetic_url_guard):
    signup(client)

    listed = client.get("/webhooks").json()
    assert listed["webhooks"] == []
    assert "content.published" in listed["supported_events"]

    assert client.post("/webhooks", json={"url": "http://localhost/h"}).status_code == 400
    assert client.post(
        "/webhooks", json={"url": "https://x.example/h", "event_types": ["content.exploded"]}
    ).status_code == 400

    created = client.post(
        "/webhooks",
        json={"url": "https://x.example/h", "event_types": ["content.approved", "content.published"]},
    )
    assert created.status_code == 200
    payload = created.json()
    assert payload["secret"].startswith("whsec_")  # revealed once
    assert payload["event_types"] == "content.approved,content.published"
    hook_id = payload["id"]

    relisted = client.get("/webhooks").json()["webhooks"]
    assert len(relisted) == 1
    assert relisted[0]["secret"] is None  # never shown again

    assert client.delete(f"/webhooks/{hook_id}").status_code == 200
    assert client.delete(f"/webhooks/{hook_id}").status_code == 404


def test_approval_fires_webhook(client, signup, monkeypatch):
    events = []
    monkeypatch.setattr(
        webhooks, "deliver",
        lambda settings, company_id, event_type, data: (events.append(event_type), 1)[1],
    )
    signup(client)
    date = client.post("/daily-content/run").json()["date"]

    client.post(f"/daily-content/{date}/trend/1/submit-for-review", json={})
    client.post(f"/daily-content/{date}/trend/1/approve", json={})

    assert "content.submitted_for_review" in events
    assert "content.approved" in events
