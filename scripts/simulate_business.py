"""Génère du trafic business réaliste pour nourrir Ally."""
import json
import time
import httpx
from datetime import datetime, timedelta

import os
BASE_URL = os.getenv("SENTINEL_URL", "http://127.0.0.1:8000")

# Scénario : On simule 20 transactions sur les 30 derniers jours
# Avec des clients récurrents et un qui a disparu (Churn !)
def generate_history():
    events = []
    now = datetime.utcnow()
    
    # Client Fidèle (Alice) - Achète souvent
    for i in range(10):
        events.append({
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "transaction_id": f"TX_ALICE_{i}",
            "amount": 45.00 + (i * 2), # Panier moyen en hausse
            "currency": "EUR",
            "buyer_email": "alice.loyal@example.com",
            "timestamp": (now - timedelta(days=30-i*2)).isoformat() + "Z",
            "raw_payload": {}
        })
        
    # Client "Churn" (Bob) - Achétait beaucoup, plus rien depuis 15 jours
    for i in range(5):
        events.append({
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "transaction_id": f"TX_BOB_{i}",
            "amount": 120.00,
            "currency": "EUR",
            "buyer_email": "bob.disparu@example.com",
            "timestamp": (now - timedelta(days=40-i*3)).isoformat() + "Z", # Il y a longtemps
            "raw_payload": {}
        })

    # Nouveaux clients (One-off)
    for i in range(5):
        events.append({
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "transaction_id": f"TX_NEW_{i}",
            "amount": 25.00,
            "currency": "EUR",
            "buyer_email": f"newbie{i}@example.com",
            "timestamp": (now - timedelta(days=i)).isoformat() + "Z",
            "raw_payload": {}
        })
        
    return sorted(events, key=lambda x: x["timestamp"])

def main():
    print("🚀 Envoi de l'historique business à Sentinel...")
    history = generate_history()
    
    with httpx.Client(timeout=60) as client:
        for evt in history:
            # On envoie via l'endpoint de simulation
            # Note: Le serveur va recalculer le timestamp, mais c'est ok pour la démo
            r = client.post(f"{BASE_URL}/simulate/transaction", json=evt)
            if r.status_code == 200:
                print(f"  ✅ {evt['buyer_email']} - {evt['amount']}€")
            else:
                print(f"  ❌ Erreur: {r.text}")
            time.sleep(0.2) # Petit délai pour voir les cartes apparaître dans le dashboard
            
    print("\n🎉 Historique chargé ! Regarde le dashboard, Ally devrait détecter le churn de Bob.")

if __name__ == "__main__":
    main()
