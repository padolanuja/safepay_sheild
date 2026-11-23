/* Dashboard: history with decision badges + filters, decisions over time, top warning
   signs, and the feedback queue (human review only; nothing is retrained). */
(function () {
  const $ = s => document.querySelector(s);
  const ICON = { ALLOW: "✓", WARN: "!", HOLD: "■" };
  const badge = d => d ? `<span class="badge b-${d}">${ICON[d]} ${SP.esc(SP.t("decision_short." + d))}</span>` : "—";
  let fbByAssessment = {};

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

    // decisions over time: stacked columns per day (status colours + legend)
    if (!sum.by_day.length) $("#overTime").innerHTML = `<p class="empty">${SP.esc(SP.t("dash.empty"))}</p>`;
    else {
      const max = Math.max(...sum.by_day.map(d => d.ALLOW + d.WARN + d.HOLD));
      $("#overTime").innerHTML = `<div class="stack-chart">${sum.by_day.slice(-14).map(d =>
        `<div class="stack-col" title="${d.day}: ALLOW ${d.ALLOW}, WARN ${d.WARN}, HOLD ${d.HOLD}">${["ALLOW", "WARN", "HOLD"].filter(k => d[k])
          .map(k => `<span class="seg s-${k}" style="height:${(100 * d[k] / max).toFixed(1)}%"></span>`).join("")}</div>`).join("")}</div>
        <div class="stack-labels">${sum.by_day.slice(-14).map(d => `<span>${d.day.slice(5)}</span>`).join("")}</div>
        <div class="legend">${["ALLOW", "WARN", "HOLD"].map(k => `<span><i class="sw seg s-${k}"></i>${ICON[k]} ${SP.esc(SP.t("decision_short." + k))}</span>`).join("")}</div>`;
    }

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
