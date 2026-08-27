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

    obj = event.data.object

    if event.type == "checkout.session.completed":
        company_id = int(obj.metadata.get("company_id", 0) or 0)
        if company_id and obj.customer:
            db.set_stripe_customer_id(settings.database_path, company_id, obj.customer)

    elif event.type in ("customer.subscription.updated", "customer.subscription.created"):
        company_id = int(obj.metadata.get("company_id", 0) or 0)
        plan = obj.metadata.get("plan", "free")
        if company_id:
            status = "active" if obj.status in ("active", "trialing") else obj.status
            db.update_company_subscription(
                settings.database_path,
                company_id,
                plan=plan,
                status=status,
                stripe_subscription_id=obj.id,
            )

    elif event.type == "customer.subscription.deleted":
        company_id = int(obj.metadata.get("company_id", 0) or 0)
        if company_id:
            db.update_company_subscription(
                settings.database_path, company_id, plan="free", status="canceled"
            )

    else:
        logger.info("Unhandled Stripe webhook event type: %s", event.type)

    return {"received": True}


__all__ = ["router"]
