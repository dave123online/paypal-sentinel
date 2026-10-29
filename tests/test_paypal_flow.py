"""Flux PayPal Sandbox avec un faux serveur PayPal (pas d'appel réseau)."""
import pytest
from fastapi.testclient import TestClient

from app import main as m
from app import paypal_client as pc


def _reset():
    for d in (m.merchant_states, m.pending_orders, m.paypal_refs):
        d.clear()
    for l in (m.shield_verdicts, m.ally_suggestions, m.transaction_history):
        l.clear()
    for st in (m.seen_ally_titles, m.seen_ally_actions, m.known_capture_ids, m.handled_order_ids):
        st.clear()


class FakePayPal:
    def __init__(self):
        self.calls, self.approved, self.capture_fails = [], True, False

    async def __call__(self, method, path, *, json=None, mock=None, request_id=None):
        self.calls.append((method, path, mock))
        if path == "/v2/checkout/orders":
            if mock == "DUPLICATE_INVOICE_ID":
                raise pc.PayPalError(422, "DUPLICATE_INVOICE_ID", "Duplicate Invoice ID", "dbg1")
            amt = json["purchase_units"][0]["amount"]["value"]
            self.amount = amt
            return {"id": "ORD1", "status": "PAYER_ACTION_REQUIRED",
                    "links": [{"rel": "payer-action", "href": "https://sandbox.paypal.com/checkoutnow?token=ORD1"}]}
        if path.endswith("/authorize"):
            if not self.approved:
                raise pc.PayPalError(422, "ORDER_NOT_APPROVED", "Not approved")
            if mock == "INSTRUMENT_DECLINED":
                raise pc.PayPalError(422, "INSTRUMENT_DECLINED", "Declined", "dbg2")
            return {"payer": {"email_address": "buyer@example.com", "address": {"country_code": "FR"}},
                    "purchase_units": [{"payments": {"authorizations": [
                        {"id": "AUTH1", "status": "CREATED", "amount": {"value": self.amount, "currency_code": "EUR"}}]}}]}
        if path.endswith("/capture"):
            if self.capture_fails:
                raise pc.PayPalError(422, "AUTHORIZATION_VOIDED", "Voided")
            return {"id": "CAP1", "status": "COMPLETED"}
        if path.endswith("/void"):
            return {}
        raise AssertionError(path)

    def captured(self):
        return [c for c in self.calls if c[1].endswith("/capture")]


@pytest.fixture
def env(monkeypatch):
    _reset()
    fake = FakePayPal()
    monkeypatch.setattr(pc, "_request", fake)
    return TestClient(m.app), fake


def _order(c, amount):
    r = c.post("/paypal/orders", json={"amount": amount, "invoice_id": "INV-1"})
    assert r.status_code == 200 and "approve_url" in r.json()
    return r.json()["order_id"]


def test_clean_payment_is_captured_automatically(env):
    c, fake = env
    r = c.post(f"/paypal/orders/{_order(c, 49.9)}/authorize").json()
    assert r["verdict"]["source"] == "paypal_sandbox"
    assert r["verdict"]["action_taken"] == "approved"
    assert r["paypal"]["status"].startswith("CAPTURED") and len(fake.captured()) == 1


def test_held_then_real_capture_on_unlock(env):
    c, fake = env
    r = c.post(f"/paypal/orders/{_order(c, 500)}/authorize", json={"signals": {"recent_micro_payments": 8}}).json()
    assert r["verdict"]["action_taken"] == "held" and not fake.captured()  # fonds retenus
    d = c.post("/shield/approve/AUTH1").json()
    assert d["capture"]["simulated"] is False and d["capture"]["capture_id"] == "CAP1"
    assert len(fake.captured()) == 1


def test_capture_failure_keeps_alert(env):
    c, fake = env
    c.post(f"/paypal/orders/{_order(c, 500)}/authorize", json={"signals": {"recent_micro_payments": 8}})
    fake.capture_fails = True
    r = c.post("/shield/approve/AUTH1")
    assert r.status_code == 502 and r.json()["detail"]["issue"] == "AUTHORIZATION_VOIDED"
    assert m.shield_verdicts[-1].action_taken == "held"  # inchangé


def test_negative_testing_decline(env):
    c, _ = env
    r = c.post(f"/paypal/orders/{_order(c, 80)}/authorize", json={"mock": "INSTRUMENT_DECLINED"}).json()
    assert r["paypal"]["status"] == "DECLINED"
    assert r["verdict"]["action_taken"] == "alerted" and "INSTRUMENT_DECLINED" in r["verdict"]["reason"]
    assert m.get_merchant_state().pending_disputes == 0  # un refus n'est pas un litige


def test_duplicate_invoice_is_surfaced(env):
    c, _ = env
    r = c.post("/paypal/orders", json={"amount": 10, "mock": "DUPLICATE_INVOICE_ID"})
    assert r.status_code == 422 and r.json()["detail"]["issue"] == "DUPLICATE_INVOICE_ID"


def test_not_approved_yet_can_retry(env):
    c, fake = env
    oid = _order(c, 20)
    fake.approved = False
    assert c.post(f"/paypal/orders/{oid}/authorize").status_code == 409
    fake.approved = True
    assert c.post(f"/paypal/orders/{oid}/authorize").status_code == 200
    assert c.post(f"/paypal/orders/{oid}/authorize").status_code == 404  # déjà traité


def test_webhook_flow_and_no_double_count(env):
    c, fake = env
    oid = _order(c, 49.9)
    wh = {"id": "WH1", "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": oid}}
    assert c.post("/webhooks/paypal", json=wh).json()["verdict"]["action_taken"] == "approved"
    back = {"id": "WH2", "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": "CAP1", "amount": {"value": "49.90", "currency_code": "EUR"}}}
    assert c.post("/webhooks/paypal", json=back).json()["status"] == "ignored"
    assert len(m.transaction_history) == 1


def test_late_webhooks_for_api_processed_payment_are_ignored(env):
    """Autorisation, order déjà traité et capture liée : aucun doublon dans l'historique."""
    c, fake = env
    oid = _order(c, 49.9)
    assert c.post(f"/paypal/orders/{oid}/authorize").json()["verdict"]["action_taken"] == "approved"
    assert len(m.transaction_history) == 1

    late = [
        {"id": "W1", "event_type": "PAYMENT.AUTHORIZATION.CREATED",
         "resource": {"id": "AUTH1", "amount": {"value": "49.90", "currency_code": "EUR"}}},
        {"id": "W2", "event_type": "CHECKOUT.ORDER.APPROVED",
         "resource": {"id": oid, "purchase_units": [{"amount": {"value": "49.90", "currency_code": "EUR"}}]}},
        {"id": "W3", "event_type": "PAYMENT.CAPTURE.COMPLETED",
         "resource": {"id": "CAP_OTHER", "amount": {"value": "49.90", "currency_code": "EUR"},
                      "supplementary_data": {"related_ids": {"order_id": oid, "authorization_id": "AUTH1"}}}},
    ]
    for wh in late:
        assert c.post("/webhooks/paypal", json=wh).json()["status"] == "ignored"
    assert len(m.transaction_history) == 1


def test_simulated_source(env):
    c, _ = env
    r = c.post("/simulate/transaction", json={"event_type": "PAYMENT.CAPTURE.COMPLETED", "transaction_id": "S1", "amount": 10}).json()
    assert r["verdict"]["source"] == "simulated"
    wh = {"id": "W", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C9", "amount": {"value": "5", "currency_code": "EUR"}}}
    assert c.post("/webhooks/paypal", json=wh).json()["verdict"]["source"] == "simulated"  # signature non vérifiée
