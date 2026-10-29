"""Ally : CFO virtuel basé sur l'historique réel. Totalement découplé de Shield."""
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional, get_args

from app import ai_engine
from app.models import AllySuggestion, MerchantState, SuggestionType, TransactionEvent

_VALID_TYPES = set(get_args(SuggestionType))

CHURN_DAYS = 14        # un client fidèle silencieux depuis >= 14 jours = risque de churn
DRY_SPELL_DAYS = 7     # aucune vente depuis > 7 jours = traversée du désert


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


SALE_TYPES = ("PAYMENT.CAPTURE.COMPLETED", "PAYMENT.AUTHORIZATION.CREATED")


def _sales(history: list[TransactionEvent]) -> list[TransactionEvent]:
    return [h for h in history if h.event_type in SALE_TYPES]


def _compute_days_since_last_sale(history: list[TransactionEvent], now: Optional[datetime] = None) -> int:
    sales = _sales(history)
    if not sales:
        return 0
    now = now or datetime.now(timezone.utc)
    return max(0, (now - max(_utc(s.timestamp) for s in sales)).days)


def _short_name(email: str) -> str:
    local = email.split("@")[0]
    return (re.split(r"[._\-0-9]", local)[0] or local).capitalize()


def analyze_history(history: list[TransactionEvent], now: Optional[datetime] = None) -> dict:
    """Faits calculés de façon déterministe : Gemini rédige, il ne calcule pas."""
    now = now or datetime.now(timezone.utc)
    by_buyer: dict[str, list[TransactionEvent]] = defaultdict(list)
    for s in _sales(history):
        if s.buyer_email:
            by_buyer[s.buyer_email].append(s)

    churn, upsell = [], []
    for buyer, txs in by_buyer.items():
        txs.sort(key=lambda t: _utc(t.timestamp))
        amounts = [t.amount for t in txs]
        days = (now - _utc(txs[-1].timestamp)).days
        if len(txs) >= 3 and days >= CHURN_DAYS:
            churn.append({
                "buyer": buyer, "orders": len(txs),
                "avg_amount": round(sum(amounts) / len(amounts), 2),
                "total_spent": round(sum(amounts), 2),
                "days_since_last_order": days,
            })
        if len(txs) >= 4 and days < CHURN_DAYS:
            first, recent = sum(amounts[:3]) / 3, sum(amounts[-3:]) / 3
            if first > 0 and recent / first >= 1.10:
                upsell.append({
                    "buyer": buyer, "orders": len(txs),
                    "first_avg": round(first, 2), "recent_avg": round(recent, 2),
                    "growth_pct": round((recent / first - 1) * 100),
                })
    churn.sort(key=lambda c: -c["total_spent"])
    upsell.sort(key=lambda u: -u["growth_pct"])

    amounts_all = [s.amount for s in _sales(history)]
    return {
        "days_since_last_sale": _compute_days_since_last_sale(history, now),
        "average_order_value": round(sum(amounts_all) / len(amounts_all), 2) if amounts_all else 0.0,
        "churn_candidates": churn[:3],
        "upsell_candidates": upsell[:3],
    }


def _already(existing: list[str], buyer: str) -> bool:
    name = _short_name(buyer).lower()
    return any(name in t.lower() for t in existing)


