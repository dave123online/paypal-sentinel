const fmtTime = (v) => v ? new Date(v).toLocaleString("fr-FR") : "";
const fmtShort = (v) => v ? new Date(v).toLocaleTimeString("fr-FR") : "";
const esc = (s) => { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; };

// ─── Modal ───
function showModal(title, text) {
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-text").textContent = text;
    document.getElementById("modal-overlay").style.display = "flex";
}
document.getElementById("modal-close").onclick = () => document.getElementById("modal-overlay").style.display = "none";
document.getElementById("modal-overlay").onclick = (e) => { if (e.target.id === "modal-overlay") e.target.style.display = "none"; };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.getElementById("modal-overlay").style.display = "none"; });

// ─── Actions (Resolve / Dismiss) ───
// Routes protégées par ADMIN_TOKEN sur la démo hébergée : on le demande une fois par session.
async function adminPost(url) {
    const send = () => fetch(url, {
        method: "POST",
        headers: sessionStorage.getItem("adminToken") ? { "X-Admin-Token": sessionStorage.getItem("adminToken") } : {},
    });
    let r = await send();
    if (r.status === 401) {
        const t = prompt("Cette action modifie l'état de la démo : token admin requis.");
        if (t) { sessionStorage.setItem("adminToken", t); r = await send(); }
    }
    return r;
}
async function approveShield(txId) {
    const r = await adminPost(`/shield/approve/${encodeURIComponent(txId)}`);
    const d = await r.json().catch(() => ({}));
    if (r.ok) {
        const c = d.capture;
        if (c) {
            showModal(c.simulated ? "Fonds capturés (simulation)" : "Fonds capturés — PayPal Sandbox",
                `${c.call}\nStatut : ${c.status}${c.capture_id ? "\nCapture ID : " + c.capture_id : ""}\nMontant : ${c.amount} ${c.currency}`);
        }
    } else {
        const err = d.detail && d.detail.issue ? `${d.detail.issue} — ${d.detail.message}` : JSON.stringify(d.detail || d);
        showModal(r.status === 401 ? "Action non autorisée" : "Capture impossible", err);
    }
    await refresh();
}
async function acknowledgeShield(txId) {
    const r = await adminPost(`/shield/acknowledge/${encodeURIComponent(txId)}`);
    if (!r.ok) showModal(r.status === 401 ? "Action non autorisée" : "Erreur", JSON.stringify((await r.json().catch(() => ({}))).detail || ""));
    await refresh();
}
async function dismissAlly(sugId) {
    await adminPost(`/ally/dismiss/${encodeURIComponent(sugId)}`);
    await refresh();
}

// Données des cartes (évite d'injecter du texte dans des onclick inline : une apostrophe cassait le JS)
const shieldById = {};
const allyById = {};

// ─── Provenance : réel (sandbox) vs simulé ───
const SRC = {
    paypal_sandbox: ["PayPal Sandbox", "src-real"],
    paypal_webhook: ["Webhook PayPal", "src-webhook"],
    simulated: ["Simulé", "src-sim"],
};
function srcBadge(src) {
    const [label, cls] = SRC[src] || SRC.simulated;
    return `<span class="src-badge ${cls}">${label}</span>`;
}

// ─── Shield Feed Card ───
function shieldCard(v) {
    const lvl = v.threat_level || "low";
    shieldById[v.transaction_id] = v;
    let actions = "";
    if (v.action_taken === "held") {
        // Autorisation retenue : le marchand peut la capturer
        actions = `<div class="card-actions">
            <button class="btn-approve" data-act="approve" data-id="${esc(v.transaction_id)}">✓ Unlock and validate</button>
        </div>`;
    } else if (v.action_taken === "alerted" || v.action_taken === "blocked") {
        // Rien à capturer (refus PayPal, litige, autorisation annulée) : on acquitte seulement
        actions = `<div class="card-actions">
            <button class="btn-dismiss" data-act="ack" data-id="${esc(v.transaction_id)}">✓ Acknowledge</button>
        </div>`;
    }
    return `
    <div class="feed-card threat-${lvl}" data-kind="shield" data-id="${esc(v.transaction_id)}">
        <div class="fc-header">
            <span class="fc-badge badge-${lvl}">${lvl} · score ${v.risk_score}</span>
            ${srcBadge(v.source)}
            <span class="fc-time">${fmtShort(v.timestamp)}</span>
        </div>
        <div class="fc-title">${esc(v.reason)}</div>
        <div class="fc-desc">Action : <strong>${esc(v.action_taken)}</strong> · ${esc(v.explanation_for_merchant || "")}</div>
        ${actions}
    </div>`;
}

// ─── Ally Feed Card ───
const allyClass = { revenue_opportunity: "ally-revenue", cash_flow_alert: "ally-cashflow", customer_retention: "ally-retention", operational_improvement: "ally-ops" };
function allyCard(s) {
    allyById[s.suggestion_id] = s;
    return `
    <div class="feed-card ${allyClass[s.suggestion_type] || ""}" data-kind="ally" data-id="${esc(s.suggestion_id)}">
        <div class="fc-header">
            <span class="fc-badge badge-low">${esc(s.suggestion_type.replaceAll("_", " "))}</span>
            <span class="fc-time">${fmtShort(s.timestamp)}</span>
        </div>
        <div class="fc-title">${esc(s.title)}</div>
        <div class="fc-desc">${esc(s.description)}</div>
        <div class="fc-action">→ ${esc(s.action)} · impact estimé ≈ ${s.estimated_impact_eur} € (heuristique)</div>
        <div class="card-actions">
            <button class="btn-dismiss" data-act="dismiss" data-id="${esc(s.suggestion_id)}">✓ Mark as read</button>
        </div>
    </div>`;
}

