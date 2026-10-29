"""Client PayPal (Sandbox / Live) : OAuth2, Orders v2, Payments v2 (authorize/capture/void), webhooks."""
import base64
import json as _json
import logging
import time
import uuid
from typing import Any, Mapping, Optional

import httpx

from app.config import get_settings

log = logging.getLogger("sentinel.paypal")

_token: Optional[str] = None
_token_expiry: float = 0.0


class PayPalError(Exception):
    """Erreur renvoyée par l'API PayPal (enveloppe name/details/debug_id)."""

    def __init__(self, status: int, issue: str, message: str = "", debug_id: Optional[str] = None):
        super().__init__(f"{status} {issue}: {message}")
        self.status, self.issue, self.message, self.debug_id = status, issue, message, debug_id

    def to_dict(self) -> dict:
        return {"status": self.status, "issue": self.issue, "message": self.message, "debug_id": self.debug_id}


def is_configured() -> bool:
    s = get_settings()
    return bool(s.paypal_client_id and s.paypal_client_secret)


async def get_access_token() -> str:
    global _token, _token_expiry
    if _token and time.time() < _token_expiry - 30:
        return _token
    s = get_settings()
    if not is_configured():
        raise PayPalError(0, "NOT_CONFIGURED", "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET non configurés")
    basic = base64.b64encode(f"{s.paypal_client_id}:{s.paypal_client_secret}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{s.paypal_base_url}/v1/oauth2/token",
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
    if r.status_code != 200:
        raise PayPalError(r.status_code, "AUTH_FAILED", "Impossible d'obtenir un token (vérifie Client ID / Secret)")
    data = r.json()
    _token = data["access_token"]
    _token_expiry = time.time() + int(data.get("expires_in", 300))
    return _token


async def check_credentials() -> tuple[bool, Optional[str]]:
    """Vérifie que les identifiants Sandbox donnent un vrai token (pastille du dashboard)."""
    try:
        await get_access_token()
        return True, None
    except PayPalError as e:
        return False, e.message
    except httpx.HTTPError as e:
        return False, str(e)


async def _request(
    method: str,
    path: str,
    *,
    json: Optional[dict] = None,
    mock: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict:
    """Appel REST authentifié. `mock` = negative testing Sandbox (en-tête PayPal-Mock-Response)."""
    s = get_settings()
    token = await get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "PayPal-Request-Id": request_id or uuid.uuid4().hex,
    }
    if mock and s.paypal_environment.lower() == "sandbox":
        headers["PayPal-Mock-Response"] = _json.dumps({"mock_application_codes": mock})
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, f"{s.paypal_base_url}{path}", headers=headers, json=json)
    if r.status_code >= 400:
        try:
            body = r.json()
        except ValueError:
            body = {}
        detail = (body.get("details") or [{}])[0]
        raise PayPalError(
            r.status_code,
            detail.get("issue") or body.get("name") or "ERROR",
            body.get("message") or r.text[:200],
            body.get("debug_id"),
        )
    return r.json() if r.content else {}


# ─── Orders v2 ─────────────────────────────────────────────────────────────────

async def create_order(
    amount: float,
    currency: str = "EUR",
    description: Optional[str] = None,
    invoice_id: Optional[str] = None,
    mock: Optional[str] = None,
) -> dict:
    """Crée un order en intent=AUTHORIZE : les fonds sont réservés, pas encore capturés."""
    s = get_settings()
    unit: dict[str, Any] = {"amount": {"currency_code": currency, "value": f"{amount:.2f}"}}
    if description:
        unit["description"] = description
    if invoice_id:
        unit["invoice_id"] = invoice_id
    body = {
        "intent": "AUTHORIZE",
        "purchase_units": [unit],
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "payment_method_preference": "IMMEDIATE_PAYMENT_REQUIRED",
                    "user_action": "PAY_NOW",
                    "return_url": f"{s.app_base_url}/dashboard",
                    "cancel_url": f"{s.app_base_url}/dashboard",
                }
            }
        },
    }
    data = await _request("POST", "/v2/checkout/orders", json=body, mock=mock)
    link = next((l["href"] for l in data.get("links", []) if l.get("rel") in ("payer-action", "approve")), None)
    return {"order_id": data["id"], "status": data.get("status"), "approve_url": link}


async def get_order(order_id: str) -> dict:
    return await _request("GET", f"/v2/checkout/orders/{order_id}")


async def authorize_order(order_id: str, mock: Optional[str] = None) -> dict:
    """Autorise un order approuvé par l'acheteur. Les fonds sont réservés, non capturés."""
    data = await _request("POST", f"/v2/checkout/orders/{order_id}/authorize", json={}, mock=mock)
    unit = (data.get("purchase_units") or [{}])[0]
    auths = (unit.get("payments") or {}).get("authorizations") or []
    if not auths:
        raise PayPalError(422, "NO_AUTHORIZATION", "PayPal n'a renvoyé aucune autorisation")
    auth = auths[0]
    payer = data.get("payer") or {}
    amt = auth.get("amount") or {}
    return {
        "authorization_id": auth["id"],
        "status": auth.get("status"),
        "amount": float(amt.get("value", 0) or 0),
        "currency": amt.get("currency_code", "EUR"),
        "buyer_email": payer.get("email_address"),
        "buyer_country": (payer.get("address") or {}).get("country_code"),
    }


# ─── Payments v2 ───────────────────────────────────────────────────────────────

async def capture_authorization(authorization_id: str, mock: Optional[str] = None) -> dict:
    data = await _request(
        "POST", f"/v2/payments/authorizations/{authorization_id}/capture",
        json={"final_capture": True}, mock=mock,
    )
    return {"capture_id": data.get("id"), "status": data.get("status")}


async def void_authorization(authorization_id: str) -> dict:
    await _request("POST", f"/v2/payments/authorizations/{authorization_id}/void")
    return {"status": "VOIDED"}


async def get_transaction_details(capture_id: str) -> dict[str, Any]:
    return await _request("GET", f"/v2/payments/captures/{capture_id}")


# ─── Webhooks ──────────────────────────────────────────────────────────────────

async def verify_webhook_signature(headers: Mapping[str, str], payload: dict[str, Any]) -> bool:
    s = get_settings()
    token = await get_access_token()
    body = {
        "auth_algo": headers.get("paypal-auth-algo"),
        "cert_url": headers.get("paypal-cert-url"),
        "transmission_id": headers.get("paypal-transmission-id"),
        "transmission_sig": headers.get("paypal-transmission-sig"),
        "transmission_time": headers.get("paypal-transmission-time"),
        "webhook_id": s.paypal_webhook_id,
        "webhook_event": payload,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{s.paypal_base_url}/v1/notifications/verify-webhook-signature",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
    if r.status_code != 200:
        return False
    return r.json().get("verification_status") == "SUCCESS"
