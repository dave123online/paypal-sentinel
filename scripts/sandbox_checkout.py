"""Démo PayPal Sandbox RÉELLE : order -> approbation buyer -> Shield -> capture ou rétention.

  python scripts/sandbox_checkout.py                       # paiement normal : capturé automatiquement
  python scripts/sandbox_checkout.py --amount 500 --hold   # Shield retient (contexte de risque simulé)
  python scripts/sandbox_checkout.py --decline             # negative testing : INSTRUMENT_DECLINED
  python scripts/sandbox_checkout.py --dup-invoice         # negative testing : DUPLICATE_INVOICE_ID

Prérequis : PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET dans .env, serveur lancé, et un compte
Sandbox *Personal* (buyer) pour approuver sur www.sandbox.paypal.com.
"""
import argparse
import json
import os
import sys
import uuid

import httpx

BASE_URL = os.getenv("SENTINEL_URL", "http://127.0.0.1:8000")


def show(title: str, data) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=float, default=49.90)
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--hold", action="store_true", help="simule un contexte de risque (velocity) pour que Shield retienne")
    ap.add_argument("--decline", action="store_true", help="force INSTRUMENT_DECLINED à l'autorisation")
    ap.add_argument("--dup-invoice", action="store_true", help="force DUPLICATE_INVOICE_ID à la création")
    args = ap.parse_args()

    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        invoice = f"SENTINEL-{uuid.uuid4().hex[:8]}"
        body = {"amount": args.amount, "currency": args.currency, "invoice_id": invoice,
                "description": "PayPal Sentinel sandbox demo"}
        if args.dup_invoice:
            body["mock"] = "DUPLICATE_INVOICE_ID"

        r = c.post("/paypal/orders", json=body)
        if r.status_code != 200:
            show(f"Création refusée par PayPal (HTTP {r.status_code})", r.json())
            return 0 if args.dup_invoice else 1  # attendu pour --dup-invoice
        order = r.json()
        show("Order créé (intent=AUTHORIZE)", order)

        print("\n1) Ouvre ce lien et connecte-toi avec ton compte Sandbox PERSONAL (buyer) :")
        print(f"   {order['approve_url']}")
        print("2) Approuve le paiement, puis reviens ici.")
        print("   (Si un webhook CHECKOUT.ORDER.APPROVED est configuré, Sentinel traite déjà l'order tout seul.)")

        payload: dict = {}
        if args.decline:
            payload["mock"] = "INSTRUMENT_DECLINED"
        if args.hold:
            payload["signals"] = {"recent_micro_payments": 8}

        while True:
            input("\nAppuie sur Entrée une fois le paiement approuvé… ")
            r = c.post(f"/paypal/orders/{order['order_id']}/authorize", json=payload)
            if r.status_code == 409:
                print("PayPal indique que l'order n'est pas encore approuvé. Approuve-le puis réessaie.")
                continue
            break

        if r.status_code == 404:
            print("Order déjà traité (probablement via webhook). Regarde le dashboard.")
            return 0
        if r.status_code != 200:
            show(f"Erreur (HTTP {r.status_code})", r.json())
            return 1
        res = r.json()
        show("Verdict Shield", res["verdict"])
        show("PayPal", res["paypal"])
        print(f"\nDashboard : {BASE_URL}/dashboard")
        if res["verdict"]["action_taken"] == "held":
            print("Fonds retenus (autorisation non capturée). Clique sur « Unlock and validate » pour capturer réellement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
