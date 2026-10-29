"""Parcours complet de la démo (sans clé Gemini : fallbacks déterministes)."""
import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import main as m

ROOT = Path(__file__).resolve().parent.parent


def _reset():
    m.merchant_states.clear(); m.shield_verdicts.clear(); m.ally_suggestions.clear()
    m.transaction_history.clear(); m.seen_ally_titles.clear(); m.seen_ally_actions.clear()


def _business_history():
    spec = importlib.util.spec_from_file_location("sb", ROOT / "scripts" / "simulate_business.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod.generate_history()


def test_full_demo():
    _reset()
    c = TestClient(m.app)

    statuses = [c.post("/simulate/transaction", json=e).json()["ally_status"] for e in _business_history()]
    assert statuses.count("throttled") >= 12          # Ally throttlé (1 fois sur 3)
    titles = [s["title"] for s in c.get("/ally/suggestions").json()]
    assert len(titles) == len(set(titles))             # dédupliqué
    assert any("Bob" in t for t in titles)             # churn détecté
    assert any("Alice" in t for t in titles)           # upsell détecté
    assert all(x["suggestion_type"] != "cash_flow_alert" for x in c.get("/ally/suggestions").json())  # pas de faux dry spell
    assert c.get("/shield/verdicts").json()[0]["action_taken"] == "approved"

    for f in sorted((ROOT / "tests" / "scenarios").glob("*.json")):
        c.post("/simulate/transaction", json=json.loads(f.read_text()))
    kpi = c.get("/merchant/state").json()
    assert kpi["active_alerts"] == 3 and kpi["money_saved_by_shield"] == 4500 + 500
    assert kpi["transactions_analyzed"] == 23

    r = c.post("/shield/approve/TX_INVOICE_ANOMALY_001").json()
    assert r["capture"]["status"] == "COMPLETED"
    assert c.get("/merchant/state").json()["active_alerts"] == 2
    assert c.post("/shield/approve/TX_INVOICE_ANOMALY_001").status_code == 404

    st = c.post("/simulate/stress", json={"unpaid_invoices": 3, "unpaid_amount": 2400}).json()
    assert st["suggestion"]["suggestion_type"] == "cash_flow_alert"

    sid = c.get("/ally/suggestions").json()[0]["suggestion_id"]
    assert c.post(f"/ally/dismiss/{sid}").status_code == 200
    assert all(s["suggestion_id"] != sid for s in c.get("/ally/suggestions").json())
