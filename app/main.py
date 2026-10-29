import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import ally, paypal_client, shield
from app.config import get_settings
from app.paypal_client import PayPalError
from app.models import AllySuggestion, MerchantState, ShieldVerdict, TransactionEvent
from app.shield import analyze_transaction, update_merchant_state
from app.webhooks.handlers import parse_paypal_event

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sentinel")

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BASE_DIR / "dashboard"

app = FastAPI(title="PayPal Sentinel", version="0.3.0")

ALLY_EVERY_N = 3  # Ally est "throttlé" : une analyse toutes les N transactions

# Stockage en mémoire (MVP)
DEFAULT_MERCHANT_ID = "demo_merchant"
merchant_states: dict[str, MerchantState] = {}
shield_verdicts: list[ShieldVerdict] = []
ally_suggestions: list[AllySuggestion] = []
transaction_history: list[TransactionEvent] = []
seen_ally_titles: set[str] = set()   # y compris les suggestions déjà "lues"
seen_ally_actions: set[str] = set()

_gemini_health_cache = {"ok": False, "error": None, "last_check": 0}
_paypal_health_cache = {"ok": False, "error": None, "last_check": 0}

# Flux PayPal Sandbox réel (Orders v2 -> authorize -> Shield -> capture/void)
pending_orders: dict[str, dict] = {}   # order_id -> {amount, currency} en attente d'approbation buyer
paypal_refs: dict[str, dict] = {}      # authorization_id -> {order_id, status, currency}
known_capture_ids: set[str] = set()    # captures faites par nous (le webhook retour est ignoré)


def get_merchant_state(merchant_id: str = DEFAULT_MERCHANT_ID) -> MerchantState:
    if merchant_id not in merchant_states:
        merchant_states[merchant_id] = MerchantState(merchant_id=merchant_id)
    return merchant_states[merchant_id]


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def register_suggestion(s: AllySuggestion, force: bool = False) -> bool:
    """Déduplique : refuse une suggestion dont le titre ou l'action a déjà été proposé."""
    t, a = _norm(s.title), _norm(s.action)
    if not force and (t in seen_ally_titles or a in seen_ally_actions):
        return False
    seen_ally_titles.add(t)
    seen_ally_actions.add(a)
    ally_suggestions.append(s)
    return True


async def process_event(event: TransactionEvent) -> dict:
    state = get_merchant_state()

    # 1. Mémoire transactionnelle pour Ally
    transaction_history.append(event)
    past_history = transaction_history[-31:-1]  # sans l'événement courant
    run_ally = len(transaction_history) % ALLY_EVERY_N == 0

    # 2. Shield (protège le présent) et Ally (construit le futur) en parallèle
    tasks = [shield.analyze_transaction(event, state)]
    if run_ally:
        tasks.append(ally.generate_suggestion(
            event, state, past_history, [s.title for s in ally_suggestions]
        ))
    results = await asyncio.gather(*tasks)
    verdict: ShieldVerdict = results[0]
    verdict.source = event.raw_payload.get("source", "simulated")

    # 3. État marchand + stockage
    update_merchant_state(state, verdict, event)
    shield_verdicts.append(verdict)

    suggestion, ally_status = None, "throttled"
    if run_ally:
        candidate: AllySuggestion = results[1]
        if register_suggestion(candidate):
            suggestion, ally_status = candidate, "generated"
        else:
            ally_status = "duplicate"

    return {
        "verdict": verdict.model_dump(mode="json"),
        "suggestion": suggestion.model_dump(mode="json") if suggestion else None,
        "ally_status": ally_status,
        "merchant_state": state.model_dump(mode="json"),
    }


# ─── ROUTES ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    s = get_settings()
    now = time.time()
    if now - _gemini_health_cache["last_check"] > 60:  # cache 60 s : évite le rate-limit
        gemini_ok, gemini_error = False, None
        if s.google_api_key:
            try:
                from google import genai
                client = genai.Client(api_key=s.google_api_key)
                await client.aio.models.generate_content(model=s.google_model, contents="Say OK")
                gemini_ok = True
            except Exception as e:
                gemini_error = str(e)
        _gemini_health_cache.update({"ok": gemini_ok, "error": gemini_error, "last_check": now})
    if now - _paypal_health_cache["last_check"] > 60:
        ok, err = await paypal_client.check_credentials()
        _paypal_health_cache.update({"ok": ok, "error": err, "last_check": now})
    return {
        "status": "ok",
        "paypal_environment": s.paypal_environment,
        "paypal_configured": paypal_client.is_configured(),
        "paypal_working": _paypal_health_cache["ok"],
        "paypal_error": _paypal_health_cache["error"],
        "gemini_model": s.google_model,
        "gemini_key_set": bool(s.google_api_key),
        "gemini_working": _gemini_health_cache["ok"],
        "gemini_error": _gemini_health_cache["error"],
    }


@app.get("/merchant/state")
async def merchant_state_view():
    """KPI du Command Center."""
    state = get_merchant_state()
    return {
        **state.model_dump(mode="json"),
        "transactions_analyzed": len(transaction_history),
        "active_alerts": sum(1 for v in shield_verdicts if v.action_taken != "approved"),
        "ally_opportunities": len(ally_suggestions),
    }


