# 🛡️ PayPal Sentinel

**Démo en ligne :** https://paypal-sentinel.onrender.com (dashboard : `/dashboard`, docs API : `/docs`) · PayPal **Sandbox**, aucun argent réel.

Agent IA de protection et de croissance pour marchands PayPal : **Shield** détecte la fraude avec des règles déterministes, **Gemini** l'explique avec empathie, **Ally** propose la prochaine action business.

## Problème résolu
Les petits marchands subissent fraude par cartes test, faux litiges (friendly fraud) et anomalies de factures, sans outil clair ni rassurant. Sentinel retient la transaction, explique pourquoi en langage humain et laisse le marchand décider.

## Vision
- **Shield** (protection, transparence, contrôle) : règles déterministes (velocity attack, friendly fraud, anomalie de facture). Gemini ne décide jamais : il explique avec empathie. Une transaction normale n'appelle pas Gemini et n'apparaît pas dans le feed. Chaque alerte a un bouton **Unlock and validate** (le marchand a le dernier mot : capture PayPal Sandbox réelle quand l'autorisation vient d'un vrai order, simulée pour les transactions de démo).
- **Ally** (CFO virtuel, découplé de Shield, jamais de fraude) : analyse les 30 dernières transactions. Quatre compétences : rétention/churn, upsell, survie de trésorerie (dry spell), factures impayées/opérationnel. Throttlé (une analyse toutes les 3 transactions) et dédupliqué. Bouton **Mark as read**.
- **Command Center** : KPI (argent protégé, alertes actives, transactions analysées, opportunités Ally), feeds Shield/Ally, tables AG Grid avec modales, statut Gemini (cache 60 s).

## Architecture
```
Webhook PayPal / simulation
        │
        ▼
   FastAPI ── asyncio.gather ──┬─► Shield (règles) ──► Gemini (explication empathique)
                               └─► Ally (historique + analytics) ──► Gemini (suggestion)
                                          │
                              stockage mémoire ──► Dashboard (KPI + feeds + AG Grid)
```
Gemini ne décide jamais du blocage : seul Shield décide.

## Outils
PayPal API (Sandbox) · Google Gemini (AI Studio) · FastAPI · AG Grid · Render

## Installation
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis remplir les clés
```

## Lancer
```bash
uvicorn app.main:app --reload
```
- API : http://paypal-sentinel.onrender.com · Docs : /docs · Dashboard : /dashboard

## Démo complète
```bash
bash scripts/demo.sh        # trafic normal -> attaques -> crise de trésorerie
# ou séparément :
python scripts/simulate_business.py   # Ally : churn de Bob, upsell d'Alice
python scripts/simulate_attack.py     # Shield : 3 scénarios de fraude
curl -X POST localhost:8000/simulate/stress -H "Content-Type: application/json" \
  -d '{"unpaid_invoices": 3, "unpaid_amount": 2400}'   # Ally : mode survie
pytest
```
Sans `GOOGLE_API_KEY`, des fallbacks déterministes sont utilisés : la démo fonctionne quand même.

## Endpoints
`GET /health` · `GET /merchant/state` · `POST /paypal/orders` · `POST /paypal/orders/{id}/authorize` · `POST /simulate/transaction` · `POST /simulate/stress` · `POST /webhooks/paypal` · `GET /shield/verdicts` · `POST /shield/approve/{tx}` · `GET /ally/suggestions` · `POST /ally/dismiss/{id}` · `GET /dashboard`

## Intégration PayPal (Sandbox)
Flux réel, `intent=AUTHORIZE` : Sentinel réserve les fonds, Shield décide, puis capture ou retient.

```
POST /paypal/orders ──► buyer approuve (compte Sandbox Personal)
        │                         │ webhook CHECKOUT.ORDER.APPROVED (ou appel manuel)
        ▼                         ▼
   order créé            POST /paypal/orders/{id}/authorize ──► Shield
                                                                │
              approved ─► capture réelle   held ─► autorisation gardée, bouton "Unlock" = capture réelle
              blocked  ─► void             refus PayPal ─► alerte Shield
