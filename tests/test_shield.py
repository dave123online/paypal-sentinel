import asyncio
import json
from pathlib import Path

from app.models import MerchantState, TransactionEvent
from app.shield import analyze_transaction

SCENARIOS = Path(__file__).parent / "scenarios"


def _run(name: str):
    event = TransactionEvent(**json.loads((SCENARIOS / name).read_text()))
    return asyncio.run(analyze_transaction(event, MerchantState(merchant_id="t")))


def test_velocity():
    v = _run("velocity_attack.json")
    assert (v.threat_level, v.risk_score, v.action_taken) == ("high", 85, "held")


def test_friendly_fraud():
    v = _run("friendly_fraud.json")
    assert (v.threat_level, v.risk_score, v.action_taken) == ("medium", 70, "alerted")


def test_invoice_anomaly():
    v = _run("invoice_anomaly.json")
    assert (v.threat_level, v.risk_score, v.action_taken) == ("critical", 92, "held")


def test_clean_transaction():
    event = TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="OK", amount=20)
    v = asyncio.run(analyze_transaction(event, MerchantState(merchant_id="t")))
    assert v.action_taken == "approved"
