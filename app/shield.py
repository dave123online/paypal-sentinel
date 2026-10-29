"""Shield : règles déterministes. Gemini ne fait qu'expliquer."""
from datetime import datetime, timezone
from app import ai_engine
from app.models import MerchantState, ShieldVerdict, TransactionEvent

def _decide(event: TransactionEvent) -> dict:
    p = event.raw_payload or {}
    
    # Paiement refusé par PayPal (ex. negative testing INSTRUMENT_DECLINED du sandbox)
    if event.event_type == "PAYMENT.CAPTURE.DENIED":
        issue = (p.get("paypal_error") or {}).get("issue") or "UNKNOWN"
        return {
            "threat_level": "medium", "risk_score": 55, "action_taken": "alerted",
            "confidence": 0.9, "reason": f"PayPal declined this payment ({issue}).",
        }

    # Scénario 3 — Invoice anomaly (priorité la plus haute)
    avg = p.get("average_invoice_amount")
    if avg and event.amount > float(avg) * 2.5 and p.get("new_payee") is True:
        return {
            "threat_level": "critical", "risk_score": 92, "action_taken": "held",
            "confidence": 0.95, "reason": "Invoice amount is significantly higher than usual and payee changed.",
        }
    # Scénario 2 — Friendly fraud
    if (event.event_type == "CUSTOMER.DISPUTE.CREATED"
        and p.get("tracking_delivered") is True
        and p.get("dispute_reason") == "ITEM_NOT_RECEIVED"):
        return {
            "threat_level": "medium", "risk_score": 70, "action_taken": "alerted",
            "confidence": 0.85, "reason": "Dispute claims item not received but tracking shows delivered.",
        }
    # Scénario 1b — Rafale automatisée de tests de carte : on libère les fonds de l'acheteur (void)
    if int(p.get("recent_micro_payments", 0) or 0) >= 15 and event.amount >= 300:
        return {
            "threat_level": "critical", "risk_score": 97, "action_taken": "blocked",
            "confidence": 0.95, "reason": "Automated burst of test payments before a high amount: authorization voided.",
        }
    # Scénario 1 — Velocity attack
    if int(p.get("recent_micro_payments", 0) or 0) >= 6 and event.amount >= 300:
        return {
            "threat_level": "high", "risk_score": 85, "action_taken": "held",
            "confidence": 0.9, "reason": "Several small test payments followed by a high amount transaction.",
        }
    return {
        "threat_level": "low", "risk_score": 10, "action_taken": "approved",
        "confidence": 0.8, "reason": "No suspicious pattern detected.",
    }

async def analyze_transaction(
    event: TransactionEvent, merchant_state: MerchantState
) -> ShieldVerdict:
    decision = _decide(event)

    # ⚡ Si tout va bien, on ne gaspille pas un appel Gemini
    if decision["action_taken"] == "approved":
        return ShieldVerdict(
            transaction_id=event.transaction_id,
            explanation_for_merchant="Transaction approved — no issues detected.",
            **decision,
        )

    # 🔍 Sinon, on demande à Gemini une explication empathique
    explanation = await ai_engine.explain_shield({
        "transaction": {
            "event_type": event.event_type, "amount": event.amount,
            "currency": event.currency,   # pas d'e-mail acheteur : inutile pour expliquer la décision
        },
        "merchant_state": {
            "monthly_revenue": merchant_state.monthly_revenue,
            "fraud_attempts_blocked": merchant_state.fraud_attempts_blocked,
            "money_saved_by_shield": merchant_state.money_saved_by_shield,
        },
        "shield_decision": {
            "threat_level": decision["threat_level"], "risk_score": decision["risk_score"],
            "action_taken": decision["action_taken"], "reason": decision["reason"],
        },
    })

    return ShieldVerdict(
        transaction_id=event.transaction_id,
        explanation_for_merchant=explanation,
        **decision,
    )

# Cette fonction doit être au niveau racine du module (pas indentée)
def update_merchant_state(
    state: MerchantState,
    verdict: ShieldVerdict,
    event: TransactionEvent,
) -> None:
    """Met à jour l'état marchand APRÈS le verdict Shield."""
    action = verdict.action_taken
    if action in ("held", "blocked"):
        state.fraud_attempts_blocked += 1
        state.money_saved_by_shield += event.amount
        state.stress_level = min(10, state.stress_level + 1)
    elif action == "alerted":
        if event.event_type.startswith("CUSTOMER.DISPUTE"):
            state.pending_disputes += 1
        state.stress_level = min(10, state.stress_level + 0.5)
    state.last_activity = datetime.now(timezone.utc)