```
- `app/paypal_client.py` : OAuth2, Orders v2 (create/authorize), Payments v2 (capture/void), vérification de signature webhook, `check_credentials` (pastille PayPal du dashboard).
- **Negative testing** : en-tête `PayPal-Mock-Response` (`INSTRUMENT_DECLINED`, `DUPLICATE_INVOICE_ID`), traité par Shield.
- Les webhooks de capture déjà traitées via l'API sont ignorés (pas de double comptage).
- Webhooks : `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `CHECKOUT.ORDER.APPROVED`, `CUSTOMER.DISPUTE.CREATED`, `INVOICING.INVOICE.PAID`. Test local : `ngrok http 8000`, URL à enregistrer dans PayPal Developer (`PAYPAL_WEBHOOK_ID`).
- Les signaux de risque que PayPal ne fournit pas (micro-paiements, tracking) sont simulés via `signals` (sandbox uniquement) ou `sentinel_signals` (webhook).

### Démo sandbox réelle
Prérequis : Client ID/Secret Sandbox dans `.env`, serveur lancé, un compte Sandbox **Personal** (buyer) et un **Business** (seller).
```bash
python scripts/sandbox_checkout.py                      # paiement normal -> capturé automatiquement
python scripts/sandbox_checkout.py --amount 500 --hold  # Shield retient -> "Unlock" = vraie capture
python scripts/sandbox_checkout.py --decline            # INSTRUMENT_DECLINED -> alerte Shield
python scripts/sandbox_checkout.py --block              # rafale de tests de carte -> blocked, autorisation annulée (void)
python scripts/sandbox_checkout.py --dup-invoice        # DUPLICATE_INVOICE_ID
```
Vérifie ensuite le paiement dans l'activité du compte Business sur www.sandbox.paypal.com.

## Intégration IA
`app/ai_engine.py` appelle Gemini (`GOOGLE_MODEL`, sortie JSON) pour (1) reformuler la décision Shield en message empathique, (2) rédiger la suggestion Ally. Les montants et types sont validés côté serveur.

## Déploiement Render
- Build : `pip install -r requirements.txt`
- Start : `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Variables : `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_ENVIRONMENT`, `PAYPAL_WEBHOOK_ID`, `GOOGLE_API_KEY`, `GOOGLE_MODEL`, `APP_BASE_URL` (URL publique Render, utilisée comme `return_url` PayPal)

## Limites et choix de conception (à lire avant de juger)
- **Signaux de risque simulés.** PayPal ne fournit pas `recent_micro_payments`, `tracking_delivered` ni `average_invoice_amount`. Sentinel démontre une architecture de décision qui *peut* exploiter ces signaux ; en démo ils viennent des scénarios (`tests/scenarios`) ou de `signals` (sandbox uniquement). Les badges « PayPal Sandbox / Webhook / Simulé » montrent ce qui est réel.
- **Quatre états Shield, tous testés :** `approved` (capture), `held` (autorisation gardée, le marchand peut capturer), `alerted` (refus PayPal ou litige : rien à capturer, le marchand acquitte), `blocked` (rafale de 15+ micro-paiements avant un montant ≥ 300 € : l'autorisation est annulée par void et ne peut plus être débloquée).
- **KPI « Montant retenu / bloqué ».** Somme des montants des transactions retenues ou bloquées : c'est l'exposition traitée, pas une perte évitée mesurée. Même logique pour « impact estimé » d'Ally : heuristique (valeur de panier, factures impayées), jamais un résultat observé.
- **Sécurité de la démo hébergée.** Définir `ADMIN_TOKEN` protège les routes qui modifient l'état (`/simulate/*`, `/paypal/orders*`, validations, et le webhook quand `PAYPAL_WEBHOOK_ID` est vide). Le dashboard (lecture) reste public ; les boutons demandent le token une fois par session. Les scripts le lisent dans `SENTINEL_ADMIN_TOKEN`. Les webhooks PayPal sont protégés par la vérification de signature. Version de démonstration : stockage en mémoire, un seul marchand, pas d'authentification utilisateur. Ne pas la déployer telle quelle pour de vrais marchands.
- **Données envoyées à l'IA.** Gemini ne reçoit ni e-mail acheteur ni prénom : les clients sont remplacés par un identifiant dérivé non réversible (`Customer-xxxxxx`), remis en prénom localement dans l'interface. Seuls les montants, dates et statuts nécessaires à la recommandation sont transmis.

## Licence
MIT — voir `LICENSE`.
