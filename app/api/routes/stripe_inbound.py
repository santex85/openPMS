"""Inbound Stripe webhooks (public POST; no JWT).

Minimal reconciliation for events initiated outside the synchronous payment API:
- ``charge.refunded``: reconcile the stripe_charges row + post a negative folio line.
- ``charge.dispute.created``: audit + Sentry alert (status left unchanged).

Any other event, or a PaymentIntent we don't know, returns 200 so Stripe does not retry.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from app.core.config import get_settings
from app.core.sentry import capture_message_with_tags
from app.db.rls_session import tenant_transaction_session
from app.services.audit_service import record_audit
from app.services.stripe_payment_service import reconcile_refund_from_webhook

router = APIRouter()
log = structlog.get_logger()

_HANDLED_EVENTS = frozenset({"charge.refunded", "charge.dispute.created"})


def _cents_to_money(cents: object) -> Decimal | None:
    try:
        return (Decimal(int(cents)) / Decimal(100)).quantize(Decimal("0.01"))
    except (TypeError, ValueError):
        return None


@router.post("/stripe")
async def inbound_stripe_webhook(request: Request) -> dict[str, str]:
    settings = get_settings()
    secret = (settings.stripe_webhook_secret or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Stripe webhooks are not configured",
        )

    body = await request.body()
    sig = request.headers.get("Stripe-Signature") or request.headers.get(
        "stripe-signature",
    )
    if not sig:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing signature",
        )

    import stripe

    try:
        event = stripe.Webhook.construct_event(body, sig, secret)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        ) from exc
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        ) from exc

    event_type = str(event.get("type") or "")
    if event_type not in _HANDLED_EVENTS:
        return {"status": "ignored"}

    obj = (event.get("data") or {}).get("object") or {}
    pi_id = str(obj.get("payment_intent") or "").strip()
    if not pi_id:
        return {"status": "ignored"}

    factory = request.app.state.async_session_factory
    async with factory() as session:
        async with session.begin():
            res = await session.execute(
                text(
                    "SELECT tenant_id, charge_id FROM "
                    "lookup_stripe_charge_for_webhook(:pi) LIMIT 1",
                ),
                {"pi": pi_id},
            )
            row = res.first()
    if row is None:
        log.info("stripe_webhook_unknown_charge", event_type=event_type, pi=pi_id)
        return {"status": "ignored"}
    tenant_id, charge_id = row[0], row[1]

    async with tenant_transaction_session(factory, tenant_id) as session:
        if event_type == "charge.refunded":
            amount_refunded = _cents_to_money(obj.get("amount_refunded"))
            changed = await reconcile_refund_from_webhook(
                session,
                tenant_id,
                charge_id,
                amount_refunded,
            )
            if changed:
                await record_audit(
                    session,
                    tenant_id=tenant_id,
                    action="stripe.refund_reconciled",
                    entity_type="stripe_charge",
                    entity_id=charge_id,
                    new_values={"amount_refunded": str(amount_refunded or "")},
                )
                log.info(
                    "stripe_webhook_refund_reconciled",
                    tenant_id=str(tenant_id),
                    charge_id=str(charge_id),
                )
        else:  # charge.dispute.created
            dispute_id = str(obj.get("id") or "")
            await record_audit(
                session,
                tenant_id=tenant_id,
                action="stripe.dispute_created",
                entity_type="stripe_charge",
                entity_id=charge_id,
                new_values={"dispute_id": dispute_id, "payment_intent": pi_id},
            )
            log.warning(
                "stripe_webhook_dispute_created",
                tenant_id=str(tenant_id),
                charge_id=str(charge_id),
                dispute_id=dispute_id,
            )
            capture_message_with_tags(
                "Stripe dispute created",
                level="warning",
                tags={"tenant_id": str(tenant_id), "charge_id": str(charge_id)},
            )

    return {"status": "ok"}