@app.post("/simulate/transaction")
async def simulate_transaction(event: TransactionEvent):
    return await process_event(event)


@app.post("/simulate/stress")
async def simulate_financial_stress(payload: dict):
    """Injecte du stress financier (factures impayées) et force une suggestion Ally de survie."""
    state = get_merchant_state()
    state.unpaid_invoices_count += int(payload.get("unpaid_invoices", 0))
    state.unpaid_invoices_amount += float(payload.get("unpaid_amount", 0.0))
    state.stress_level = min(10, state.stress_level + 2)

    suggestion = None
    if transaction_history:
        last_event = transaction_history[-1]
        suggestion = await ally.generate_suggestion(
            last_event, state, transaction_history[-31:-1], [s.title for s in ally_suggestions]
        )
        register_suggestion(suggestion, force=True)  # la démo "crise" ne doit jamais être filtrée

    log.info("💸 Financial stress injected: %d unpaid invoices", state.unpaid_invoices_count)
    return {
        "status": "stress_injected",
        "suggestion": suggestion.model_dump(mode="json") if suggestion else None,
        "merchant_state": state.model_dump(mode="json"),
    }


# ─── FLUX PAYPAL SANDBOX RÉEL ──────────────────────────────────────────────────

class OrderRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = "EUR"
    description: Optional[str] = None
    invoice_id: Optional[str] = None
    mock: Optional[str] = None  # negative testing sandbox (ex. DUPLICATE_INVOICE_ID)


class AuthorizeRequest(BaseModel):
    mock: Optional[str] = None      # negative testing sandbox (ex. INSTRUMENT_DECLINED)
    signals: Optional[dict] = None  # contexte de risque simulé (sandbox uniquement)


def _raise_http(e: PayPalError, status: int = 502):
    raise HTTPException(status_code=status, detail=e.to_dict())


@app.post("/paypal/orders")
async def create_paypal_order(req: OrderRequest):
    """Étape 1 : crée un order Sandbox (intent=AUTHORIZE). Le buyer l'approuve via approve_url."""
    try:
        order = await paypal_client.create_order(
            req.amount, req.currency, req.description, req.invoice_id, mock=req.mock
        )
    except PayPalError as e:
        _raise_http(e, 422 if e.status == 422 else 502)
    pending_orders[order["order_id"]] = {"amount": req.amount, "currency": req.currency}
    return order


async def authorize_and_analyze(order_id: str, mock: Optional[str] = None, signals: Optional[dict] = None) -> dict:
    """Étape 2 : autorise l'order approuvé, passe par Shield, puis capture (approved) ou retient (held)."""
    order = pending_orders.pop(order_id, None)
    if order is None:
        raise HTTPException(status_code=404, detail="Order inconnu ou déjà traité")
    if signals and get_settings().paypal_environment.lower() != "sandbox":
        pending_orders[order_id] = order
        raise HTTPException(status_code=400, detail="signals autorisés uniquement en sandbox")
    extra = {"source": "paypal_sandbox", "order_id": order_id, **(signals or {})}

    try:
        auth = await paypal_client.authorize_order(order_id, mock=mock)
    except PayPalError as e:
        if e.status != 422 or e.issue == "ORDER_NOT_APPROVED":
            pending_orders[order_id] = order  # l'acheteur n'a pas encore approuvé : on peut réessayer
            _raise_http(e, 409 if e.issue == "ORDER_NOT_APPROVED" else 502)
        # Refus PayPal (ex. INSTRUMENT_DECLINED) : Shield alerte le marchand
        event = TransactionEvent(
            event_type="PAYMENT.CAPTURE.DENIED", transaction_id=f"DENIED_{order_id}",
            amount=order["amount"], currency=order["currency"],
            raw_payload={**extra, "paypal_error": e.to_dict()},
        )
        result = await process_event(event)
        return {**result, "paypal": {"status": "DECLINED", "error": e.to_dict()}}

    tid = auth["authorization_id"]
    paypal_refs[tid] = {"order_id": order_id, "status": "authorized", "currency": auth["currency"]}
    event = TransactionEvent(
        event_type="PAYMENT.AUTHORIZATION.CREATED", transaction_id=tid,
        amount=auth["amount"], currency=auth["currency"],
        buyer_email=auth["buyer_email"], buyer_country=auth["buyer_country"],
        raw_payload=extra,
    )
    result = await process_event(event)

    action = result["verdict"]["action_taken"]
    paypal: dict = {"status": "AUTHORIZED", "authorization_id": tid, "order_id": order_id}
    try:
        if action == "approved":  # RAS : on capture tout de suite
            cap = await paypal_client.capture_authorization(tid)
            paypal_refs[tid]["status"] = "captured"
            known_capture_ids.add(cap["capture_id"])
            paypal.update(status=f"CAPTURED ({cap['status']})", capture_id=cap["capture_id"])
        elif action == "blocked":  # menace critique : on libère les fonds de l'acheteur
            await paypal_client.void_authorization(tid)
            paypal_refs[tid]["status"] = "voided"
            paypal["status"] = "VOIDED"
        else:  # held / alerted : l'autorisation reste ouverte, le marchand décide
            paypal["status"] = "AUTHORIZED — funds held, not captured"
    except PayPalError as e:
        paypal["error"] = e.to_dict()
    return {**result, "paypal": paypal}