def _fallback(state: MerchantState, a: dict, existing: list[str]) -> dict:
    """Fallback déterministe (Gemini indisponible), couvre les 4 compétences d'Ally."""
    gap = a["days_since_last_sale"]
    if state.unpaid_invoices_count > 0:
        return {
            "suggestion_type": "cash_flow_alert",
            "title": "Chase your unpaid invoices",
            "description": (
                f"{state.unpaid_invoices_count} invoice(s) worth {state.unpaid_invoices_amount:.0f} € are still unpaid. "
                "Send polite automatic reminders with PayPal Invoicing and offer a small early-payment discount."
            ),
            "action": "Send PayPal Invoicing reminders",
        }
    if gap > DRY_SPELL_DAYS:
        return {
            "suggestion_type": "cash_flow_alert",
            "title": "Bridge your cash flow gap",
            "description": f"It's been {gap} days since your last sale. Run a flash sale or look at PayPal Working Capital to cover upcoming expenses.",
            "action": "Launch a flash sale or request Working Capital",
        }
    for c in a["churn_candidates"]:
        if not _already(existing, c["buyer"]):
            n = _short_name(c["buyer"])
            return {
                "suggestion_type": "customer_retention",
                "title": f"Win back {n}",
                "description": f"{n} ordered {c['orders']} times (avg {c['avg_amount']:.0f} €) but nothing for {c['days_since_last_order']} days. Send a targeted comeback offer.",
                "action": f"Send a personalized comeback email to {n}",
            }
    for u in a["upsell_candidates"]:
        if not _already(existing, u["buyer"]):
            n = _short_name(u["buyer"])
            return {
                "suggestion_type": "revenue_opportunity",
                "title": f"Upsell {n} with a premium bundle",
                "description": f"{n}'s average order grew from {u['first_avg']:.0f} € to {u['recent_avg']:.0f} € (+{u['growth_pct']}%). Offer a premium bundle or a subscription.",
                "action": f"Propose a premium bundle to {n}",
            }
    if state.monthly_revenue > 3000:
        return {
            "suggestion_type": "revenue_opportunity",
            "title": "Launch a loyalty discount campaign",
            "description": "Your revenue is healthy. A small returning-customer discount could boost repeat purchases.",
            "action": "Create a 5% loyalty code",
        }
    return {
        "suggestion_type": "customer_retention",
        "title": "Re-engage inactive customers",
        "description": "Some customers haven't purchased recently. A friendly reminder could bring them back.",
        "action": "Send a re-engagement email",
    }


def _impact(stype: str, state: MerchantState, a: dict, event: TransactionEvent) -> float:
    aov = a["average_order_value"] or event.amount
    if stype == "cash_flow_alert":
        return round(state.unpaid_invoices_amount or aov * 3, 2)
    if stype == "customer_retention" and a["churn_candidates"]:
        return round(a["churn_candidates"][0]["avg_amount"] * 3, 2)
    if stype == "revenue_opportunity" and a["upsell_candidates"]:
        u = a["upsell_candidates"][0]
        return round((u["recent_avg"] - u["first_avg"]) * 4, 2)
    return round(aov * 0.15, 2)


async def generate_suggestion(
    event: TransactionEvent,
    merchant_state: MerchantState,
    history: list[TransactionEvent],
    existing_titles: Optional[list[str]] = None,
) -> AllySuggestion:
    """Suggestion business basée sur la mémoire transactionnelle (30 dernières transactions)."""
    existing = existing_titles or []
    # Référence temporelle = l'événement courant (identique à 'maintenant' en live, correct en rejeu)
    analytics = analyze_history(history, now=_utc(event.timestamp))

    payload = {
        # Aucune donnée de fraude ici : Ally est découplé de Shield.
        "merchant_state": {
            "monthly_revenue": merchant_state.monthly_revenue,
            "pending_disputes": merchant_state.pending_disputes,
            "stress_level": merchant_state.stress_level,
        },
        "transaction_history": [
            {
                "date": h.timestamp.strftime("%Y-%m-%d %H:%M"),
                "amount": h.amount,
                "buyer": h.buyer_email or "guest",
                "status": "success" if h.event_type in SALE_TYPES else "other",
            }
            for h in history[-30:]
        ],
        "current_event": {"amount": event.amount, "buyer": event.buyer_email},
        "cash_flow_status": {
            "days_since_last_sale": analytics["days_since_last_sale"],
            "unpaid_invoices_count": merchant_state.unpaid_invoices_count,
            "unpaid_invoices_amount": merchant_state.unpaid_invoices_amount,
        },
        "analytics": {
            "average_order_value": analytics["average_order_value"],
            "churn_candidates": analytics["churn_candidates"],
            "upsell_candidates": analytics["upsell_candidates"],
        },
        "already_suggested": existing[-10:],
    }

    data = await ai_engine.suggest_ally(payload) or {}
    fb = _fallback(merchant_state, analytics, existing)

    stype = data.get("suggestion_type")
    if stype not in _VALID_TYPES:
        stype = fb["suggestion_type"]
        data = {}  # type incohérent : on ignore tout le texte Gemini

    return AllySuggestion(
        merchant_id=merchant_state.merchant_id,
        suggestion_type=stype,
        title=str(data.get("title") or fb["title"]),
        description=str(data.get("description") or fb["description"]),
        estimated_impact_eur=_impact(stype, merchant_state, analytics, event),
        action=str(data.get("action") or fb["action"]),
        requires_approval=True,
    )
