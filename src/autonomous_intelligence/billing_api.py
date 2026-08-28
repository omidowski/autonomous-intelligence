from __future__ import annotations

import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import auth, billing, db
from .config import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan: str


def _metadata(obj: object) -> dict:
    """A webhook object's `metadata` as a plain dict. Real Stripe objects
    always carry a (possibly empty) `metadata`; this stays safe if one
    doesn't."""
    return getattr(obj, "metadata", None) or {}


def _first_price_id(obj: object) -> str | None:
    """The Stripe Price id on a subscription object's first line item, or
    `None` if the payload doesn't carry one (some events don't expand
    `items`)."""
    try:
        return obj["items"]["data"][0]["price"]["id"]
    except (KeyError, IndexError, TypeError):
        return None


def _plan_for_price_id(settings: Settings, price_id: str | None) -> str | None:
    """Reverse the `settings.stripe_price_id_<plan>` mapping - lets us
    recover the plan for a `customer.subscription.*` event that carries a
    price but none of our metadata (e.g. edited from the Stripe dashboard)."""
    if not price_id:
        return None
    for plan in billing.PLAN_CHOICES:
        if getattr(settings, f"stripe_price_id_{plan}", None) == price_id:
            return plan
    return None


def _resolve_company_id(settings: Settings, obj: object) -> int:
    """The company a webhook object belongs to. Prefer the `company_id` we
    stamped into metadata at checkout; fall back to a lookup by Stripe
    customer id so events created outside our checkout flow - a plan change
    or cancellation done from the Stripe dashboard, which carry none of our
    metadata - still land on the right company."""
    try:
        company_id = int(_metadata(obj).get("company_id", 0) or 0)
    except (TypeError, ValueError):
        company_id = 0
    if company_id:
        return company_id
    customer = getattr(obj, "customer", None)
    if customer:
        company = db.get_company_by_stripe_customer_id(settings.database_path, customer)
        if company:
            return company["id"]
    return 0


@router.get("/plans")
def list_plans(settings: Settings = Depends(get_settings)) -> list[dict[str, str]]:
    return billing.available_plans(settings)


@router.get("/status")
def billing_status(current: auth.CurrentUser = Depends(auth.get_current_user)) -> dict:
    settings = get_settings()
    company = db.get_company_by_id(settings.database_path, current.company_id)
    return {
        "plan": company["subscription_plan"] if company else "free",
        "status": company["subscription_status"] if company else "active",
        "manageable": bool(company and company["stripe_customer_id"]),
    }


@router.post("/checkout")
def create_checkout(
    body: CheckoutRequest,
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    company = db.get_company_by_id(settings.database_path, current.company_id)
    try:
        url = billing.create_checkout_session(
            settings,
            company_id=current.company_id,
            plan=body.plan,
            customer_email=current.email,
            stripe_customer_id=company["stripe_customer_id"] if company else None,
        )
    except billing.BillingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except billing.UnknownPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url}


@router.post("/portal")
def create_portal(
    current: auth.CurrentUser = Depends(auth.require_owner),
    settings: Settings = Depends(get_settings),
) -> dict:
    if not settings.stripe_secret_key:
        # Distinguish "billing switched off" (503) from "you just haven't
        # upgraded yet" (400) - otherwise a self-hosted deploy with no
        # Stripe keys looks identical to a free user who never paid.
        raise HTTPException(status_code=503, detail="Billing is not configured yet.")
    company = db.get_company_by_id(settings.database_path, current.company_id)
    if not company or not company["stripe_customer_id"]:
        raise HTTPException(status_code=400, detail="No billing account yet - upgrade a plan first.")
    try:
        url = billing.create_portal_session(settings, stripe_customer_id=company["stripe_customer_id"])
    except billing.BillingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"url": url}


@router.post("/webhook")
async def stripe_webhook(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    payload = await request.body()
    signature_header = request.headers.get("stripe-signature", "")
    try:
        event = billing.verify_webhook_event(
            settings, payload=payload, signature_header=signature_header
        )
    except billing.BillingUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook signature.") from exc

    if not db.record_stripe_event(settings.database_path, event.id, event.type):
        # Stripe re-delivers on any non-2xx/timeout; we've already handled
        # this exact event id.
        return {"received": True, "duplicate": True}

    obj = event.data.object

    if event.type == "checkout.session.completed":
        company_id = _resolve_company_id(settings, obj)
        if company_id:
            if obj.customer:
                db.set_stripe_customer_id(settings.database_path, company_id, obj.customer)
            # Activate the plan straight away rather than waiting for the
            # separate `customer.subscription.created` - that event can lag
            # or be dropped, and until it lands the customer has paid but
            # still shows as "free".
            plan = _metadata(obj).get("plan")
            if plan in billing.PLAN_CHOICES:
                db.update_company_subscription(
                    settings.database_path,
                    company_id,
                    plan=plan,
                    status="active",
                    stripe_subscription_id=getattr(obj, "subscription", None),
                )

    elif event.type in ("customer.subscription.updated", "customer.subscription.created"):
        company_id = _resolve_company_id(settings, obj)
        if company_id:
            # Persist the customer id here too, not only on
            # `checkout.session.completed` - Stripe does not guarantee event
            # ordering or delivery, so if that event is delayed or dropped
            # the company would otherwise have an active paid plan but no
            # `stripe_customer_id`, leaving `/billing/portal` permanently
            # unreachable.
            if obj.customer:
                db.set_stripe_customer_id(settings.database_path, company_id, obj.customer)
            # Never let a metadata-less event silently downgrade a paying
            # company to "free": prefer our checkout metadata, then the
            # price id on the subscription, and only then keep whatever
            # plan is already on record. "free" is reached solely via
            # `customer.subscription.deleted` below.
            plan = _metadata(obj).get("plan") or _plan_for_price_id(settings, _first_price_id(obj))
            if plan is None:
                existing = db.get_company_by_id(settings.database_path, company_id)
                plan = existing["subscription_plan"] if existing else "free"
            status = "active" if obj.status in ("active", "trialing") else obj.status
            db.update_company_subscription(
                settings.database_path,
                company_id,
                plan=plan,
                status=status,
                stripe_subscription_id=obj.id,
            )

    elif event.type == "customer.subscription.deleted":
        company_id = _resolve_company_id(settings, obj)
        if company_id:
            db.update_company_subscription(
                settings.database_path, company_id, plan="free", status="canceled"
            )

    else:
        logger.info("Unhandled Stripe webhook event type: %s", event.type)

    return {"received": True}


__all__ = ["router"]
