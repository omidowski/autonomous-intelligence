"""Covers `billing_api.py` - the Stripe-backed `/billing/*` router.

The `client` fixture builds the app with no Stripe env, so tests that need
a configured Stripe set the env vars themselves and call
`get_settings.cache_clear()` (the router reads settings per-request via
`Depends`, so no app rebuild is needed). Stripe's network calls are either
monkeypatched (`checkout`) or exercised through real HMAC signing that
`stripe.Webhook.construct_event` verifies locally (`webhook`).
"""

import hashlib
import hmac
import json
import time

import stripe

from autonomous_intelligence import db
from autonomous_intelligence.config import get_settings

WEBHOOK_SECRET = "whsec_testsecret"


def _enable_stripe(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_ID_STARTER", "price_starter_fake")
    monkeypatch.setenv("STRIPE_PRICE_ID_PRO", "price_pro_fake")
    monkeypatch.setenv("APP_BASE_URL", "https://app.example.test")
    get_settings.cache_clear()


def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    ts = int(time.time())
    signature = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={signature}"


def _post_event(client, event: dict, *, secret: str = WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    return client.post(
        "/billing/webhook",
        content=body,
        headers={"stripe-signature": _sign(body, secret), "content-type": "application/json"},
    )


_event_seq = 0


def _subscription_event(
    event_type: str, *, event_id: str | None = None, obj_overrides: dict | None = None
) -> dict:
    global _event_seq
    _event_seq += 1
    obj = {
        "id": "sub_test123",
        "status": "active",
        "customer": "cus_test123",
        "metadata": {"company_id": "1", "plan": "pro"},
    }
    obj.update(obj_overrides or {})
    return {
        "id": event_id or f"evt_test_{_event_seq}",
        "type": event_type,
        "data": {"object": obj},
    }


# --- unconfigured (no Stripe keys) -----------------------------------------


def test_plans_is_empty_when_stripe_unconfigured(client):
    assert client.get("/billing/plans").json() == []


def test_status_defaults_to_free_plan(client, signup):
    signup(client)
    assert client.get("/billing/status").json() == {
        "plan": "free",
        "status": "active",
        "manageable": False,
    }


def test_checkout_requires_authentication(client):
    assert client.post("/billing/checkout", json={"plan": "pro"}).status_code == 401


def test_checkout_requires_owner_role(client, signup):
    signup(client)
    client.post("/auth/invite", json={"email": "member@acme.test", "password": "hunter22"})
    client.cookies.clear()
    client.post("/auth/login", json={"email": "member@acme.test", "password": "hunter22"})

    assert client.post("/billing/checkout", json={"plan": "pro"}).status_code == 403


def test_checkout_returns_503_when_stripe_unconfigured(client, signup):
    signup(client)
    r = client.post("/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 503


def test_portal_returns_503_when_stripe_unconfigured(client, signup):
    """Regression: previously returned 400 ("upgrade a plan first") because
    the missing-customer check ran before the missing-config check, making
    a keyless deploy indistinguishable from a free user."""
    signup(client)
    r = client.post("/billing/portal")
    assert r.status_code == 503


def test_webhook_returns_503_when_stripe_unconfigured(client):
    assert client.post("/billing/webhook", content=b"{}").status_code == 503


# --- configured Stripe ----------------------------------------------------


def test_plans_lists_configured_plans(client, monkeypatch):
    _enable_stripe(monkeypatch)
    plans = client.get("/billing/plans").json()
    assert {p["id"] for p in plans} == {"starter", "pro"}


def test_checkout_creates_session_with_expected_stripe_args(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)

    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return type("S", (), {"url": "https://checkout.stripe.com/c/pay/cs_test_123"})()

    monkeypatch.setattr(stripe.checkout.Session, "create", staticmethod(fake_create))

    r = client.post("/billing/checkout", json={"plan": "pro"})
    assert r.status_code == 200
    assert r.json() == {"url": "https://checkout.stripe.com/c/pay/cs_test_123"}

    assert captured["line_items"] == [{"price": "price_pro_fake", "quantity": 1}]
    assert captured["client_reference_id"] == "1"
    assert captured["customer_email"] == "owner@acme.test"
    assert captured["success_url"] == "https://app.example.test/?billing=success"
    assert captured["cancel_url"] == "https://app.example.test/?billing=cancelled"
    assert captured["metadata"] == {"company_id": "1", "plan": "pro"}
    assert captured["subscription_data"] == {"metadata": {"company_id": "1", "plan": "pro"}}


def test_checkout_rejects_unknown_plan(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)
    r = client.post("/billing/checkout", json={"plan": "enterprise"})
    assert r.status_code == 400


def test_portal_returns_400_before_any_upgrade(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)
    r = client.post("/billing/portal")
    assert r.status_code == 400


# --- webhook ------------------------------------------------------------


def test_webhook_rejects_bad_signature(client, monkeypatch):
    _enable_stripe(monkeypatch)
    body = json.dumps(_subscription_event("customer.subscription.created")).encode()
    r = client.post(
        "/billing/webhook",
        content=body,
        headers={"stripe-signature": "t=1,v1=deadbeef", "content-type": "application/json"},
    )
    assert r.status_code == 400


def test_webhook_subscription_created_syncs_plan_and_customer_id(client, signup, monkeypatch):
    """Core regression: a `customer.subscription.created` with no preceding
    `checkout.session.completed` must still persist `stripe_customer_id`
    (Stripe does not guarantee event ordering/delivery), otherwise
    `/billing/portal` stays permanently unreachable for a paying company."""
    _enable_stripe(monkeypatch)
    signup(client)

    r = _post_event(client, _subscription_event("customer.subscription.created"))
    assert r.status_code == 200

    status = client.get("/billing/status").json()
    assert status == {"plan": "pro", "status": "active", "manageable": True}

    company = db.get_company_by_id(get_settings().database_path, 1)
    assert company["stripe_subscription_id"] == "sub_test123"
    assert company["stripe_customer_id"] == "cus_test123"


def test_webhook_maps_incomplete_subscription_status_to_itself(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)

    _post_event(
        client,
        _subscription_event(
            "customer.subscription.updated", obj_overrides={"status": "past_due"}
        ),
    )
    assert client.get("/billing/status").json()["status"] == "past_due"


def test_webhook_recovers_plan_from_price_id_when_metadata_absent(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)

    # Establish the customer id first (normal `.created` with metadata)...
    _post_event(client, _subscription_event("customer.subscription.created"))  # -> pro
    assert client.get("/billing/status").json()["plan"] == "pro"

    # ...then a dashboard-style downgrade: no metadata, company resolved by
    # customer id, plan recovered from the price on the line item.
    r = _post_event(
        client,
        _subscription_event(
            "customer.subscription.updated",
            obj_overrides={
                "metadata": {},
                "items": {"data": [{"price": {"id": "price_starter_fake"}}]},
            },
        ),
    )
    assert r.status_code == 200
    assert client.get("/billing/status").json()["plan"] == "starter"


def test_webhook_does_not_downgrade_plan_when_nothing_identifies_it(client, signup, monkeypatch):
    """A metadata-less, price-less `customer.subscription.updated` (e.g. a
    trial-end status change) must not clobber an existing paid plan to
    "free"."""
    _enable_stripe(monkeypatch)
    signup(client)
    _post_event(client, _subscription_event("customer.subscription.created"))  # -> pro

    r = _post_event(
        client,
        _subscription_event(
            "customer.subscription.updated",
            obj_overrides={"metadata": {}, "status": "past_due"},
        ),
    )
    assert r.status_code == 200
    status = client.get("/billing/status").json()
    assert status["plan"] == "pro"
    assert status["status"] == "past_due"


def test_webhook_resolves_company_by_customer_id_when_metadata_absent(client, signup, monkeypatch):
    """Dashboard-initiated events carry none of our metadata - the handler
    must fall back to looking the company up by Stripe customer id."""
    _enable_stripe(monkeypatch)
    signup(client)

    # First a normal event so `stripe_customer_id` is on record.
    _post_event(client, _subscription_event("customer.subscription.created"))

    # Then a cancellation with an empty metadata object, as Stripe sends for
    # a subscription deleted from the dashboard.
    r = _post_event(
        client,
        _subscription_event("customer.subscription.deleted", obj_overrides={"metadata": {}}),
    )
    assert r.status_code == 200

    assert client.get("/billing/status").json() == {
        "plan": "free",
        "status": "canceled",
        "manageable": True,
    }


def test_webhook_ignores_unhandled_event_types(client, monkeypatch):
    _enable_stripe(monkeypatch)
    r = _post_event(client, {"id": "evt_x", "type": "invoice.paid", "data": {"object": {}}})
    assert r.status_code == 200
    assert r.json() == {"received": True}


def test_checkout_completed_activates_plan_immediately(client, signup, monkeypatch):
    """`checkout.session.completed` alone (no `customer.subscription.created`
    yet) must already flip the company to the paid plan."""
    _enable_stripe(monkeypatch)
    signup(client)

    event = {
        "id": "evt_checkout",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "customer": "cus_test123",
                "subscription": "sub_test123",
                "metadata": {"company_id": "1", "plan": "pro"},
            }
        },
    }
    r = _post_event(client, event)
    assert r.status_code == 200

    assert client.get("/billing/status").json() == {
        "plan": "pro",
        "status": "active",
        "manageable": True,
    }


def test_webhook_is_idempotent_on_repeated_event_id(client, signup, monkeypatch):
    _enable_stripe(monkeypatch)
    signup(client)

    event = _subscription_event("customer.subscription.created", event_id="evt_dupe")  # -> pro
    assert _post_event(client, event).json() == {"received": True}
    assert client.get("/billing/status").json()["plan"] == "pro"

    # Same event id, contradictory payload: must be ignored, not re-applied.
    replay = _subscription_event(
        "customer.subscription.created",
        event_id="evt_dupe",
        obj_overrides={"metadata": {"company_id": "1", "plan": "starter"}},
    )
    r = _post_event(client, replay)
    assert r.json() == {"received": True, "duplicate": True}
    assert client.get("/billing/status").json()["plan"] == "pro"