// ─── Délégation de clics (cartes + boutons) ───
document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) {
        e.stopPropagation();
        if (btn.dataset.act === "approve") approveShield(btn.dataset.id);
        if (btn.dataset.act === "ack") acknowledgeShield(btn.dataset.id);
        if (btn.dataset.act === "dismiss") dismissAlly(btn.dataset.id);
        return;
    }
    const card = e.target.closest(".feed-card");
    if (!card) return;
    if (card.dataset.kind === "shield") {
        const v = shieldById[card.dataset.id];
        if (v) showModal(`Shield — ${v.transaction_id}`, v.explanation_for_merchant || v.reason);
    } else if (card.dataset.kind === "ally") {
        const s = allyById[card.dataset.id];
        if (s) showModal(`Ally — ${s.title}`, `${s.description}\n\nAction : ${s.action}\nImpact estimé (heuristique, non observé) : ${s.estimated_impact_eur} €`);
    }
});

// ─── AG Grid setup ───
const gridOpts = { defaultColDef: { sortable: true, resizable: true }, animateRows: false, suppressColumnVirtualisation: true };
const shieldApi = agGrid.createGrid(document.getElementById("shieldGrid"), { ...gridOpts, columnDefs: [
    { field: "timestamp", headerName: "Timestamp", valueFormatter: (p) => fmtTime(p.value), width: 170 },
    { field: "transaction_id", headerName: "Transaction", width: 200 },
    { field: "threat_level", headerName: "Threat", width: 100 },
    { field: "risk_score", headerName: "Risk", width: 80 },
    { field: "action_taken", headerName: "Action", width: 110 },
    { field: "source", headerName: "Source", width: 150, cellRenderer: (p) => srcBadge(p.value) },
    { field: "reason", headerName: "Reason", flex: 1 },
    { field: "explanation_for_merchant", headerName: "Explanation", flex: 2 },
], rowData: [], onCellClicked: (e) => { if (e.value) showModal(e.colDef.headerName, String(e.value)); } });

const allyApi = agGrid.createGrid(document.getElementById("allyGrid"), { ...gridOpts, columnDefs: [
    { field: "timestamp", headerName: "Timestamp", valueFormatter: (p) => fmtTime(p.value), width: 170 },
    { field: "suggestion_type", headerName: "Type", width: 190 },
    { field: "title", headerName: "Title", flex: 1 },
    { field: "description", headerName: "Description", flex: 2 },
    { field: "estimated_impact_eur", headerName: "Impact estimé €", width: 130 },
    { field: "action", headerName: "Action", width: 200 },
], rowData: [], onCellClicked: (e) => { if (e.value) showModal(e.colDef.headerName, String(e.value)); } });

// ─── Refresh loop ───
let lastV = null, lastA = null;
async function refresh() {
    try {
        const [v, s, h, m] = await Promise.all([
            fetch("/shield/verdicts").then(r => r.json()),
            fetch("/ally/suggestions").then(r => r.json()),
            fetch("/health").then(r => r.json()),
            fetch("/merchant/state").then(r => r.json()),
        ]);
        document.getElementById("kpi-protected").textContent = `${Math.round(m.amount_held_or_blocked ?? m.money_saved_by_shield).toLocaleString("fr-FR")} €`;
        document.getElementById("kpi-alerts").textContent = m.active_alerts;
        document.getElementById("kpi-transactions").textContent = m.transactions_analyzed;
        document.getElementById("kpi-ally").textContent = m.ally_opportunities;
        const dot = document.getElementById("gemini-status");
        dot.className = "status-dot " + (h.gemini_working ? "ok" : "err");
        document.getElementById("paypal-status").className = "status-dot " + (h.paypal_working ? "ok" : "err");
        document.getElementById("status-text").textContent = `MAJ ${new Date().toLocaleTimeString("fr-FR")} · Gemini ${h.gemini_working ? "✓" : "✗"} · PayPal ${h.paypal_working ? "✓" : "✗"}`;

        const vj = JSON.stringify(v), sj = JSON.stringify(s);
        if (vj !== lastV) {
            const shieldActive = v.filter(x => x.action_taken !== "approved" && !x.acknowledged);
            document.getElementById("shield-feed").innerHTML = shieldActive.length ? shieldActive.map(shieldCard).join("") : '<div class="empty-state">Aucune alerte — tout est calme 🟢</div>';
            shieldApi.setGridOption("rowData", v); lastV = vj;
        }
        if (sj !== lastA) {
            document.getElementById("ally-feed").innerHTML = s.length ? s.map(allyCard).join("") : '<div class="empty-state">Aucune suggestion Ally pour l\'instant</div>';
            allyApi.setGridOption("rowData", s); lastA = sj;
        }
    } catch (e) {
        document.getElementById("status-text").textContent = "API injoignable";
    }
}
refresh();
setInterval(refresh, 3000);
