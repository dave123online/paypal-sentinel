#!/usr/bin/env bash
# ============================================================
#  PayPal Sentinel — Initialisation de l'arborescence
#  À exécuter À LA RACINE du repo cloné :
#  bash init_sentinel.sh
# ============================================================

set -euo pipefail

echo "🛡️  Initialisation de PayPal Sentinel..."
echo "📂  Racine du projet : $(pwd)"
echo ""

# ------------------------------------------------------------
# 1. Répertoires
# ------------------------------------------------------------
mkdir -p app/webhooks
mkdir -p dashboard/src
mkdir -p dashboard/public
mkdir -p tests/scenarios
mkdir -p scripts
mkdir -p docs

# ------------------------------------------------------------
# 2. Fichiers (placeholders vides, non destructifs)
# ------------------------------------------------------------
files=(
  "app/__init__.py"
  "app/main.py"
  "app/config.py"
  "app/models.py"
  "app/paypal_client.py"
  "app/ai_engine.py"
  "app/shield.py"
  "app/ally.py"
  "app/webhooks/__init__.py"
  "app/webhooks/handlers.py"

  "dashboard/index.html"
  "dashboard/src/main.js"
  "dashboard/src/style.css"

  "tests/__init__.py"
  "tests/test_shield.py"
  "tests/test_ally.py"
  "tests/scenarios/velocity_attack.json"
  "tests/scenarios/friendly_fraud.json"
  "tests/scenarios/invoice_anomaly.json"

  "scripts/simulate_attack.py"
  "scripts/demo.sh"

  "requirements.txt"
  "README.md"
)

for file in "${files[@]}"; do
  mkdir -p "$(dirname "$file")"
  if [ ! -e "$file" ]; then
    : > "$file"
    printf "  ✅ créé   %s\n" "$file"
  else
    printf "  ↪️  déjà là %s\n" "$file"
  fi
done

# ------------------------------------------------------------
# 3. .gitignore (sécurité des secrets)
# ------------------------------------------------------------
if [ ! -f .gitignore ]; then
  cat > .gitignore <<'EOF'
# Secrets / environnement
.env
.env.local
.env.*.local
!.env.example

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
venv/
env/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.egg-info/
build/
dist/

# Node / dashboard
node_modules/
dashboard/dist/
.vite/

# OS / IDE
.DS_Store
Thumbs.db
.idea/
.vscode/
*.log

# Base locale (hackathon)
*.db
*.sqlite3
EOF
  echo "  ✅ créé   .gitignore"
fi

# ------------------------------------------------------------
# 4. .env.example (template SANS vraies clés)
# ------------------------------------------------------------
if [ ! -f .env.example ]; then
  cat > .env.example <<'EOF'
# === PayPal (Sandbox) ===
PAYPAL_CLIENT_ID=ton_client_id_sandbox
PAYPAL_CLIENT_SECRET=ton_client_secret_sandbox
PAYPAL_ENVIRONMENT=sandbox
PAYPAL_WEBHOOK_ID=ton_webhook_id

# === IA ===
OPENAI_API_KEY=ta_cle_openai
OPENAI_MODEL=gpt-4o-mini

# === Serveur ===
HOST=0.0.0.0
PORT=8000
EOF
  echo "  ✅ créé   .env.example"
fi

# ------------------------------------------------------------
# 5. Copie du template en .env LOCAL (ignoré par git)
# ------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  ✅ créé   .env (local, ignoré par git)"
fi

echo ""
echo "🎉  Structure prête !"
echo ""
echo "Prochaine étape :"
echo "  1. Remplir les fichiers avec le code envoyé"
echo "  2. Ne JAMAIS commiter .env"
echo "  3. git add . && git commit -m 'chore: init project structure'"
echo ""
