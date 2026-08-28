from types import SimpleNamespace

import pytest

from autonomous_intelligence import billing
from autonomous_intelligence.config import Settings


def _settings(**overrides) -> Settings:
    defaults = {
        "ai_provider": "demo",
        "stripe_secret_key": None,
        "stripe_publishable_key": None,
        "stripe_webhook_secret": None,
        "stripe_price_id_starter": None,
        "stripe_price_id_pro": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_available_plans_excludes_plans_without_a_price_id():
    settings = _settings(stripe_price_id_starter="price_starter_123")
    plans = billing.available_plans(settings)

    assert [p["id"] for p in plans] == ["starter"]
    assert plans[0]["name"] == "Starter"


def test_available_plans_empty_when_unconfigured():
    assert billing.available_plans(_settings()) == []


def test_create_checkout_session_raises_when_stripe_unconfigured():
    with pytest.raises(billing.BillingUnavailableError):
        billing.create_checkout_session(
            _settings(),
            company_id=1,
            plan="starter",
            customer_email="owner@acme.test",
            stripe_customer_id=None,
        )


def test_create_checkout_session_raises_on_unknown_plan():
    settings = _settings(stripe_secret_key="sk_test_123", stripe_price_id_starter="price_123")
    with pytest.raises(billing.UnknownPlanError):
        billing.create_checkout_session(
            settings,
            company_id=1,
            plan="enterprise",
            customer_email="owner@acme.test",
            stripe_customer_id=None,
        )


def test_create_checkout_session_raises_when_plan_has_no_price_id():
    settings = _settings(stripe_secret_key="sk_test_123")
    with pytest.raises(billing.UnknownPlanError):
        billing.create_checkout_session(
            settings,
            company_id=1,
            plan="starter",
            customer_email="owner@acme.test",
            stripe_customer_id=None,
        )


def test_create_checkout_session_returns_url(monkeypatch):
    settings = _settings(
        stripe_secret_key="sk_test_123",
        stripe_price_id_starter="price_starter_123",
        app_base_url="https://app.example.com",
    )
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.com/session-abc")

    monkeypatch.setattr(billing.stripe.checkout.Session, "create", fake_create)

    url = billing.create_checkout_session(
        settings,
        company_id=42,
        plan="starter",
        customer_email="owner@acme.test",
        stripe_customer_id=None,
    )

    assert url == "https://checkout.stripe.com/session-abc"
    assert captured["api_key"] == "sk_test_123"
    assert captured["line_items"] == [{"price": "price_starter_123", "quantity": 1}]
    assert captured["client_reference_id"] == "42"
    assert captured["customer_email"] == "owner@acme.test"
    assert captured["success_url"] == "https://app.example.com/?billing=success"


def test_create_checkout_session_uses_existing_customer_id(monkeypatch):
    settings = _settings(stripe_secret_key="sk_test_123", stripe_price_id_starter="price_123")
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.com/session-abc")

    monkeypatch.setattr(billing.stripe.checkout.Session, "create", fake_create)

    billing.create_checkout_session(
        settings,
        company_id=1,
        plan="starter",
        customer_email="owner@acme.test",
        stripe_customer_id="cus_existing",
    )

    assert captured["customer"] == "cus_existing"
    assert captured["customer_email"] is None


def test_create_portal_session_raises_when_unconfigured():
    with pytest.raises(billing.BillingUnavailableError):
        billing.create_portal_session(_settings(), stripe_customer_id="cus_123")


def test_create_portal_session_returns_url(monkeypatch):
    settings = _settings(stripe_secret_key="sk_test_123", app_base_url="https://app.example.com")
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://billing.stripe.com/portal-abc")

    monkeypatch.setattr(billing.stripe.billing_portal.Session, "create", fake_create)

    url = billing.create_portal_session(settings, stripe_customer_id="cus_123")

    assert url == "https://billing.stripe.com/portal-abc"
    assert captured["customer"] == "cus_123"
    assert captured["return_url"] == "https://app.example.com/"


def test_verify_webhook_event_raises_when_unconfigured():
    with pytest.raises(billing.BillingUnavailableError):
        billing.verify_webhook_event(_settings(), payload=b"{}", signature_header="t=1,v1=abc")


def test_verify_webhook_event_delegates_to_stripe(monkeypatch):
    settings = _settings(stripe_webhook_secret="whsec_123")
    captured = {}

    def fake_construct_event(payload, sig_header, secret):
        captured.update(payload=payload, sig_header=sig_header, secret=secret)
        return SimpleNamespace(type="checkout.session.completed")

    monkeypatch.setattr(billing.stripe.Webhook, "construct_event", fake_construct_event)

    event = billing.verify_webhook_event(settings, payload=b"raw-body", signature_header="t=1,v1=abc")

    assert event.type == "checkout.session.completed"
    assert captured == {"payload": b"raw-body", "sig_header": "t=1,v1=abc", "secret": "whsec_123"}
