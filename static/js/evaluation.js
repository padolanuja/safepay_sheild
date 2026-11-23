/* Evaluation screen: renders reports/*.json via GET /api/metrics. Read-only. */
(function () {
  const $ = s => document.querySelector(s);
  const pct = (x, d = 1) => SP.pct(x, d);
  const missing = (name, script) => `<section class="panel" style="margin-bottom:18px"><h2 class="section">${name}</h2>
    <p class="caveat">Not generated yet. Run <code>python ${script}</code>.</p></section>`;

  function system(e) {
    const h = e.headline, A = e.approaches, L = e.latency;
    const rows = ["rule_new_payee_5x", "rule_new_unknown_call", "mule_only", "coercion_only", "combined"];
    const tiles = `<div class="tiles">
      <div class="tile t-key"><div class="t-label">Coerced payments to clean payees missed by mule-only</div>
        <div class="t-value">${h.missed_by_mule_only} / ${h.coerced_to_clean_payees}</div><div class="t-sub">receiver-side check alone</div></div>
      <div class="tile t-ALLOW"><div class="t-label">…of those caught by SafePay Shield</div>
        <div class="t-value">${h.of_those_caught_by_combined_pct}%</div><div class="t-sub">${h.of_those_caught_by_combined} WARN or HOLD · ${h.of_those_held_by_combined_pct}% held</div></div>
      <div class="tile t-ALLOW"><div class="t-label">Coerced payments to payees with no history caught</div>
        <div class="t-value">${h.no_history_caught_by_combined_pct}%</div><div class="t-sub">n = ${h.coerced_to_payees_with_no_history}</div></div>
      <div class="tile t-key"><div class="t-label">/api/assess response time</div>
        <div class="t-value">${L.wall_ms.p50} ms</div><div class="t-sub">p50 · p95 ${L.wall_ms.p95} ms · ${L.calls} calls, in-process</div></div>
    </div>`;
    const table = `<div class="table-wrap"><table><thead><tr><th>Approach</th><th class="num">Recall</th><th class="num">Precision</th><th class="num">F1</th>
      <th class="num">False-positive rate</th><th class="num">FP on hard negatives</th><th class="num">HOLD recall</th><th class="num">HOLD FP hard neg.</th>
      <th class="num">ALLOW / WARN / HOLD</th></tr></thead><tbody>${rows.map(k => {
        const a = A[k], i = a.intervene, hd = a.hold, s = a.decision_share;
        return `<tr class="${k === "combined" ? "highlight" : ""}"><td>${SP.esc(a.name)}</td><td class="num">${pct(i.recall)}</td><td class="num">${pct(i.precision)}</td>
          <td class="num">${i.f1.toFixed(3)}</td><td class="num">${pct(i.fp_rate)}</td><td class="num">${pct(i.fp_rate_hard_negatives)}</td>
          <td class="num">${k.startsWith("rule") || k === "mule_only" ? "—" : pct(hd.recall)}</td>
          <td class="num">${k.startsWith("rule") || k === "mule_only" ? "—" : pct(hd.fp_rate_hard_negatives)}</td>
          <td class="num">${pct(s.ALLOW, 0)} / ${pct(s.WARN, 0)} / ${pct(s.HOLD, 0)}</td></tr>`;
      }).join("")}</tbody></table></div>
      <p class="muted small">"Intervene" = WARN or HOLD. Ground truth: coerced payment OR payee is an RBIH-labelled mule.
      Joint set: ${e.joint_set.rows.toLocaleString("en-IN")} held-out rows; ${e.joint_set.should_intervene} should be stopped (seed ${e.joint_set.seed}).</p>`;
    const charts = (e.charts || []).map(c => `<figure style="margin:0 0 14px"><img class="chart-img" src="/reports/${c}" alt="${c.replace(/[_.]/g, " ")}" loading="lazy"></figure>`).join("");
    const lims = (e.known_limitations || []).map(l => `<div class="limit"><strong>Known limitation</strong><p>${SP.esc(l.finding)}</p>
      <p class="small"><b>Cause:</b> ${SP.esc(l.cause)}</p><p class="small"><b>Planned fix:</b> ${SP.esc(l.planned_fix)}</p></div>`).join("");
    const scam = A.combined.recall_by_scam_type, hn = A.combined.fp_rate_by_hard_negative;
    const breakdown = `<div class="grid-2">
      <div><h3 class="why-h">Recall by scam type (SafePay Shield)</h3><table><thead><tr><th>Scam type</th><th class="num">WARN or HOLD</th><th class="num">HOLD</th><th class="num">n</th></tr></thead>
      <tbody>${Object.entries(scam).filter(([k]) => k !== "none").map(([k, v]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="num">${pct(v.intervene)}</td><td class="num">${pct(v.hold)}</td><td class="num">${v.n}</td></tr>`).join("")}</tbody></table></div>
      <div><h3 class="why-h">False positives by hard-negative type (SafePay Shield)</h3><table><thead><tr><th>Genuine payment type</th><th class="num">WARN or HOLD</th><th class="num">HOLD</th><th class="num">n</th></tr></thead>
      <tbody>${Object.entries(hn).map(([k, v]) => `<tr><td>${k.replace(/_/g, " ")}</td><td class="num">${pct(v.intervene)}</td><td class="num">${pct(v.hold)}</td><td class="num">${v.n}</td></tr>`).join("")}</tbody></table></div></div>`;
    return `<section class="panel" style="margin-bottom:18px"><h2 class="section">System comparison — same joint test set</h2>
      <p class="caveat">${SP.esc(e.caveat)}<br>Source: <code>scripts/evaluate_system.py</code> → <code>reports/evaluation.json</code> (${e.generated_at}).</p>
      ${tiles}${table}${lims}</section>
      <section class="panel" style="margin-bottom:18px"><h2 class="section">Charts</h2>${charts}${breakdown}
      <p class="muted small">Not covered by this evaluation: ${e.not_covered.join("; ")}.</p></section>`;
  }

  function mule(m) {
    if (m.status) return missing("Mule model on RBIH data", "scripts/evaluate_mule_rbih.py");
    const k = m.metrics_at_threshold;
    return `<section class="panel"><h2 class="section">Mule model (receiver side) — RBIH-NFPC Phase 2</h2>
      <p class="caveat">⚠ Metrics ${SP.esc(m.caveat)}. ${SP.esc(m.caveat_detail)}</p>
      <div class="tiles">
        <div class="tile t-key"><div class="t-label">ROC-AUC / PR-AUC</div><div class="t-value">${m.roc_auc} / ${m.pr_auc}</div></div>
        <div class="tile"><div class="t-label">Recall at ${m.threshold}</div><div class="t-value">${pct(k.recall)}</div><div class="t-sub">${k.tp} of ${k.tp + k.fn} mules</div></div>
        <div class="tile"><div class="t-label">Precision at ${m.threshold}</div><div class="t-value">${pct(k.precision)}</div><div class="t-sub">FP rate ${pct(k.false_positive_rate)}</div></div>
      </div>
      <p class="small muted">Sample: ${m.sample.accounts.toLocaleString("en-IN")} labelled accounts (${m.sample.mules} mules), ${m.sample.transactions.toLocaleString("en-IN")} transactions from ${m.sample.parts.length} parquet parts. Source: <code>reports/rbih_mule_eval.json</code>.</p></section>`;
  }

  function coercion(card) {
    if (card.status) return missing("Coercion model", "scripts/train_coercion_model.py");
    const st = card.stages;
    return `<section class="panel"><h2 class="section">Coercion model (payer side)</h2>
      <p class="caveat">${SP.esc(card.caveat)} · trained on <code>${SP.esc(card.data_source)}</code>; payee-side columns excluded.</p>
      <table><thead><tr><th>Stage</th><th>Model</th><th class="num">Test ROC-AUC</th><th class="num">WARN cut-off</th><th class="num">HOLD cut-off</th></tr></thead><tbody>
      ${Object.entries(st).map(([k, v]) => `<tr><td>${k.replace("_", "-")}</td><td>${v.model_kind.replace("_", " ")}</td><td class="num">${v.test_roc_auc}</td>
        <td class="num">${card.cutoffs[k].medium}</td><td class="num">${card.cutoffs[k].high}</td></tr>`).join("")}</tbody></table>
      <div class="limit"><strong>Synthetic-data artefact</strong><p class="small">${SP.esc(card.known_artefact)}</p></div>
      <div class="limit"><strong>Rule revisions</strong>${card.rule_revisions.map(r => `<p class="small">${SP.esc(r)}</p>`).join("")}</div>
      <p class="small muted">Cut-offs chosen on a validation split, never the test set. Source: <code>models/coercion_model_card.json</code>.</p></section>`;
  }

  function scenarios(s) {
    if (s.status) return missing("Demo scenarios", "scripts/run_scenarios.py");
    return `<section class="panel" style="margin-top:18px"><h2 class="section">Demo scenarios — ${s.passed}/${s.total} match the expected decision</h2>
      <div class="table-wrap"><table><thead><tr><th>#</th><th>Scenario</th><th>Expected</th><th>Actual</th><th>Rule</th><th>Receiver check</th></tr></thead><tbody>
      ${s.scenarios.map(r => `<tr><td>${r.id}</td><td>${SP.esc(r.title)}</td><td>${r.expected.join(" / ")}</td>
        <td><span class="badge b-${r.actual}">${r.passed ? "✓" : "✗"} ${r.actual}</span></td>
        <td><code>${r.pre_pause.matrix_row}${r.post_pause ? " → " + r.post_pause.matrix_row : ""}</code></td>
        <td class="small">${r.pre_pause.mule_status === "ok" ? r.pre_pause.mule_band : r.pre_pause.mule_status.replace("_", " ")}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">Source: <code>reports/scenarios.json</code> (${s.generated_at}).</p></section>`;
  }

  (async function boot() {
    await SP.ready;
    try {
      const m = await SP.api("GET", "/api/metrics");
      const r = m.reports;
      $("#evalBody").innerHTML =
        (r.system_evaluation.status ? missing("System comparison", "scripts/evaluate_system.py") : system(r.system_evaluation)) +
        `<div class="grid-2">${mule(r.mule_rbih)}${coercion(r.coercion_model_card)}</div>` + scenarios(r.scenarios);
    } catch (e) { $("#evalBody").innerHTML = `<p class="form-err">${SP.esc(e.message)}</p>`; }
  })();
})();
