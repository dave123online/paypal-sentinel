import asyncio

from app.ally import analyze_history, generate_suggestion
from app.models import MerchantState, TransactionEvent

TYPES = ("revenue_opportunity", "cash_flow_alert", "customer_retention", "operational_improvement")


def test_ally_independent():
    event = TransactionEvent(
        event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="TX", amount=500,
        raw_payload={"recent_micro_payments": 8},
    )
    s = asyncio.run(generate_suggestion(event, MerchantState(merchant_id="t"), [event]))
    assert s.requires_approval is True
    assert s.suggestion_type in TYPES


def test_ally_never_mentions_fraud_in_fallback():
    event = TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="TX", amount=50)
    s = asyncio.run(generate_suggestion(event, MerchantState(merchant_id="t"), []))
    text = f"{s.title} {s.description} {s.action}".lower()
    assert not any(w in text for w in ("fraud", "security", "block"))


def test_unpaid_invoices_trigger_cash_flow():
    event = TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="TX", amount=50)
    state = MerchantState(merchant_id="t", unpaid_invoices_count=3, unpaid_invoices_amount=2400)
    s = asyncio.run(generate_suggestion(event, state, []))
    assert s.suggestion_type == "cash_flow_alert"
    assert s.estimated_impact_eur == 2400


def test_churn_and_upsell_detection():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def tx(i, buyer, amount, days_ago):
        return TransactionEvent(
            event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id=f"T{i}", amount=amount,
            buyer_email=buyer, timestamp=now - timedelta(days=days_ago),
        )

    hist = [tx(i, "bob@x.com", 120, 40 - 3 * i) for i in range(5)]
    hist += [tx(10 + i, "alice@x.com", 45 + 2 * i, 30 - 2 * i) for i in range(10)]
    a = analyze_history(hist)
    assert [c["buyer"] for c in a["churn_candidates"]] == ["bob@x.com"]
    assert [u["buyer"] for u in a["upsell_candidates"]] == ["alice@x.com"]
