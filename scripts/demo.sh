#!/usr/bin/env bash
# Démo complète : trafic normal -> attaques -> crise de trésorerie
set -euo pipefail
URL="${SENTINEL_URL:-http://127.0.0.1:${PORT:-8000}}"
export SENTINEL_URL="$URL"

echo "1/3 Trafic business normal (Ally : churn de Bob, upsell d'Alice)"
python scripts/simulate_business.py
sleep 2
echo "2/3 Attaques (Shield : velocity, friendly fraud, facture anormale)"
python scripts/simulate_attack.py
sleep 2
echo "3/3 Crise de trésorerie (Ally : mode survie)"
curl -s -X POST "$URL/simulate/stress" -H "Content-Type: application/json" \
  -d '{"unpaid_invoices": 3, "unpaid_amount": 2400}' | python -m json.tool
echo "Dashboard: $URL/dashboard"
