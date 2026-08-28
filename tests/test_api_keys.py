"""Covers `api_keys.py`, the `/api-keys` router, and the API-key branch of
`auth.get_current_user_optional`.

The security-relevant claims are the point of this file: a key authenticates,
is shown once, acts as `member` (never `owner`), and stops working the
instant it is revoked.
"""

from autonomous_intelligence import api_keys, db
from autonomous_intelligence.config import get_settings


def _issue_key(client, name="CI pipeline") -> str:
    created = client.post("/api-keys", json={"name": name})
    assert created.status_code == 200, created.text
    return created.json()["key"]


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


# --- key generation -----------------------------------------------------


def test_generate_key_is_prefixed_and_hashes_consistently():
    full, prefix, key_hash = api_keys.generate_key()
    assert full.startswith(api_keys.KEY_PREFIX)
    assert full.startswith(prefix)
    assert api_keys.hash_key(full) == key_hash
    assert full not in key_hash  # the raw key must not be recoverable from the hash


def test_generated_keys_are_unique():
    assert len({api_keys.generate_key()[0] for _ in range(50)}) == 50


def test_looks_like_api_key_only_matches_our_prefix():
    assert api_keys.looks_like_api_key(api_keys.generate_key()[0])
    assert not api_keys.looks_like_api_key("eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert not api_keys.looks_like_api_key("")


# --- router -------------------------------------------------------------


def test_api_keys_require_owner(client, signup):
    signup(client)
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})
    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    assert client.get("/api-keys").status_code == 403
    assert client.post("/api-keys", json={"name": "x"}).status_code == 403


def test_key_is_revealed_once_then_never_again(client, signup):
    signup(client)
    key = _issue_key(client)
    assert key.startswith(api_keys.KEY_PREFIX)

    listed = client.get("/api-keys").json()
    assert listed["keys"][0]["key"] is None
    assert listed["keys"][0]["prefix"] and key.startswith(listed["keys"][0]["prefix"])
    assert "Authorization: Bearer" in listed["usage"]


def test_only_the_hash_is_persisted(client, signup):
    signup(client)
    key = _issue_key(client)
    row = db.list_api_keys(get_settings().database_path, 1)[0]
    assert row["key_hash"] == api_keys.hash_key(key)
    assert key not in dict(row).values()


# --- authentication -----------------------------------------------------


def test_key_authenticates_a_cookieless_client(client, signup, api_client):
    signup(client)
    key = _issue_key(client)

    assert api_client.get("/library").status_code == 401
    assert api_client.get("/library", headers=_bearer(key)).status_code == 200
    assert api_client.get("/usage", headers=_bearer(key)).status_code == 200


def test_key_acts_as_member_not_owner(client, signup, api_client):
    """The escalation guard: a leaked key must not be able to reshape the
    account it belongs to."""
    signup(client)
    key = _issue_key(client)
    headers = _bearer(key)

    assert api_client.get("/api-keys", headers=headers).status_code == 403
    assert api_client.post("/billing/checkout", json={"plan": "pro"}, headers=headers).status_code == 403
    assert api_client.post("/webhooks", json={"url": "https://x.example/h"}, headers=headers).status_code == 403
    assert api_client.post("/auth/invite", json={"email": "x@y.test", "password": "hunter22"}, headers=headers).status_code == 403


def test_bad_and_foreign_bearer_tokens_are_rejected(client, signup, api_client):
    signup(client)
    _issue_key(client)

    assert api_client.get("/library", headers=_bearer("ai_live_not-a-real-key")).status_code == 401
    assert api_client.get("/library", headers=_bearer("eyJ.some.jwt")).status_code == 401
    assert api_client.get("/library", headers={"Authorization": "Basic abc"}).status_code == 401


def test_key_is_scoped_to_its_own_company(client, signup, api_client):
    signup(client)
    key = _issue_key(client)
    client.post("/custom-content/run", json={"topic": "company one topic"})

    client.cookies.clear()
    signup(client, company_name="Other Co", email="other@other.test")
    client.post("/custom-content/run", json={"topic": "company two topic"})

    seen = api_client.get("/library", headers=_bearer(key)).json()
    assert len(seen) == 1
    assert "company one" in seen[0]["title"].lower()


def test_last_used_at_is_recorded(client, signup, api_client):
    signup(client)
    key = _issue_key(client)
    assert client.get("/api-keys").json()["keys"][0]["last_used_at"] is None

    api_client.get("/library", headers=_bearer(key))
    assert client.get("/api-keys").json()["keys"][0]["last_used_at"] is not None


def test_revocation_takes_effect_immediately(client, signup, api_client):
    signup(client)
    key = _issue_key(client)
    key_id = client.get("/api-keys").json()["keys"][0]["id"]

    assert api_client.get("/library", headers=_bearer(key)).status_code == 200
    assert client.delete(f"/api-keys/{key_id}").status_code == 200
    assert api_client.get("/library", headers=_bearer(key)).status_code == 401

    # The row is kept (audit) but shown as revoked, and cannot be revoked twice.
    listed = client.get("/api-keys").json()["keys"]
    assert listed[0]["revoked"] is True
    assert client.delete(f"/api-keys/{key_id}").status_code == 404
