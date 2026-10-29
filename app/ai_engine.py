"""Couche Gemini. Ne décide JAMAIS : explique et suggère seulement."""

import json
import logging
import re
from typing import Any, Optional

from app.config import get_settings

log = logging.getLogger("sentinel.ai")

_client = None


def _get_client():
    """Initialise le client google-genai (nouveau SDK)."""
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    if not settings.google_api_key:
        log.error("❌ GOOGLE_API_KEY vide — Gemini désactivé, fallback utilisé.")
        return None

    try:
        from google import genai

        _client = genai.Client(api_key=settings.google_api_key)
        log.info("✅ Client Gemini initialisé (modèle: %s)", settings.google_model)
        return _client
    except ImportError:
        log.error("❌ Package 'google-genai' non installé. Lance: pip install google-genai")
        return None
    except Exception as exc:
        log.error("❌ Erreur init Gemini: %s", exc)
        return None


def _parse_json(text: str) -> Optional[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        log.warning("⚠️  Réponse Gemini non-JSON: %.200s", text)
        return None


async def _generate_json(prompt: str) -> Optional[dict]:
    client = _get_client()
    if client is None:
        return None

    settings = get_settings()
    try:
        response = await client.aio.models.generate_content(
            model=settings.google_model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "temperature": 0.4,
            },
        )
        return _parse_json(response.text)
    except Exception as exc:
        log.error("❌ Appel Gemini échoué (modèle=%s): %s", settings.google_model, exc)
        return None


# ─── Prompts ───────────────────────────────────────────────────────────────────

SHIELD_PROMPT = """You are PayPal Sentinel, a calm and reassuring fraud-protection assistant for small merchants.
A deterministic rules engine already made the decision below. Do NOT change it.
Rewrite it as a short (max 2 sentences), clear, empathetic message addressed to the merchant, in first person ("I ...").
Mention what you did (action_taken) and that the merchant stays in control. No jargon.
Reply ONLY with JSON: {{"explanation_for_merchant": "..."}}

Input:
{payload}
"""

ALLY_PROMPT = """You are Ally, an expert Business Analyst and empathetic CFO for a PayPal merchant.
Your goal is to find ONE specific growth or survival opportunity by analyzing the data below.

Look for patterns in this priority order:
1. **Cash Flow Survival (URGENT)**: If `days_since_last_sale` > 5, the merchant may struggle to pay bills. Suggest immediate tactics: flash sale, PayPal Working Capital, early payment discounts for clients.
2. **Unpaid Invoices**: If `unpaid_invoices_count` > 0, suggest sending polite automated reminders via PayPal Invoicing.
3. **Churn Risk**: A loyal buyer who went silent. Use `analytics.churn_candidates` and cite the buyer, number of orders, average amount and days of silence.
4. **Upsell Potential**: A buyer whose average basket is rising. Use `analytics.upsell_candidates`, cite the growth, suggest a premium bundle or subscription.
5. **Revenue Opportunities**: General growth strategies based on merchant_state.

Rules:
- Do NOT talk about fraud, security, or blocking (that is Shield's job).
- Never repeat a topic or customer already listed in `already_suggested`; pick a different angle or customer.
- Be specific: cite the data (e.g., "It's been 12 days since your last sale..." or "Client alice@... hasn't ordered since...").
- If the situation is urgent (cash flow gap), be empathetic but actionable — offer a lifeline, not generic advice.
- suggestion_type must be one of: revenue_opportunity, cash_flow_alert, customer_retention, operational_improvement.

Reply ONLY with JSON:
{{"suggestion_type": "...", "title": "...", "description": "...", "action": "..."}}
Input:
{payload}
"""


# ─── Fallbacks ─────────────────────────────────────────────────────────────────

def _fallback_shield(decision: dict) -> str:
    action = decision.get("action_taken")
    if action == "held":
        return (
            "I noticed unusual activity on this transaction. I placed it on hold "
            "temporarily so you can review it safely."
        )
    if action == "alerted" and "declined" in str(decision.get("reason", "")).lower():
        return (
            "PayPal declined this payment, so nothing was charged. "
            "You may want to contact the buyer to suggest another payment method."
        )
    if action == "alerted":
        return (
            "This dispute looks suspicious: tracking shows the item was delivered. I flagged it and "
            "prepared the delivery proof so you can respond with confidence."
        )
    if action == "blocked":
        return "I blocked this transaction to protect your account. You can review it anytime."
    return "Everything looks normal on this transaction. No action needed."


# ─── API publique ──────────────────────────────────────────────────────────────

async def explain_shield(payload: dict[str, Any]) -> str:
    data = await _generate_json(SHIELD_PROMPT.format(payload=json.dumps(payload, default=str)))
    if data and isinstance(data.get("explanation_for_merchant"), str):
        return data["explanation_for_merchant"].strip()
    log.warning("⚠️  Fallback Shield utilisé (Gemini indisponible ou réponse invalide)")
    return _fallback_shield(payload.get("shield_decision", {}))


async def suggest_ally(payload: dict[str, Any]) -> Optional[dict]:
    return await _generate_json(ALLY_PROMPT.format(payload=json.dumps(payload, default=str)))