@app.post("/paypal/orders/{order_id}/authorize")
async def authorize_paypal_order(order_id: str, req: Optional[AuthorizeRequest] = None):
    req = req or AuthorizeRequest()
    return await authorize_and_analyze(order_id, mock=req.mock, signals=req.signals)


@app.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if get_settings().paypal_webhook_id:
        try:
            ok = await paypal_client.verify_webhook_signature(request.headers, payload)
        except Exception as exc:
            log.warning("Webhook verification error: %s", exc)
            raise HTTPException(status_code=502, detail="Signature verification unavailable")
        if not ok:
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_type = payload.get("event_type", "")
    resource_id = (payload.get("resource") or {}).get("id")

    # L'acheteur vient d'approuver un order créé par Sentinel : on autorise, Shield décide.
    if event_type == "CHECKOUT.ORDER.APPROVED" and resource_id in pending_orders:
        return await authorize_and_analyze(resource_id)
    # Webhook retour d'une capture déjà traitée via l'API : on ne la compte pas deux fois.
    if event_type.startswith("PAYMENT.CAPTURE") and resource_id in known_capture_ids:
        return {"status": "ignored", "reason": "capture already processed via API"}

    event = parse_paypal_event(payload)
    # "paypal_webhook" seulement si la signature a été vérifiée ; sinon c'est une simulation.
    event.raw_payload["source"] = "paypal_webhook" if get_settings().paypal_webhook_id else "simulated"
    return await process_event(event)


@app.post("/shield/approve/{transaction_id}")
async def approve_shield(transaction_id: str):
    """Human-in-the-loop : le marchand débloque et valide. Capture réelle si l'autorisation vient du sandbox."""
    state = get_merchant_state()
    verdict = next(
        (v for v in reversed(shield_verdicts)
         if v.transaction_id == transaction_id and v.action_taken in ("held", "alerted", "blocked")),
        None,
    )
    if verdict is None:
        raise HTTPException(status_code=404, detail="Verdict introuvable ou déjà résolu")

    event = next((e for e in reversed(transaction_history) if e.transaction_id == transaction_id), None)
    was_held = verdict.action_taken in ("held", "blocked")
    was_alerted = verdict.action_taken == "alerted"

    capture = None
    if was_held and event:
        ref = paypal_refs.get(transaction_id)
        if ref and ref["status"] == "authorized":
            # Capture RÉELLE sur le sandbox : si PayPal refuse, rien ne change côté Sentinel.
            try:
                cap = await paypal_client.capture_authorization(transaction_id)
            except PayPalError as e:
                _raise_http(e)
            ref["status"] = "captured"
            known_capture_ids.add(cap["capture_id"])
            capture = {
                "simulated": False,
                "call": f"POST /v2/payments/authorizations/{transaction_id}/capture",
                "status": cap["status"], "capture_id": cap["capture_id"],
                "amount": event.amount, "currency": event.currency,
            }
        else:
            capture = {
                "simulated": True,
                "call": f"POST /v2/payments/authorizations/{transaction_id}/capture",
                "status": "COMPLETED", "amount": event.amount, "currency": event.currency,
            }
        state.fraud_attempts_blocked = max(0, state.fraud_attempts_blocked - 1)
        state.money_saved_by_shield = max(0.0, state.money_saved_by_shield - event.amount)
        log.info("✅ Capture %s: %s %.2f %s", "simulée" if capture["simulated"] else "RÉELLE",
                 transaction_id, event.amount, event.currency)
    if was_alerted and event and event.event_type.startswith("CUSTOMER.DISPUTE"):
        state.pending_disputes = max(0, state.pending_disputes - 1)

    verdict.action_taken = "approved"
    verdict.explanation_for_merchant = "Validated by you — transaction unlocked."
    return {"status": "approved", "capture": capture, "merchant_state": state.model_dump(mode="json")}


@app.post("/ally/dismiss/{suggestion_id}")
async def dismiss_ally(suggestion_id: str):
    for i, s in enumerate(ally_suggestions):
        if s.suggestion_id == suggestion_id:
            ally_suggestions.pop(i)  # titre/action restent dans seen_* : pas de ré-affichage
            return {"status": "dismissed"}
    raise HTTPException(status_code=404, detail="Suggestion introuvable")


@app.get("/shield/verdicts")
async def list_verdicts():
    return [v.model_dump(mode="json") for v in reversed(shield_verdicts)]


@app.get("/ally/suggestions")
async def list_suggestions():
    return [s.model_dump(mode="json") for s in reversed(ally_suggestions)]


@app.get("/dashboard", include_in_schema=False)
async def dashboard():
    return FileResponse(DASHBOARD_DIR / "index.html")


app.mount("/dashboard/src", StaticFiles(directory=DASHBOARD_DIR / "src"), name="dashboard-src")
