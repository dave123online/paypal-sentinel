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


def _order(c, amount, **extra):
    r = c.post("/paypal/orders", json={"amount": amount, "invoice_id": "INV-1", **extra})
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


def test_webhook_flow_honors_hold_given_at_creation(env):
    """--hold : le webhook autorise avant l'Entrée, Shield doit quand même retenir."""
    c, fake = env
    oid = _order(c, 500, signals={"recent_micro_payments": 8})
    wh = {"id": "WH10", "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": oid}}
    r = c.post("/webhooks/paypal", json=wh).json()
    assert r["verdict"]["action_taken"] == "held"
    assert not fake.captured()  # fonds retenus, rien de capturé


def test_webhook_flow_honors_decline_given_at_creation(env):
    """--decline : le refus simulé doit s'appliquer à l'autorisation lancée par le webhook."""
    c, _ = env
    oid = _order(c, 80, authorize_mock="INSTRUMENT_DECLINED")
    wh = {"id": "WH11", "event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": oid}}
    r = c.post("/webhooks/paypal", json=wh).json()
    assert r["paypal"]["status"] == "DECLINED"
    assert r["verdict"]["action_taken"] == "alerted"


# ─── Revue avant soumission ────────────────────────────────────────────────────

def test_declined_payment_can_only_be_acknowledged(env):
    c, fake = env
    c.post(f"/paypal/orders/{_order(c, 80)}/authorize", json={"mock": "INSTRUMENT_DECLINED"})
    tid = "DENIED_ORD1"
    assert c.post(f"/shield/approve/{tid}").status_code == 409          # rien à capturer
    assert m.shield_verdicts[-1].action_taken == "alerted"             # pas devenu "approved"
    assert not fake.captured()
    assert c.get("/merchant/state").json()["active_alerts"] == 1
    assert c.post(f"/shield/acknowledge/{tid}").status_code == 200
    assert m.shield_verdicts[-1].action_taken == "alerted" and m.shield_verdicts[-1].acknowledged
    assert c.get("/merchant/state").json()["active_alerts"] == 0
    assert c.post(f"/shield/acknowledge/{tid}").status_code == 404      # déjà pris en compte


def test_burst_is_blocked_and_authorization_voided(env):
    c, fake = env
    r = c.post(f"/paypal/orders/{_order(c, 500)}/authorize", json={"signals": {"recent_micro_payments": 20}}).json()
    assert r["verdict"]["action_taken"] == "blocked" and r["paypal"]["status"] == "VOIDED"
    assert any(call[1].endswith("/void") for call in fake.calls) and not fake.captured()
    assert c.post("/shield/approve/AUTH1").status_code == 409           # une autorisation annulée ne se débloque pas
    assert c.post("/shield/acknowledge/AUTH1").status_code == 200
    assert c.get("/merchant/state").json()["amount_held_or_blocked"] == 500  # le montant reste compté


def test_protected_routes_need_admin_token(env, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "admin_token", "s3cret")
    c, _ = env
    evt = {"event_type": "PAYMENT.CAPTURE.COMPLETED", "transaction_id": "X1", "amount": 10}
    for path, body in [("/simulate/transaction", evt), ("/simulate/stress", {}), ("/paypal/orders", {"amount": 5}),
                       ("/shield/approve/X", None), ("/shield/acknowledge/X", None), ("/ally/dismiss/X", None)]:
        assert c.post(path, json=body).status_code == 401, path
        assert c.post(path, json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401, path
    assert c.post("/simulate/transaction", json=evt, headers={"X-Admin-Token": "s3cret"}).status_code == 200
    # sans PAYPAL_WEBHOOK_ID (pas de signature à vérifier), le webhook demande aussi le token
    assert c.post("/webhooks/paypal", json={"id": "W", "event_type": "PAYMENT.CAPTURE.COMPLETED"}).status_code == 401
    # lecture seule et santé restent publics (démo accessible aux juges)
    assert c.get("/shield/verdicts").status_code == 200 and c.get("/merchant/state").status_code == 200


def test_no_buyer_email_sent_to_gemini(monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from app import ai_engine, ally
    from app.models import MerchantState, TransactionEvent
    from app.shield import analyze_transaction

    sent = {}

    async def fake_ally(payload):
        sent["ally"] = payload
        cand = payload["analytics"]["churn_candidates"][0]["buyer"]
        return {"suggestion_type": "customer_retention", "title": f"Win back {cand}",
                "description": f"{cand} went silent.", "action": f"Email {cand}"}

    async def fake_shield(payload):
        sent["shield"] = payload
        return "ok"

    monkeypatch.setattr(ai_engine, "suggest_ally", fake_ally)
    monkeypatch.setattr(ai_engine, "explain_shield", fake_shield)
    now = datetime.now(timezone.utc)
    hist = [TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id=f"T{i}", amount=100,
                             buyer_email="bob.martin@example.com", timestamp=now - timedelta(days=40 - 3 * i))
            for i in range(5)]
    ev = TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="T9", amount=20,
                          buyer_email="carol@example.com")
    s = asyncio.run(ally.generate_suggestion(ev, MerchantState(merchant_id="t"), hist))
    assert "@" not in str(sent["ally"]) and "bob" not in str(sent["ally"]).lower()
    assert "Bob" in s.title and "Customer-" not in s.title + s.description + s.action  # prénom remis localement

    risky = TransactionEvent(event_type="PAYMENT.CAPTURE.COMPLETED", transaction_id="R1", amount=500,
                             buyer_email="secret@example.com", raw_payload={"recent_micro_payments": 8})
    asyncio.run(analyze_transaction(risky, MerchantState(merchant_id="t")))
    assert "secret@example.com" not in str(sent["shield"])


def test_network_error_during_authorize_keeps_the_order(env):
    import httpx
    c, fake = env
    oid = _order(c, 49.9)
    original = fake.__call__

    async def flaky(method, path, **kw):
        if path.endswith("/authorize"):
            raise httpx.ConnectTimeout("boom")
        return await original(method, path, **kw)

    pc._request = flaky  # le fixture monkeypatch restaure l'original à la fin du test
    assert c.post(f"/paypal/orders/{oid}/authorize").status_code == 502
    assert oid in m.pending_orders and oid not in m.handled_order_ids
    pc._request = fake
    assert c.post(f"/paypal/orders/{oid}/authorize").status_code == 200
