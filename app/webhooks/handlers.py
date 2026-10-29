"""Conversion d'un webhook PayPal brut en TransactionEvent."""
from datetime import datetime, timezone
from typing import Any

from app.models import TransactionEvent


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_paypal_event(payload: dict[str, Any]) -> TransactionEvent:
    event_type = payload.get("event_type", "UNKNOWN")
    res = payload.get("resource") or {}

    amount, currency = 0.0, "EUR"
    tx_id = res.get("id") or payload.get("id") or "UNKNOWN"
    buyer_email = buyer_country = seller_email = None

    if event_type.startswith(("PAYMENT.CAPTURE", "PAYMENT.AUTHORIZATION")):
        amt = res.get("amount") or {}
        amount, currency = _f(amt.get("value")), amt.get("currency_code", "EUR")
        payer = res.get("payer") or {}
        buyer_email = payer.get("email_address")
    elif event_type.startswith("CHECKOUT.ORDER"):
        pu = (res.get("purchase_units") or [{}])[0]
        amt = pu.get("amount") or {}
        amount, currency = _f(amt.get("value")), amt.get("currency_code", "EUR")
        payer = res.get("payer") or {}
        buyer_email = payer.get("email_address")
        buyer_country = (payer.get("address") or {}).get("country_code")
    elif event_type.startswith("CUSTOMER.DISPUTE"):
        amt = res.get("dispute_amount") or {}
        amount, currency = _f(amt.get("value")), amt.get("currency_code", "EUR")
        disputed = (res.get("disputed_transactions") or [{}])[0]
        tx_id = disputed.get("seller_transaction_id") or tx_id
        buyer_email = (disputed.get("buyer") or {}).get("name")
        seller_email = (disputed.get("seller") or {}).get("email")
    elif event_type.startswith("INVOICING.INVOICE"):
        inv = res.get("invoice") or res
        amt = inv.get("amount") or {}
        amount = _f(amt.get("value"))
        currency = amt.get("currency_code") or amt.get("currency", "EUR")
        tx_id = inv.get("id") or tx_id
        seller_email = (inv.get("invoicer") or {}).get("email_address")
        buyer_email = ((inv.get("primary_recipients") or [{}])[0].get("billing_info") or {}).get(
            "email_address"
        )

    # Les signaux de risque (recent_micro_payments, tracking_delivered, ...) ne viennent pas
    # de PayPal : on les accepte via un bloc optionnel "sentinel_signals" (démo / enrichissement).
    raw: dict[str, Any] = {"paypal_resource": res}
    raw.update(payload.get("sentinel_signals") or {})
    if event_type.startswith("CUSTOMER.DISPUTE") and "dispute_reason" not in raw:
        raw["dispute_reason"] = res.get("reason")

    return TransactionEvent(
        event_id=payload.get("id") or f"EVT_{tx_id}",
        event_type=event_type,
        transaction_id=str(tx_id),
        amount=amount,
        currency=currency,
        buyer_email=buyer_email,
        buyer_country=buyer_country,
        seller_email=seller_email,
        timestamp=datetime.now(timezone.utc),
        raw_payload=raw,
    )
