#!/usr/bin/env bash
# Injecte une crise de trésorerie (3 factures impayées) : Ally passe en mode survie.
# Usage : bash scripts/crisis.sh   (SENTINEL_URL et SENTINEL_ADMIN_TOKEN exportés)
set -euo pipefail
URL="${SENTINEL_URL:-http://127.0.0.1:8000}"
curl -s -X POST "$URL/simulate/stress" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: ${SENTINEL_ADMIN_TOKEN:-}" \
  -d '{"unpaid_invoices": 3, "unpaid_amount": 2400}' > /dev/null
echo "Crise injectée."
