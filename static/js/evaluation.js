/* Evaluation screen: renders reports/*.json via GET /api/metrics. Read-only. */
(function () {
  const $ = s => document.querySelector(s);
  const pct = (x, d = 1) => SP.pct(x, d);
  const missing = (name, script) => `<section class="panel" style="margin-bottom:18px"><h2 class="section">${name}</h2>
    <p class="caveat">Not generated yet. Run <code>python ${script}</code>.</p></section>`;

  // Fixed series slot per approach (Flowmatic chart palette, never cycled): the same approach has the
  // same colour in every chart on this page.
  const SERIES = { combined: 1, coercion_only: 2, mule_only: 3, rule_new_payee_5x: 4, rule_new_unknown_call: 5 };
  const nice = k => k.replace(/_/g, " ").replace(/\b(kyc|fd|upi|otp)\b/g, w => w.toUpperCase()).replace(/^\w/, c => c.toUpperCase());

  // Approach comparison: small multiples, one column per metric, one shared 0–100% scale.
  function comparison(A, keys) {
    const cols = [["recall", "Detection rate (recall)", "higher is better"], ["precision", "Precision", "higher is better"],
      ["fp_rate", "False-positive rate", "lower is better"], ["fp_rate_hard_negatives", "False alarms on hard negatives", "lower is better"]];
    const head = `<div></div>` + cols.map(([, t, h]) => `<div class="ch">${t}<small>${h}</small></div>`).join("");
    const body = keys.map(k => {
      const a = A[k], hi = k === "combined" ? " row-hi" : "";
      const tip = `<b>${SP.esc(a.name)}</b>` + cols.map(([m, t]) => `<div class="tt-row"><i class="sw ser-${SERIES[k]}"></i>${t}<b>${pct(a.intervene[m])}</b></div>`).join("");
      return `<div class="rl${hi}"><i class="sw ser-${SERIES[k]}" style="width:10px;height:10px;border-radius:3px;flex:none"></i>${SP.esc(a.name)}</div>` +
        cols.map(([m, t]) => `<div class="cell${hi}" data-tip="${SP.esc(tip)}"><div class="track ser-${SERIES[k]}" role="img" aria-label="${SP.esc(a.name)} ${t} ${pct(a.intervene[m])}">
          <span style="width:${(100 * a.intervene[m]).toFixed(1)}%"></span></div><span class="val">${pct(a.intervene[m], 0)}</span></div>`).join("");
    }).join("");
    return `<div class="chart-card"><h3>Intervene (WARN or HOLD): every approach on the same joint test set</h3>
      <p class="cc-sub">Each bar runs on a 0–100% scale; gridlines every 25%.</p>
      <div class="cmp">${head}${body}</div>
      <div class="cmp-foot muted small"><span>Synthetic coercion test split + RBIH payee profiles. Feasibility only, not real-world accuracy.</span></div></div>`;
  }

  const order = ["rule_new_payee_5x", "mule_only", "coercion_only", "combined"];   // drawn bottom-up, SafePay Shield on top
  const legendOrder = ["combined", "coercion_only", "mule_only", "rule_new_payee_5x"];
  const PLOTS = {};

  // Builds the dot plot at the host's real pixel width, so text and marks keep their size on any screen.
  function dotSvg(field, W) {
    const { A, cats, title } = PLOTS[field];
    const LW = Math.min(250, W * .34), RP = 46, RH = 38, T = 8, H = T + cats.length * RH + 26;
    const x = v => LW + v * (W - LW - RP);
    const grid = [0, .25, .5, .75, 1].map(v => `<line x1="${x(v)}" x2="${x(v)}" y1="${T}" y2="${H - 22}" class="${v ? "g" : "g base"}"></line>
      <text x="${x(v)}" y="${H - 6}" class="tk" text-anchor="middle">${v * 100}%</text>`).join("");
    const rowsSvg = cats.map((c, i) => {
      const cy = T + i * RH + RH / 2, vals = order.map(k => A[k][field]?.[c]?.intervene).filter(v => v != null);
      const n = A.combined[field][c].n, comb = A.combined[field][c];
      const tip = `<b>${SP.esc(nice(c))}</b> · n = ${n}` + legendOrder.map(k => A[k][field]?.[c] ? `<div class="tt-row"><i class="sw ser-${SERIES[k]}"></i>${SP.esc(A[k].name)}<b>${pct(A[k][field][c].intervene, 0)}</b></div>` : "").join("") +
        `<div class="tt-row"><i></i>SafePay Shield HOLD only<b>${pct(comb.hold, 0)}</b></div>`;
      const dots = order.map(k => { const v = A[k][field]?.[c]?.intervene; return v == null ? "" : `<circle class="dot ser-${SERIES[k]}" cx="${x(v)}" cy="${cy}" r="${k === "combined" ? 7 : 6}"></circle>`; }).join("");
      const cv = comb.intervene, lx = x(cv) + 12 > W - 6 ? x(cv) - 12 : x(cv) + 12;
      return `<g class="row" data-tip="${SP.esc(tip)}" tabindex="0">
        <rect class="hit" x="0" y="${cy - RH / 2}" width="${W}" height="${RH}" rx="6"></rect>
        <text x="${LW - 16}" y="${cy + 4}" class="rlab" text-anchor="end">${SP.esc(nice(c))} <tspan class="n">(n=${n})</tspan></text>
        <line x1="${x(0)}" x2="${x(1)}" y1="${cy}" y2="${cy}" class="lane"></line>
        <line x1="${x(Math.min(...vals))}" x2="${x(Math.max(...vals))}" y1="${cy}" y2="${cy}" class="span"></line>
        ${dots}<text x="${lx}" y="${cy + 4}" class="dv" text-anchor="${lx < x(cv) ? "end" : "start"}">${pct(cv, 0)}</text></g>`;
    }).join("");
    return `<svg class="dotplot" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${SP.esc(title)}">${grid}${rowsSvg}</svg>`;
  }
  function drawPlots() {
    document.querySelectorAll(".dp-host").forEach(h => {
      const W = Math.max(480, Math.floor(h.clientWidth));
      if (h.dataset.w !== String(W)) { h.dataset.w = W; h.innerHTML = dotSvg(h.dataset.field, W); }
    });
  }

  // Cleveland dot plot: one row per category, one dot per approach on a shared 0–100% axis.
  function dotPlot(A, field, o) {
    const cats = Object.keys(A.combined[field] || {}).filter(k => !(o.skip || []).includes(k))
      .sort((a, b) => A.combined[field][b].intervene - A.combined[field][a].intervene);
    if (!cats.length) return "";
    PLOTS[field] = { A, cats, title: o.title };
    const table = `<details class="table-view"><summary>Show as table</summary><div class="table-wrap"><table><thead><tr><th>${o.tableHead}</th>
      ${legendOrder.map(k => `<th class="num">${SP.esc(A[k].name)}</th>`).join("")}<th class="num">SafePay Shield HOLD</th><th class="num">n</th></tr></thead><tbody>
      ${cats.map(c => `<tr><td>${SP.esc(nice(c))}</td>${legendOrder.map(k => `<td class="num">${pct(A[k][field]?.[c]?.intervene)}</td>`).join("")}
        <td class="num">${pct(A.combined[field][c].hold)}</td><td class="num">${A.combined[field][c].n}</td></tr>`).join("")}</tbody></table></div></details>`;
    return `<div class="chart-card"><h3>${o.title}</h3><p class="cc-sub">${o.sub}</p>
      <div class="series-legend">${legendOrder.map(k => `<span><i class="sw ser-${SERIES[k]}"></i>${SP.esc(A[k].name)}</span>`).join("")}</div>
      <div class="dp-host" data-field="${field}"></div>
      <p class="muted small" style="margin:6px 0 0">The grey span joins the lowest and highest approach; the label is SafePay Shield's value.</p>${table}</div>`;
  }

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
    const charts = comparison(A, rows) + dotPlot(A, "recall_by_scam_type", {
        title: "Coerced payments caught (WARN or HOLD), by scam type",
        sub: "Higher is better. Each row is one scam type; each dot is one approach on the same held-out rows.",
        skip: ["none"], tableHead: "Scam type" }) +
      dotPlot(A, "fp_rate_by_hard_negative", {
        title: "Genuine payments interrupted (WARN or HOLD), by hard-negative type",
        sub: "Lower is better. Hard negatives are genuine payments built to look risky.",
        tableHead: "Genuine payment type" });
    const lims = (e.known_limitations || []).map(l => `<div class="limit"><strong>Known limitation</strong><p>${SP.esc(l.finding)}</p>
      <p class="small"><b>Cause:</b> ${SP.esc(l.cause)}</p><p class="small"><b>Planned fix:</b> ${SP.esc(l.planned_fix)}</p></div>`).join("");
    return `<section class="panel" style="margin-bottom:18px"><h2 class="section">System comparison — same joint test set</h2>
      <p class="caveat">${SP.esc(e.caveat)}<br>Source: <code>scripts/evaluate_system.py</code> → <code>reports/evaluation.json</code> (${e.generated_at}).</p>
      ${tiles}${table}${lims}</section>
      <section class="panel" style="margin-bottom:18px"><h2 class="section"><svg class="ico"><use href="#i-chart"/></svg>Charts</h2>
      <p class="muted small" style="margin:-4px 0 14px">Drawn live from <code>reports/evaluation.json</code>. Hover any row for exact values; each chart has a table view.</p>${charts}
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
      drawPlots();
      new ResizeObserver(() => drawPlots()).observe($("#evalBody"));
    } catch (e) { $("#evalBody").innerHTML = `<p class="form-err">${SP.esc(e.message)}</p>`; }
  })();
})();
