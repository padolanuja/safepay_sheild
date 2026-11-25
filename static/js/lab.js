/* Blind Test Lab: random held-out cases scored live by GET /api/lab/random. */
(function () {
  const $ = s => document.querySelector(s);
  const ic = n => SP.icon(n);
  const ICON = { ALLOW: "check-circle", WARN: "alert", HOLD: "octagon" };
  const pct = (x, d = 1) => x === null || x === undefined ? "—" : SP.pct(x, d);
  const nice = s => String(s || "").replace(/_/g, " ");
  const st = { kase: null, vote: null, revealed: false, engineShown: false, you: 0, engine: 0, rounds: 0, runs: [] };

  function pool(r, ms) {
    $("#tPool").textContent = r.test_pool_size.toLocaleString("en-IN");
    $("#hPool").textContent = r.test_pool_size.toLocaleString("en-IN");
    $("#tAll").textContent = r.dataset_rows.toLocaleString("en-IN");
    $("#tLeak").textContent = r.rows_in_training_set;
    $("#hLeak").textContent = r.rows_in_training_set;
    $("#tTime").textContent = ms < 1000 ? Math.round(ms) + " ms" : (ms / 1000).toFixed(1) + " s";
    $("#tSeed").textContent = "seed " + r.seed;
  }

  async function timed(url) {
    const t0 = performance.now();
    const r = await SP.api("GET", url);
    return [r, performance.now() - t0];
  }

  // ---------------------------------------------------------------- single case
  function signals(s, m) {
    const call = s.on_video_call ? `Video · ${Math.round(s.call_duration_min)} min` : s.on_voice_call ? `Voice · ${Math.round(s.call_duration_min)} min` : "None";
    const yes = v => v ? "Yes" : "No";
    const items = [
      ["Amount", SP.inr(s.amount_inr)], ["vs usual", s.amount_to_avg_ratio.toFixed(1) + "×"], ["of balance", Math.round(s.amount_pct_of_balance) + "%"],
      ["New payee", yes(s.payee_is_new), s.payee_is_new], ["Payee in contacts", yes(s.payee_in_contacts)], ["Payee type", s.payee_type],
      ["Phone call", call, s.on_voice_call || s.on_video_call], ["Caller saved", s.on_voice_call || s.on_video_call ? yes(s.caller_in_contacts) : "—"],
      ["International", yes(s.caller_is_international), s.caller_is_international],
      ["Screen share", yes(s.screen_share_active), s.screen_share_active], ["Remote app (7d)", yes(s.remote_access_app_installed_7d), s.remote_access_app_installed_7d],
      ["UPI ID pasted", yes(s.vpa_pasted), s.vpa_pasted],
      ["Hesitations", s.hesitation_events, s.hesitation_events >= 4], ["Stop-start typing", yes(s.segmented_typing), s.segmented_typing], ["App switches", s.app_switches, s.app_switches >= 4],
      ["Time", String(s.hour_of_day).padStart(2, "0") + ":00", [23, 0, 1, 2, 3, 4, 5].includes(s.hour_of_day)], ["Payer age", s.user_age], ["FD broken (7d)", yes(s.fd_or_loan_liquidated_7d), s.fd_or_loan_liquidated_7d],
      ["New-payee txns 24h", s.new_payee_txns_24h], ["Sent to new 24h", SP.inr(s.amount_sent_new_payees_24h)],
      ["Payee mule score", m.mule_probability === null ? "No history" : pct(m.mule_probability), m.mule_probability !== null && m.mule_probability >= 0.4],
      ["Answer: know payee", nice(s.ans_know_payee)], ["Answer: guided", nice(s.ans_someone_guiding), s.ans_someone_guiding === "yes"], ["Stated purpose", nice(s.ans_purpose)],
    ];
    return `<div class="sig-grid">${items.map(([k, v, on]) => `<div class="sig ${on ? "on" : ""}"><small>${k}</small><b>${SP.esc(v)}</b></div>`).join("")}</div>`;
  }

  function renderCase() {
    const k = st.kase;
    if (!k) return;
    const t = k.truth;
    const eng = k.final_decision, caught = eng !== "ALLOW";
    const engineBox = !st.engineShown
      ? `<div class="verdict-box sealed"><div>${ic("cpu")}<p>Engine decision hidden</p><button class="btn btn-primary btn-sm" id="showEngine" ${st.vote ? "" : "disabled"}>${ic("play")} Show engine decision</button>${st.vote ? "" : `<p class="small">Make your call first</p>`}</div></div>`
      : `<div class="verdict-box"><h4>${ic("cpu")} SafePay Shield</h4><span class="chip c-${eng}">${ic(ICON[eng])} ${eng}</span>
          <dl class="kv"><dt>First check</dt><dd>${k.pre.decision} · rule ${k.pre.matrix_row}</dd>
            ${k.post ? `<dt>After answers</dt><dd>${k.post.decision} · rule ${k.post.matrix_row}</dd>` : ""}
            <dt>Coercion score</dt><dd class="mono">${pct((k.post || k.pre).coercion_score)} (${(k.post || k.pre).band})</dd>
            <dt>ML probability</dt><dd class="mono">${pct(k.pre.ml_probability)}</dd></dl>
          ${(k.post || k.pre).indicators.length ? `<ul class="ind-list">${(k.post || k.pre).indicators.slice(0, 5).map(i => `<li>${i.severity === "severe" ? "<b>SEVERE</b> " : ""}${SP.esc(i.evidence)}</li>`).join("")}</ul>` : ""}</div>`;
    const truthBox = !st.revealed
      ? `<div class="verdict-box sealed"><div>${ic("lock")}<p>Ground truth sealed</p><button class="btn btn-outline btn-sm" id="reveal" ${st.engineShown ? "" : "disabled"}>${ic("eye")} Reveal truth</button></div></div>`
      : `<div class="verdict-box ${k.correct ? "ok" : "miss"}"><h4>${ic("target")} Ground truth</h4>
          <span class="chip ${t.should_intervene ? "c-HOLD" : "c-ALLOW"}">${t.should_intervene ? ic("alert") + " Should be stopped" : ic("check-circle") + " Genuine payment"}</span>
          <dl class="kv"><dt>Label</dt><dd>${t.label_coerced ? "Coerced" : "Not coerced"}</dd>
            <dt>Scam type</dt><dd>${t.scam_type === "none" ? "—" : nice(t.scam_type)}</dd>
            <dt>Group</dt><dd>${nice(t.sample_group)}${t.hard_negative_reason !== "none" ? ` (${nice(t.hard_negative_reason)})` : ""}</dd>
            <dt>Payee</dt><dd>${{ new_no_history: "New, no history", rbih_legit: "RBIH clean account", rbih_mule: "RBIH labelled mule" }[t.payee_profile]}</dd>
            <dt>Engine</dt><dd>${k.correct ? `${ic("check")} correct (${caught ? "stopped" : "allowed"})` : `${ic("x")} ${caught ? "false alarm" : "missed"}`}</dd>
            <dt>Your call</dt><dd>${(st.vote === "coerced") === !!t.should_intervene ? ic("check") + " correct" : ic("x") + " wrong"}</dd></dl></div>`;
    $("#caseBox").innerHTML = `<div class="case">
      <div class="case-head">${ic("database")}<span class="mono">${SP.esc(k.txn_id)}</span><span class="muted">held-out row</span><span class="badge b-hidden">${ic("eye-off")} truth sealed</span></div>
      ${signals(k.signals, k.mule)}
      <div class="vote"><b>Your call:</b>
        <button class="btn btn-outline btn-sm" data-vote="genuine" aria-pressed="${st.vote === "genuine"}" ${st.engineShown ? "disabled" : ""}>${ic("check-circle")} Genuine</button>
        <button class="btn btn-outline btn-sm" data-vote="coerced" aria-pressed="${st.vote === "coerced"}" ${st.engineShown ? "disabled" : ""}>${ic("alert")} Coerced / stop it</button>
        <span class="muted small">Red values are classic pressure signs.</span></div></div>
      <div class="verdict">${engineBox}${truthBox}</div>`;
    document.querySelectorAll("[data-vote]").forEach(b => b.addEventListener("click", () => { st.vote = b.dataset.vote; renderCase(); }));
    if ($("#showEngine")) $("#showEngine").addEventListener("click", () => { st.engineShown = true; renderCase(); });
    if ($("#reveal")) $("#reveal").addEventListener("click", () => {
      st.revealed = true; st.rounds++;
      if ((st.vote === "coerced") === !!k.truth.should_intervene) st.you++;
      if (k.correct) st.engine++;
      $("#tScore").textContent = `${st.you} : ${st.engine}`;
      $("#tScore").nextElementSibling.textContent = `you : engine, over ${st.rounds} case${st.rounds > 1 ? "s" : ""}`;
      renderCase();
    });
  }

  async function drawOne() {
    const b = $("#drawOne"); b.disabled = true;
    try {
      const [r, ms] = await timed(`/api/lab/random?n=1&balanced=${$("#balanced").checked ? 1 : 0}`);
      pool(r, ms);
      Object.assign(st, { kase: r.results[0], vote: null, revealed: false, engineShown: false });
      renderCase();
    } catch (e) { SP.toast(e.message, "error"); }
    b.disabled = false;
  }

  // ---------------------------------------------------------------- stress test
  function renderBatch(r) {
    const m = r.metrics, c = m.confusion, B = m.baseline, d = m.decisions, n = r.n;
    const vs = (ours, base, lowerBetter) => base === null || ours === null ? "" :
      `<span class="vs">rule: ${pct(base)} ${(lowerBetter ? ours < base : ours > base) ? "· better" : ours === base ? "" : "· worse"}</span>`;
    $("#batchBox").innerHTML = `
      <div class="metric-row">
        <div class="metric"><small>Recall (stopped / should stop)</small><b>${pct(m.recall)}</b>${vs(m.recall, B.recall)}</div>
        <div class="metric"><small>Precision</small><b>${pct(m.precision)}</b>${vs(m.precision, B.precision)}</div>
        <div class="metric"><small>False-alarm rate</small><b>${pct(m.fp_rate)}</b>${vs(m.fp_rate, B.fp_rate, true)}</div>
        <div class="metric"><small>Held (HOLD) of should-stop</small><b>${pct(m.hold_recall)}</b><span class="vs">rule can't hold</span></div>
        <div class="metric"><small>Hard negatives interrupted</small><b>${pct(m.hard_negative_interrupted)}</b>${vs(m.hard_negative_interrupted, B.hard_negative_interrupted, true)}<span class="vs"> · n=${m.hard_negatives}</span></div>
      </div>
      <div class="grid-2" style="margin:0;gap:14px">
        <div><h3 class="why-h" style="margin-top:0">Confusion matrix</h3>
          <div class="cm"><div></div><div class="hd">Should stop (${m.positives})</div><div class="hd">Genuine (${m.negatives})</div>
            <div class="hd">Stopped</div><div class="cell good">${c.tp}<small>caught</small></div><div class="cell bad">${c.fp}<small>false alarms</small></div>
            <div class="hd">Allowed</div><div class="cell bad">${c.fn}<small>missed</small></div><div class="cell good">${c.tn}<small>correctly allowed</small></div></div></div>
        <div><h3 class="why-h" style="margin-top:0">Decisions (${n} cases)</h3>
          <div class="share-bar">${["ALLOW", "WARN", "HOLD"].map(k => `<span class="s-${k}" style="width:${100 * d[k] / n}%" title="${k} ${d[k]}"></span>`).join("")}</div>
          <div class="legend"><span><i class="sw d-ALLOW"></i>ALLOW ${d.ALLOW}</span><span><i class="sw d-WARN"></i>WARN ${d.WARN}</span><span><i class="sw d-HOLD"></i>HOLD ${d.HOLD}</span></div>
          <p class="muted small">Compared against the simple rule “${SP.esc(B.name)}” on the same ${n} cases. WARN = pause and verify, HOLD = cooling-off.</p></div>
      </div>`;
    const rows = r.results;
    $("#rowsNote").textContent = `· showing ${rows.length} of ${n}, seed ${r.seed}`;
    $("#rowsTable").innerHTML = `<thead><tr><th>Row</th><th>Truth</th><th>Scam type / genuine kind</th><th class="num">Amount</th><th>Call</th><th class="num">Coercion</th><th>Decision</th><th>Result</th></tr></thead><tbody>${rows.map(x => {
      const s = x.signals, t = x.truth, k = x.post || x.pre;
      return `<tr><td class="mono small">${SP.esc(x.txn_id)}</td>
        <td>${t.should_intervene ? `<span class="badge b-HOLD">stop</span>` : `<span class="badge b-ALLOW">genuine</span>`}</td>
        <td class="small">${t.scam_type !== "none" ? nice(t.scam_type) : t.hard_negative_reason !== "none" ? nice(t.hard_negative_reason) : nice(t.sample_group)}${t.payee_profile === "rbih_mule" ? " · mule payee" : ""}</td>
        <td class="num">${SP.inr(s.amount_inr)}</td><td class="small">${s.on_video_call ? "video" : s.on_voice_call ? "voice" : "—"}${s.on_voice_call || s.on_video_call ? " " + Math.round(s.call_duration_min) + "m" : ""}</td>
        <td class="num">${pct(k.coercion_score, 0)}</td>
        <td><span class="badge b-${x.final_decision}">${x.final_decision}</span></td>
        <td>${x.correct ? `<span class="badge b-ALLOW">${ic("check")} correct</span>` : `<span class="badge b-HOLD">${ic("x")} ${x.final_decision === "ALLOW" ? "missed" : "false alarm"}</span>`}</td></tr>`;
    }).join("")}</tbody>`;
  }

  function renderRuns() {
    const tb = $("#runs tbody");
    tb.innerHTML = st.runs.slice().reverse().map(r => `<tr><td class="mono"><a href="#" data-seed="${r.seed}" data-n="${r.n}">${r.seed}</a></td><td class="num">${r.n}</td>
      <td class="num">${pct(r.recall)}</td><td class="num">${pct(r.precision)}</td><td class="num">${pct(r.fp)}</td><td class="num">${(r.ms / 1000).toFixed(1)} s</td></tr>`).join("");
    tb.querySelectorAll("[data-seed]").forEach(a => a.addEventListener("click", e => {
      e.preventDefault(); $("#seedIn").value = a.dataset.seed; $("#nRows").value = a.dataset.n; runBatch();
    }));
    const W = 400, H = 70, P = 6, pts = st.runs;
    if (pts.length < 2) { $("#spark").innerHTML = ""; return; }
    const X = i => P + (W - 2 * P) * i / (pts.length - 1), Y = v => H - P - (H - 2 * P) * v;
    const line = k => pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k] || 0).toFixed(1)}`).join(" ");
    $("#spark").innerHTML = `<path class="r" d="${line("recall")}"/><path class="p" d="${line("precision")}"/>`;
  }

  async function runBatch() {
    const b = $("#runBatch"); b.disabled = true;
    const n = $("#nRows").value, seed = $("#seedIn").value.trim();
    $("#batchBox").innerHTML = `<div class="loading-row"><span class="spinner"></span> Scoring ${n} held-out payments through the full pipeline…</div>`;
    try {
      const [r, ms] = await timed(`/api/lab/random?n=${n}${seed ? "&seed=" + encodeURIComponent(seed) : ""}`);
      pool(r, ms);
      renderBatch(r);
      st.runs.push({ seed: r.seed, n: r.n, recall: r.metrics.recall, precision: r.metrics.precision, fp: r.metrics.fp_rate, ms });
      renderRuns();
      $("#seedIn").value = "";
    } catch (e) { $("#batchBox").innerHTML = `<p class="form-err">${SP.esc(e.message)}</p>`; }
    b.disabled = false;
  }

  async function boot() {
    await SP.ready;
    $("#drawOne").addEventListener("click", drawOne);
    $("#runBatch").addEventListener("click", runBatch);
  }
  boot();
})();
