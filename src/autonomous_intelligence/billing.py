"""Real subscription billing via Stripe - Checkout for upgrading, a
Customer Portal session for managing/canceling, and webhook verification
that keeps `companies.subscription_plan`/`subscription_status` in sync
with what Stripe actually charged (see `billing_api.py`).

Unlike every other external-provider integration in this codebase
(ElevenLabs, Leonardo, Runway, Kling, HeyGen), there is deliberately no
stub/demo fallback here when `settings.stripe_secret_key` is unset:
`BillingUnavailableError` propagates to a 503 instead. Silently
pretending a checkout succeeded would be actively misleading rather than
a harmless placeholder - money is the one place in this app where "fail
loudly" is the safe default, not "degrade gracefully".
"""

from __future__ import annotations

import stripe

from .config import Settings

PLAN_CHOICES: dict[str, dict[str, str]] = {
    "starter": {"name": "Starter", "price_display": "$29/mo"},
    "pro": {"name": "Pro", "price_display": "$99/mo"},
}
"""Plan metadata shown in `/billing/plans` - the actual Stripe Price id
for each comes from `settings.stripe_price_id_<plan>`, not from here, so
this dict alone is safe to display even with no Stripe key configured."""


class BillingUnavailableError(RuntimeError):
    """Stripe isn't configured (`stripe_secret_key` unset) - callers
    (`billing_api.py`) map this to HTTP 503, never to a fake success."""


class UnknownPlanError(ValueError):
    """`plan` isn't a key in `PLAN_CHOICES`, or that plan has no
    configured Stripe Price id."""


def _price_id_for_plan(settings: Settings, plan: str) -> str:
    if plan not in PLAN_CHOICES:
        raise UnknownPlanError(f"Unknown plan: {plan!r}.")
    price_id = getattr(settings, f"stripe_price_id_{plan}", None)
    if not price_id:
        raise UnknownPlanError(f"Plan {plan!r} has no configured Stripe price id.")
    return price_id


def available_plans(settings: Settings) -> list[dict[str, str]]:
    """Plans with a configured Stripe price id - a plan without one is
    left out rather than shown with a checkout button that would 503."""
    plans = []
    for key, meta in PLAN_CHOICES.items():
        if getattr(settings, f"stripe_price_id_{key}", None):
            plans.append({"id": key, **meta})
    return plans


def create_checkout_session(
    settings: Settings,
    *,
    company_id: int,
    plan: str,
    customer_email: str,
    stripe_customer_id: str | None,
) -> str:
    """Returns a Stripe-hosted Checkout URL for upgrading `company_id` to
    `plan`. Raises `BillingUnavailableError` if Stripe isn't configured,
    `UnknownPlanError` if `plan` is invalid/unconfigured."""
    if not settings.stripe_secret_key:
        raise BillingUnavailableError("Billing is not configured yet.")
    price_id = _price_id_for_plan(settings, plan)

    session = stripe.checkout.Session.create(
        api_key=settings.stripe_secret_key,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{settings.app_base_url}/?billing=success",
        cancel_url=f"{settings.app_base_url}/?billing=cancelled",
        client_reference_id=str(company_id),
        customer=stripe_customer_id,
        customer_email=customer_email if not stripe_customer_id else None,
        metadata={"company_id": str(company_id), "plan": plan},
        subscription_data={"metadata": {"company_id": str(company_id), "plan": plan}},
    )
    return session.url


def create_portal_session(settings: Settings, *, stripe_customer_id: str) -> str:
    """Returns a Stripe-hosted Customer Portal URL for managing/canceling
    the subscription. Raises `BillingUnavailableError` if Stripe isn't
    configured."""
    if not settings.stripe_secret_key:
        raise BillingUnavailableError("Billing is not configured yet.")
    session = stripe.billing_portal.Session.create(
        api_key=settings.stripe_secret_key,
        customer=stripe_customer_id,
        return_url=f"{settings.app_base_url}/",
    )
    return session.url


def verify_webhook_event(settings: Settings, *, payload: bytes, signature_header: str) -> stripe.Event:
    """Verifies `payload` was actually sent by Stripe (HMAC signature
    check against `stripe_webhook_secret`) and parses it into an Event.
    Raises `BillingUnavailableError` if no webhook secret is configured,
    `stripe.error.SignatureVerificationError` if the signature is
    invalid/forged."""
    if not settings.stripe_webhook_secret:
        raise BillingUnavailableError("Billing webhook is not configured yet.")
    return stripe.Webhook.construct_event(payload, signature_header, settings.stripe_webhook_secret)


__all__ = [
    "PLAN_CHOICES",
    "BillingUnavailableError",
    "UnknownPlanError",
    "available_plans",
    "create_checkout_session",
    "create_portal_session",
    "verify_webhook_event",
]
