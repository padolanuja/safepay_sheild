/* Dashboard: history with decision badges + filters, decisions over time, top warning
   signs, and the feedback queue (human review only; nothing is retrained). */
(function () {
  const $ = s => document.querySelector(s);
  const ICON = { ALLOW: "✓", WARN: "!", HOLD: "■" };
  const badge = d => d ? `<span class="badge b-${d}">${ICON[d]} ${SP.esc(SP.t("decision_short." + d))}</span>` : "—";
  let fbByAssessment = {};
  const DECISIONS = ["ALLOW", "WARN", "HOLD"];
  const label = d => SP.esc(SP.t("decision_short." + d));

  // Decision mix: donut (status colours, 2px surface gaps) + legend rows with icon, label, count and share.
  function donut(by, tot) {
    const R = 62, C = 2 * Math.PI * R, gap = DECISIONS.filter(d => by[d]).length > 1 ? 3 : 0;
    let off = 0;
    const arcs = DECISIONS.filter(d => by[d]).map(d => {
      const len = C * by[d] / tot, dash = Math.max(len - gap, 1);
      const tip = `<b>${ICON[d]} ${label(d)}</b><br>${by[d]} of ${tot} checks · ${SP.pct(by[d] / tot)}`;
      const s = `<circle class="arc a-${d}" r="${R}" cx="80" cy="80" stroke-dasharray="${dash.toFixed(2)} ${(C - dash).toFixed(2)}"
        stroke-dashoffset="${(-off).toFixed(2)}" data-tip="${SP.esc(tip)}"></circle>`;
      off += len; return s;
    }).join("");
    return `<div class="mix">
      <svg class="donut" viewBox="0 0 160 160" role="img" aria-label="Decision mix: ${DECISIONS.map(d => `${d} ${by[d]}`).join(", ")}">
        <circle class="arc-track" r="${R}" cx="80" cy="80"></circle>
        <g transform="rotate(-90 80 80)">${arcs}</g>
        <text x="80" y="78" class="d-total">${tot}</text><text x="80" y="98" class="d-sub">checks</text>
      </svg>
      <ul class="mix-legend">${DECISIONS.map(d => `<li><i class="sw d-${d}"></i><span class="ml-label">${ICON[d]} ${label(d)}</span>
        <b>${by[d]}</b><span class="ml-pct">${SP.pct(by[d] / tot)}</span>
        <span class="ml-bar"><span class="d-${d}" style="width:${(100 * by[d] / tot).toFixed(1)}%"></span></span></li>`).join("")}</ul>
    </div>`;
  }

  // Risk score per check: one mark per assessment (oldest → newest), y = coercion score.
  // Shape and colour both encode the decision (circle ALLOW, diamond WARN, square HOLD).
  function timeline(rec) {
    const W = 640, H = 210, L = 40, Rt = 12, T = 12, B = 30, n = rec.length;
    const x = i => n === 1 ? (L + W - Rt) / 2 : L + 10 + i * (W - L - Rt - 20) / (n - 1);
    const y = v => T + (1 - v) * (H - T - B);
    const grid = [0, .25, .5, .75, 1].map(v => `<line x1="${L}" x2="${W - Rt}" y1="${y(v)}" y2="${y(v)}" class="${v ? "g" : "g base"}"></line>
      <text x="${L - 8}" y="${y(v) + 4}" class="tk" text-anchor="end">${v * 100}%</text>`).join("");
    const shape = (d, cx, cy) => d === "HOLD" ? `<rect x="${cx - 6}" y="${cy - 6}" width="12" height="12" rx="2"></rect>`
      : d === "WARN" ? `<path d="M${cx} ${cy - 8}L${cx + 8} ${cy}L${cx} ${cy + 8}L${cx - 8} ${cy}Z"></path>` : `<circle cx="${cx}" cy="${cy}" r="6.5"></circle>`;
    const when = s => s.replace("T", " ").slice(5, 16);
    const marks = rec.map((a, i) => {
      const v = a.coercion_score ?? 0, cx = x(i), cy = y(v);
      const tip = `<b>${ICON[a.decision]} ${label(a.decision)}</b> · risk ${SP.pct(v)}<br>${SP.esc(when(a.created_at))} · ${SP.esc(SP.t(a.stage === "post_pause" ? "why.after_answers" : "why.first_check"))}<br>${SP.esc(a.payee)} · ${SP.esc(SP.inr(a.amount))}`;
      return `<g class="mk m-${a.decision}" data-tip="${SP.esc(tip)}" tabindex="0"><rect class="hit" x="${cx - 9}" y="${T}" width="18" height="${H - T - B}"></rect>${shape(a.decision, cx, cy)}</g>`;
    }).join("");
    const xl = `<text x="${x(0)}" y="${H - 8}" class="tk" text-anchor="${n === 1 ? "middle" : "start"}">${SP.esc(when(rec[0].created_at))}</text>` +
      (n > 1 ? `<text x="${x(n - 1)}" y="${H - 8}" class="tk" text-anchor="end">${SP.esc(when(rec[n - 1].created_at))}</text>` : "");
    return `<svg class="timeline" viewBox="0 0 ${W} ${H}" role="img" aria-label="Coercion risk score of the last ${n} checks">${grid}${marks}${xl}</svg>
      <div class="legend">${DECISIONS.map(d => `<span><svg class="lg-mk m-${d}" viewBox="0 0 16 16">${shape(d, 8, 8)}</svg>${ICON[d]} ${label(d)}</span>`).join("")}
        <span class="muted">Last ${n} checks, oldest → newest. Hover a mark for details.</span></div>`;
  }

  function userOptions() {
    const sel = $("#fUser"), cur = sel.value;
    sel.innerHTML = `<option value="">${SP.esc(SP.t("dash.filter_all"))}</option>` +
      SP.users.map(u => `<option value="${u.id}">${SP.esc(u.name)}</option>`).join("");
    sel.value = cur;
  }

  async function load() {
    const uid = $("#fUser").value, dec = $("#fDecision").value;
    const sum = await SP.api("GET", "/api/dashboard/summary" + (uid ? `?user_id=${uid}` : ""));
    const tile = (cls, label, value, sub = "") => `<div class="tile ${cls}"><div class="t-label">${label}</div><div class="t-value">${value}</div><div class="t-sub">${sub}</div></div>`;
    const tot = sum.assessments || 0;
    $("#tiles").innerHTML =
      tile("t-key", "Assessments", tot, SP.t("dash.latency") + ": " + (sum.avg_latency_ms ?? "—") + " ms") +
      ["ALLOW", "WARN", "HOLD"].map(d => tile("t-" + d, `${ICON[d]} ${SP.esc(SP.t("decision." + d))}`, sum.by_decision[d],
        tot ? SP.pct(sum.by_decision[d] / tot) : "")).join("");

    $("#mix").innerHTML = tot ? donut(sum.by_decision, tot) : `<p class="empty">${SP.esc(SP.t("dash.empty"))}</p>`;
    $("#overTime").innerHTML = sum.recent && sum.recent.length ? timeline(sum.recent) : `<p class="empty">${SP.esc(SP.t("dash.empty"))}</p>`;

    // top indicators
    const ti = sum.top_indicators;
    $("#topInd").innerHTML = ti.length ? `<ul class="hbars">${ti.map(i => `<li><span>${SP.esc(SP.has("rule." + i.id) ? SP.t("rule." + i.id) : i.id)}</span>
      <span class="bar" style="width:${(100 * i.count / ti[0].count).toFixed(0)}%"></span><span class="val">${i.count}</span></li>`).join("")}</ul>`
      : `<p class="empty">${SP.esc(SP.t("dash.empty"))}</p>`;

    // history (assessments) for the selected payer(s)
    const users = uid ? SP.users.filter(u => u.id === Number(uid)) : SP.users;
    const fb = (await SP.api("GET", "/api/feedback")).feedback;
    fbByAssessment = Object.fromEntries(fb.map(f => [f.assessment_id, f]));
    const rows = [];
    for (const u of users) {
      const h = await SP.api("GET", `/api/history/${u.id}?limit=200${dec ? "&decision=" + dec : ""}`);
      h.assessments.forEach(a => rows.push({ ...a, payer: u.name }));
    }
    rows.sort((a, b) => b.id - a.id);
    $("#histTable").innerHTML = rows.length ? `<thead><tr><th>Time</th><th>Payer</th><th>Payee</th><th class="num">Amount</th><th>Check</th><th>Decision</th><th>Outcome</th><th>Reason</th><th>Feedback</th></tr></thead><tbody>` +
      rows.slice(0, 150).map(a => `<tr><td>${a.created_at.replace("T", " ").slice(5, 16)}</td><td>${SP.esc(a.payer)}</td>
        <td><code>${SP.esc(a.payee_vpa_masked)}</code></td><td class="num">${SP.inr(a.amount)}</td>
        <td class="small">${SP.esc(SP.t(a.stage === "post_pause" ? "why.after_answers" : "why.first_check"))}${a.scenario_id ? ` · S${a.scenario_id}` : ""}</td>
        <td>${badge(a.decision)}</td><td class="small">${a.outcome || "—"}</td><td class="small">${SP.esc(a.reason)}</td>
        <td>${fbByAssessment[a.id] ? `<span class="small">${fbByAssessment[a.id].outcome}${fbByAssessment[a.id].verified ? " ✓" : ""}</span>` :
          `<div class="fb-actions">${["was_scam", "was_genuine", "rescued"].map(o => `<button class="btn btn-sm btn-outline" data-fb="${a.id}" data-o="${o}">${SP.esc(SP.t({ was_scam: "btn.mark_scam", was_genuine: "btn.mark_genuine", rescued: "btn.mark_rescued" }[o]))}</button>`).join("")}</div>`}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">${SP.esc(SP.t("dash.empty"))}</td></tr></tbody>`;
    document.querySelectorAll("[data-fb]").forEach(b => b.addEventListener("click", async () => {
      try { await SP.api("POST", "/api/feedback", { assessment_id: Number(b.dataset.fb), outcome: b.dataset.o, submitted_by: "analyst" }); load(); }
      catch (e) { SP.toast(e.message, "error"); }
    }));

    // feedback queue
    $("#fbTable").innerHTML = fb.length ? `<thead><tr><th>#</th><th>Assessment</th><th>Decision</th><th class="num">Amount</th><th>Outcome</th><th>By</th><th>Note</th><th>Verified</th></tr></thead><tbody>` +
      fb.map(f => `<tr><td>${f.id}</td><td>${f.assessment_id} · <code>${SP.esc(f.payee_vpa_masked)}</code></td><td>${badge(f.decision)}</td>
        <td class="num">${SP.inr(f.amount)}</td><td>${f.outcome}</td><td>${f.submitted_by}</td><td class="small">${SP.esc(f.note || "")}</td>
        <td>${f.verified ? "✓" : `<button class="btn btn-sm btn-primary" data-verify="${f.id}">${SP.esc(SP.t("btn.verify"))}</button>`}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">No feedback yet.</td></tr></tbody>`;
    document.querySelectorAll("[data-verify]").forEach(b => b.addEventListener("click", async () => {
      await SP.api("POST", `/api/feedback/${b.dataset.verify}/verify`); load();
    }));
  }

  (async function boot() {
    await SP.ready;
    userOptions();
    $("#fUser").addEventListener("change", load);
    $("#fDecision").addEventListener("change", load);
    SP.on("lang", () => { userOptions(); load(); });
    load().catch(e => SP.toast(e.message, "error"));
  })();
})();
