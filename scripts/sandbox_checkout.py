"""Démo PayPal Sandbox RÉELLE avec affichage des résultats."""
import argparse
import json
import os
import sys
import uuid
import time
import httpx
import os as _os

# Si la démo est protégée par ADMIN_TOKEN : export SENTINEL_ADMIN_TOKEN=...
ADMIN_HEADERS = {"X-Admin-Token": _os.getenv("SENTINEL_ADMIN_TOKEN", "")} if _os.getenv("SENTINEL_ADMIN_TOKEN") else {}

BASE_URL = os.getenv("SENTINEL_URL", "https://paypal-sentinel.onrender.com")

def show(title: str, data) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(data, indent=2, ensure_ascii=False))

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=float, default=49.90)
    ap.add_argument("--currency", default="EUR")
    ap.add_argument("--hold", action="store_true", help="simule un contexte de risque (velocity)")
    ap.add_argument("--block", action="store_true", help="rafale de tests de carte : Shield bloque et annule (void) l'autorisation")
    ap.add_argument("--decline", action="store_true", help="force INSTRUMENT_DECLINED")
    ap.add_argument("--dup-invoice", action="store_true", help="force DUPLICATE_INVOICE_ID")
    args = ap.parse_args()
    if (args.block or args.hold) and args.amount < 300:
        args.amount = 500.0  # les règles hold/block de Shield exigent un montant >= 300 €
        print("(montant relevé à 500 : Shield ne retient / ne bloque qu'à partir de 300)")

    with httpx.Client(base_url=BASE_URL, timeout=60, headers=ADMIN_HEADERS) as c:
        invoice = f"SENTINEL-{uuid.uuid4().hex[:8]}"
        body = {
            "amount": args.amount,
            "currency": args.currency,
            "invoice_id": invoice,
            "description": "PayPal Sentinel sandbox demo"
        }
        
        if args.dup_invoice:
            body["mock"] = "DUPLICATE_INVOICE_ID"
        # Donnés dès la création : un webhook peut autoriser l'order avant ton Entrée, donc
        # le contexte de risque et le refus simulé doivent déjà être connus de Sentinel.
        if args.decline:
            body["authorize_mock"] = "INSTRUMENT_DECLINED"
        if args.hold:
            body["signals"] = {"recent_micro_payments": 8}
        if args.block:
            body["signals"] = {"recent_micro_payments": 20}

        try:   # verdicts déjà présents : sert à repérer le nouveau après l'approbation
            known = {(v["timestamp"], v["transaction_id"]) for v in c.get("/shield/verdicts").json()}
        except (httpx.HTTPError, ValueError):
            known = set()
        r = c.post("/paypal/orders", json=body)
        
        if r.status_code == 401:
            print("Token admin requis par ce serveur. Définis-le puis relance :")
            print("  export SENTINEL_ADMIN_TOKEN=<valeur de ADMIN_TOKEN sur Render>")
            return 1
        if r.status_code != 200:
            show(f"Création refusée (HTTP {r.status_code})", r.json())
            return 0 if args.dup_invoice else 1
        
        order = r.json()
        show("Order créé (intent=AUTHORIZE)", order)
        
        print(f"\n1) Ouvre ce lien et connecte-toi avec ton compte Sandbox PERSONAL (buyer) :")
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
            # Le webhook a déjà pris l'order en charge. Son traitement (Shield + Gemini) peut durer
            # plusieurs secondes : on attend l'apparition d'un nouveau verdict au lieu de dormir 1 s.
            print("Order déjà pris en charge par le webhook. En attente du verdict…")
            new = None
            for _ in range(40):
                time.sleep(1)
                try:
                    current = c.get("/shield/verdicts").json()
                except (httpx.HTTPError, ValueError):
                    continue
                fresh = [v for v in current if (v["timestamp"], v["transaction_id"]) not in known]
                if fresh:
                    new = fresh[0]
                    break
            if new is None:
                print("Aucun nouveau verdict après 40 s. Regarde les logs Render (POST /webhooks/paypal) :")
                print("webhook non livré, erreur de traitement ou service redémarré (stockage en mémoire).")
                return 1
            show("Verdict Shield (via webhook)", new)
            print(f"\nAuthorization ID : {new['transaction_id']}")
            if new["action_taken"] == "held":
                print("Fonds retenus (autorisation non capturée). Clique sur « Unlock and validate » pour capturer réellement.")
            else:
                print("Vérifie l'activité sur https://www.sandbox.paypal.com/business/")
            print(f"\nDashboard : {BASE_URL}/dashboard")
            return 0

        if r.status_code != 200:
            show(f"Création refusée (HTTP {r.status_code})", r.json())
            return 0 if args.dup_invoice else 1
        
        order = r.json()
        show("Order créé (intent=AUTHORIZE)", order)
        
        print(f"\n1) Ouvre ce lien et connecte-toi avec ton compte Sandbox PERSONAL (buyer) :")
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
            # Le webhook a déjà traité l'order - on récupère les résultats depuis les verdicts
            print("Order déjà traité par webhook. Récupération des résultats...")
            time.sleep(1)  # Petit délai pour laisser le temps au webhook de finir
            
            # Récupérer le dernier verdict
            verdicts = c.get("/shield/verdicts").json()
            if verdicts:
                last_verdict = verdicts[0]  # Le plus récent
                show("Verdict Shield (via webhook)", last_verdict)
                
                # Chercher dans PayPal refs si on a un authorization_id
                auth_id = last_verdict.get("transaction_id", "")
                if auth_id and auth_id != last_verdict.get("reason", ""):
                    print(f"\nAuthorization ID : {auth_id}")
                    print("Vérifie l'activité sur https://www.sandbox.paypal.com/business/")
            else:
                print("Aucun verdict trouvé. Le webhook n'a peut-être pas encore terminé.")
            
            print(f"\nDashboard : {BASE_URL}/dashboard")
            return 0
        
        if r.status_code != 200:
            show(f"Erreur (HTTP {r.status_code})", r.json())
            return 1
        
        res = r.json()
        show("Verdict Shield", res["verdict"])
        show("PayPal", res["paypal"])
        
        print(f"\nDashboard : {BASE_URL}/dashboard")
        
        if res["verdict"]["action_taken"] == "held":
            print("\nFonds retenus (autorisation non capturée). Clique sur « Unlock and validate » pour capturer réellement.")
        
        return 0

if __name__ == "__main__":
    sys.exit(main())
