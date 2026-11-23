/* Pay screen: simulated UPI app + situation simulator + "Why?" panel.
   Flow: Pay -> first check (pre_pause) -> ALLOW | WARN (questions -> post_pause) | HOLD.
   The server decides everything; the final decision after questions comes from the server
   (final_decision = higher of the two passes). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const SIT_BOOL = ["caller_in_contacts", "caller_is_international", "screen_share_active",
    "remote_access_app_installed_7d", "vpa_pasted", "payee_in_contacts", "fd_or_loan_liquidated_7d", "segmented_typing"];
  const SIT_RANGE = ["call_duration_min", "hesitation_events", "app_switches", "session_duration_sec", "hour_of_day"];
  const NIGHT = [23, 0, 1, 2, 3, 4, 5];
  const VPA_RE = /^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$/;
  const SOURCE = {
    "rule": { key: "why.legend.rule", cls: "src-rule" },
    "model_prediction:coercion": { key: "why.legend.ml", cls: "src-ml" },
    "model_prediction:mule": { key: "why.legend.mule", cls: "src-mule" },
    "rule:splitting": { key: "why.legend.split", cls: "src-split" },
  };
  const ICON = { ALLOW: "✓", WARN: "!", HOLD: "■" };

  const st = {
    scenarios: [], scenario: null, callType: "none", flow: null, screen: "form",
    form: { vpa: "", amount: "" }, payee: null, answers: null, timer: null, holdEnds: null,
  };

  // ------------------------------------------------------------------ simulator
  function setCallType(v) {
    st.callType = v;
    document.querySelectorAll("#callType button").forEach(b => b.setAttribute("aria-pressed", b.dataset.val === v));
    $("#callDetails").classList.toggle("disabled", v === "none");
    $("#callDetails").querySelectorAll("input").forEach(i => (i.disabled = v === "none"));
    if (v === "none") { $("#call_duration_min").value = 0; }
    else if (Number($("#call_duration_min").value) === 0) { $("#call_duration_min").value = 10; }
    syncOutputs();
  }

  function syncOutputs() {
    $("#callMinOut").textContent = $("#call_duration_min").value;
    $("#hesOut").textContent = $("#hesitation_events").value;
    $("#appOut").textContent = $("#app_switches").value;
    $("#sessOut").textContent = $("#session_duration_sec").value;
    $("#npOut").textContent = $("#new_payees_24h").value;
    const h = Number($("#hour_of_day").value);
    $("#hourOut").textContent = String(h).padStart(2, "0") + ":00";
    $("#nightTag").hidden = !NIGHT.includes(h);
    $("#clock").textContent = String(h).padStart(2, "0") + ":" + String(new Date().getMinutes()).padStart(2, "0");
  }

  function getSituation() {
    const s = {
      on_voice_call: st.callType === "voice" ? 1 : 0,
      on_video_call: st.callType === "video" ? 1 : 0,
    };
    SIT_BOOL.forEach(k => (s[k] = $("#" + k).checked ? 1 : 0));
    SIT_RANGE.forEach(k => (s[k] = Number($("#" + k).value)));
    if (st.callType === "none") { s.call_duration_min = 0; s.caller_in_contacts = 0; s.caller_is_international = 0; }
    if (st.scenario && st.scenario.situation.simulated_day_of_month) s.simulated_day_of_month = st.scenario.situation.simulated_day_of_month;
    return s;
  }

  function setSituation(s) {
    setCallType(s.on_video_call ? "video" : s.on_voice_call ? "voice" : "none");
    SIT_BOOL.forEach(k => ($("#" + k).checked = !!s[k]));
    SIT_RANGE.forEach(k => { if (s[k] !== undefined) $("#" + k).value = s[k]; });
    syncOutputs();
  }

  function resetSimulator() {
    st.scenario = null; st.answers = null;
    $("#preset").value = "";
    $("#presetInfo").hidden = true;
    $("#newPayeesWrap").classList.remove("disabled"); $("#new_payees_24h").disabled = false;
    $("#new_payees_24h").value = 0;
    setSituation({ hour_of_day: 14, session_duration_sec: 45, hesitation_events: 0, app_switches: 0, call_duration_min: 0 });
  }

  function renderPresets() {
    const sel = $("#preset");
    const cur = sel.value;
    sel.innerHTML = `<option value="">${SP.esc(SP.t("sim.preset_none"))}</option>` + st.scenarios.map(s =>
      `<option value="${s.id}">${s.id}. ${SP.esc(s.title)} → ${s.expected_decisions.join(" / ")}</option>`).join("");
    sel.value = cur;
  }

  async function loadPreset(id) {
    if (!id) { resetSimulator(); renderForm(); return; }
    const s = await SP.api("POST", `/api/scenarios/${id}/prepare`);
    st.scenario = s; st.answers = s.answers;
    if (SP.userId !== s.user_id) await SP.setUser(s.user_id, { followLanguage: false });
    st.form = { vpa: s.payee_vpa, amount: String(s.amount) };
    setSituation(s.situation);
    $("#newPayeesWrap").classList.add("disabled"); $("#new_payees_24h").disabled = true;
    const info = $("#presetInfo");
    info.hidden = false;
    info.innerHTML = `<p>${SP.esc(s.description)}</p>
      <p class="expected"><span class="muted">${SP.esc(SP.t("sim.expected"))}:</span>
        ${s.expected_decisions.map(d => `<span class="badge b-${d}">${ICON[d]} ${SP.esc(SP.t("decision." + d))}</span>`).join(" ")}</p>
      ${s.prior_payments.length ? `<p class="muted small">${s.prior_payments.length} earlier payment(s) to this payee loaded (demo data).</p>` : ""}
      <p class="muted small">${SP.esc(SP.t("sim.answers_note"))}</p>`;
    st.flow = null; renderForm(); renderWhy();
  }

  // ------------------------------------------------------------------ phone screens
  const screen = () => $("#screen");
  function stopTimer() { if (st.timer) { clearInterval(st.timer); st.timer = null; } }

  function renderForm() {
    stopTimer(); st.screen = "form";
    const u = SP.user();
    screen().innerHTML = `
      <div class="balance-card"><span>${SP.esc(u.name)}</span><span><small>${SP.esc(SP.t("pay.balance"))}</small> ${SP.inr(u.balance_inr)}</span></div>
      <form id="payForm" class="pay-form" novalidate>
        <label class="field"><span>${SP.esc(SP.t("pay.upi_id"))}</span>
          <input id="vpa" autocomplete="off" inputmode="email" placeholder="${SP.esc(SP.t("pay.upi_placeholder"))}" value="${SP.esc(st.form.vpa)}"></label>
        <div id="payeeInfo" class="payee-info"></div>
        <label class="field"><span>${SP.esc(SP.t("pay.amount"))}</span>
          <div class="amount-wrap"><span>₹</span><input id="amount" inputmode="numeric" placeholder="0" value="${SP.esc(st.form.amount)}"></div></label>
        <p id="formErr" class="form-err" role="alert"></p>
        <button class="btn btn-primary btn-lg btn-block" type="submit">${SP.esc(SP.t("pay.button"))}</button>
      </form>`;
    const vpa = $("#vpa"), amt = $("#amount");
    let deb;
    vpa.addEventListener("input", () => { st.form.vpa = vpa.value.trim(); clearTimeout(deb); deb = setTimeout(lookup, 350); });
    amt.addEventListener("input", () => { amt.value = amt.value.replace(/[^0-9]/g, ""); st.form.amount = amt.value; });
    $("#payForm").addEventListener("submit", e => { e.preventDefault(); pay(); });
    lookup();
  }

  async function lookup() {
    const box = $("#payeeInfo");
    if (!box) return;
    const v = st.form.vpa.toLowerCase();
    st.payee = null;
    if (!VPA_RE.test(v)) { box.innerHTML = ""; return; }
    try {
      const p = await SP.api("GET", `/api/payees/${encodeURIComponent(v)}?user_id=${SP.userId}`);
      st.payee = p;
      const initials = p.display_name.split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
      box.innerHTML = `<div class="payee-card"><span class="avatar">${SP.esc(initials)}</span>
        <div><strong>${SP.esc(p.display_name)}</strong>
        <div class="small muted">${p.history_transactions ? SP.esc(SP.t("pay.history", { n: p.history_transactions.toLocaleString("en-IN") })) : SP.esc(SP.t("pay.no_history"))}</div></div>
        ${p.new_for_user ? `<span class="badge b-new">${SP.esc(SP.t("pay.new_payee"))}</span>` : `<span class="badge b-known">${SP.esc(SP.t("pay.known_payee"))}</span>`}</div>`;
    } catch (e) {
      box.innerHTML = `<div class="payee-card unknown"><span class="avatar">?</span><div class="small">${SP.esc(SP.t("pay.not_in_directory"))}</div>
        <span class="badge b-new">${SP.esc(SP.t("pay.new_payee"))}</span></div>`;
    }
  }

  const delay = ms => new Promise(r => setTimeout(r, ms));

  async function pay() {
    const err = $("#formErr");
    const vpa = st.form.vpa.toLowerCase(), amount = Number(st.form.amount);
    if (!VPA_RE.test(vpa)) { err.textContent = SP.t("pay.invalid_upi"); return; }
    if (!(amount >= 1 && amount <= 100000)) { err.textContent = SP.t("pay.invalid_amount"); return; }
    renderChecking();
    try {
      if (st.scenario) await SP.api("POST", `/api/scenarios/${st.scenario.id}/prepare`);   // fresh earlier payments
      else await SP.api("POST", "/api/simulator/new-payee-payments", { user_id: SP.userId, count: Number($("#new_payees_24h").value) });
      const body = { user_id: SP.userId, payee_vpa: vpa, amount, situation: getSituation(), stage: "pre_pause" };
      if (!st.payee) body.payee_type = "P2P";
      const [res] = await Promise.all([SP.api("POST", "/api/assess", body), delay(1300)]);
      st.flow = { pre: res, post: null, final: res.decision, body };
      renderWhy();
      if (res.decision === "ALLOW") return allow(res);
      if (res.decision === "WARN") return renderQuestions("warn");
      return renderHold();
    } catch (e) {
      renderForm(); $("#formErr").textContent = e.message;
    }
  }

  function renderChecking() {
    stopTimer(); st.screen = "checking";
    screen().innerHTML = `<div class="checking"><div class="radar"><span></span><span></span><svg viewBox="0 0 32 32"><path d="M16 2 4 7v8c0 7.2 5.1 13.4 12 15 6.9-1.6 12-7.8 12-15V7z"/></svg></div>
      <h3>${SP.esc(SP.t("pay.checking"))}</h3><p class="muted">${SP.esc(SP.t("pay.checking_sub"))}</p></div>`;
  }

  async function allow(res) {
    st.screen = "allow";
    try { await SP.api("POST", "/api/payments/confirm", { assessment_id: res.assessment_id, action: "proceed", payee_vpa: res.payee.vpa }); }
    catch (e) { SP.toast(e.message, "error"); }
    resultScreen("ALLOW", "result.allow.title", "result.allow.sub", res);
  }

  function resultScreen(kind, titleKey, subKey, res) {
    stopTimer();
    screen().innerHTML = `<div class="result r-${kind}">
      <div class="result-icon">${kind === "ALLOW" ? "✓" : kind === "CANCEL" ? "✓" : "!"}</div>
      <h3>${SP.esc(SP.t(titleKey))}</h3>
      ${res ? `<p class="amount-big">${SP.inr(res.amount)}</p><p class="muted">${SP.esc(res.payee.display_name || res.payee.vpa)}</p>` : ""}
      <p class="muted small">${SP.esc(SP.t(subKey))}</p>
      <button class="btn btn-ghost btn-block" id="newPay">${SP.esc(SP.t("result.new_payment"))}</button></div>`;
    $("#newPay").addEventListener("click", () => { st.flow = null; renderForm(); renderWhy(); });
  }

  function renderQuestions(mode) {
    stopTimer(); st.screen = "questions:" + mode;
    const a = st.answers || {};
    const radio = (name, opts, key) => `<div class="choice" role="radiogroup">${opts.map(o =>
      `<label><input type="radio" name="${name}" value="${o}" ${a[name] === o ? "checked" : ""}><span>${SP.esc(SP.t(key + "." + o))}</span></label>`).join("")}</div>`;
    const purposes = ["bills_shopping", "family", "rent_fees", "medical", "investment", "loan_repayment",
      "govt_fine_verification", "refund_cashback", "job_task_fee", "other"];
    const checks = ["under_arrest", "claims_authority", "told_not_to_tell", "asked_to_install_app"];
    const head = mode === "warn"
      ? `<div class="banner-warn"><span class="b-ico">!</span><div><strong>${SP.esc(SP.t("result.warn.title"))}</strong><p>${SP.esc(SP.t("result.warn.sub"))}</p></div></div>`
      : `<div class="banner-hold"><span class="b-ico">■</span><div><strong>${SP.esc(SP.t("result.hold.title"))}</strong></div></div>`;
    screen().innerHTML = `${head}
      <form id="qForm" class="questions" novalidate>
        <fieldset><legend>${SP.esc(SP.t("q.know"))}</legend>${radio("ans_know_payee", ["yes", "unsure", "no"], "q.know")}</fieldset>
        <fieldset><legend>${SP.esc(SP.t("q.guiding"))}</legend>${radio("ans_someone_guiding", ["yes", "no"], "q.guiding")}</fieldset>
        <fieldset><legend>${SP.esc(SP.t("q.purpose"))}</legend>
          <select name="ans_purpose"><option value="">—</option>${purposes.map(p => `<option value="${p}" ${a.ans_purpose === p ? "selected" : ""}>${SP.esc(SP.t("purpose." + p))}</option>`).join("")}</select></fieldset>
        <fieldset><legend>${SP.esc(SP.t("q.checklist"))}</legend>
          ${checks.map(c => `<label class="check"><input type="checkbox" name="checklist" value="${c}" ${(a.checklist || []).includes(c) ? "checked" : ""}><span>${SP.esc(SP.t("check." + c))}</span></label>`).join("")}</fieldset>
        <p id="qErr" class="form-err" role="alert"></p>
        <button class="btn btn-primary btn-lg btn-block" type="submit">${SP.esc(SP.t("btn.check_again"))}</button>
        <button class="btn btn-ghost btn-block" type="button" id="qCancel">${SP.esc(SP.t("btn.cancel"))}</button>
      </form>`;
    $("#qCancel").addEventListener("click", () => cancel(st.flow.pre));
    $("#qForm").addEventListener("submit", async e => {
      e.preventDefault();
      const f = new FormData(e.target);
      const answers = { ans_know_payee: f.get("ans_know_payee"), ans_someone_guiding: f.get("ans_someone_guiding"),
        ans_purpose: f.get("ans_purpose"), checklist: f.getAll("checklist") };
      if (!answers.ans_know_payee || !answers.ans_someone_guiding || !answers.ans_purpose) {
        $("#qErr").textContent = "Please answer the three questions."; return;
      }
      st.answers = answers;
      renderChecking();
      try {
        const [res] = await Promise.all([SP.api("POST", "/api/assess", { ...st.flow.body, stage: "post_pause",
          answers, pre_assessment_id: st.flow.pre.assessment_id }), delay(900)]);
        st.flow.post = res; st.flow.final = res.final_decision;
        renderWhy();
        if (res.final_decision === "HOLD") renderHold(); else renderVerified();
      } catch (err) { renderQuestions(mode); $("#qErr").textContent = err.message; }
    });
  }

  function decisive(final) {   // the assessment whose own decision equals the final one
    const { pre, post } = st.flow;
    return post && post.decision === final ? post : pre.decision === final ? pre : (post || pre);
  }

  function renderVerified() {
    stopTimer(); st.screen = "verified";
    const a = decisive("WARN");
    screen().innerHTML = `<div class="result r-WARN">
      <div class="result-icon">!</div><h3>${SP.esc(SP.t("result.verified.title"))}</h3>
      <p class="amount-big">${SP.inr(a.amount)}</p><p class="muted">${SP.esc(a.payee.display_name || a.payee.vpa)}</p>
      <p>${SP.esc(SP.t("result.verified.sub"))}</p>
      <button class="btn btn-primary btn-lg btn-block" id="vCancel">${SP.esc(SP.t("btn.cancel"))}</button>
      <button class="btn btn-ghost btn-block" id="vPay">${SP.esc(SP.t("btn.pay_anyway"))}</button></div>`;
    $("#vCancel").addEventListener("click", () => cancel(a));
    $("#vPay").addEventListener("click", async () => {
      try {
        await SP.api("POST", "/api/payments/confirm", { assessment_id: a.assessment_id, action: "proceed", payee_vpa: a.payee.vpa });
        resultScreen("ALLOW", "result.allow.title", "result.allow.sub", a);
      } catch (e) { SP.toast(e.message, "error"); }
    });
  }

  async function cancel(a) {
    try { await SP.api("POST", "/api/payments/confirm", { assessment_id: a.assessment_id, action: "cancel", payee_vpa: a.payee.vpa }); }
    catch (e) { if (e.status !== 409) SP.toast(e.message, "error"); }
    resultScreen("CANCEL", "result.cancelled", "app.simulated", null);
  }

  async function renderHold() {
    st.screen = "hold";
    const a = decisive("HOLD");
    const mins = a.response.cooling_off_minutes;
    // One cooling-off period per payment flow (answering questions must not restart it).
    if (!st.flow.holdEnds) st.flow.holdEnds = Date.now() + mins * 60000;
    st.holdEnds = st.flow.holdEnds;
    let contacts = [];
    try { contacts = (await SP.api("GET", `/api/contacts?user_id=${SP.userId}`)).contacts.filter(c => c.consent_given); } catch (e) { /* none */ }
    const asked = !!st.flow.post;
    screen().innerHTML = `<div class="result r-HOLD hold">
      <div class="result-icon">■</div>
      <h3>${SP.esc(SP.t("result.hold.title"))}</h3>
      <p class="muted small">${SP.esc(SP.t("result.hold.sub"))}</p>
      <div class="countdown" aria-live="off"><small>${SP.esc(SP.t("result.hold.countdown"))}</small><span id="cd">--:--</span></div>
      <p class="hold-msg">${SP.esc(SP.t("result.hold.message"))}</p>
      <a class="helpline" href="tel:${a.response.helpline}">☎ ${SP.esc(SP.t("result.hold.helpline"))}</a>
      <a class="small" href="${a.response.helpline_url}" target="_blank" rel="noopener">${SP.esc(SP.t("result.hold.website"))}</a>
      <button class="btn btn-primary btn-lg btn-block" id="hCancel">${SP.esc(SP.t("btn.cancel"))}</button>
      ${contacts.length ? `<button class="btn btn-outline btn-block" id="hAlert" ${st.flow.sms ? "disabled" : ""}>${SP.esc(SP.t("btn.alert_contact"))} · ${SP.esc(contacts[0].name.split(" ")[0])}</button>`
        : `<p class="muted small">${SP.esc(SP.t("alert.no_contact"))}</p>`}
      <div id="smsPreview">${st.flow.sms || ""}</div>
      ${asked ? "" : `<button class="btn btn-link btn-block" id="hTell">${SP.esc(SP.t("q.checklist").replace(/\s*\(.*\)/, ""))}…</button>`}
      <button class="btn btn-ghost btn-block" id="hPay" disabled>🔒 ${SP.esc(SP.t("btn.pay_anyway"))}</button>
    </div>`;
    const tick = () => {
      const left = Math.max(0, st.holdEnds - Date.now());
      const el = $("#cd"); if (!el) return stopTimer();
      el.textContent = `${String(Math.floor(left / 60000)).padStart(2, "0")}:${String(Math.floor(left / 1000) % 60).padStart(2, "0")}`;
      if (left === 0) { $("#hPay").disabled = false; $("#hPay").textContent = SP.t("btn.pay_anyway"); stopTimer(); }
    };
    stopTimer(); tick(); st.timer = setInterval(tick, 1000);
    $("#hCancel").addEventListener("click", () => cancel(a));
    if ($("#hTell")) $("#hTell").addEventListener("click", () => renderQuestions("hold"));
    $("#hPay").addEventListener("click", async () => {
      try { await SP.api("POST", "/api/payments/confirm", { assessment_id: a.assessment_id, action: "proceed", payee_vpa: a.payee.vpa });
        resultScreen("ALLOW", "result.allow.title", "result.allow.sub", a); }
      catch (e) { SP.toast(e.message, "error"); }
    });
    if ($("#hAlert")) $("#hAlert").addEventListener("click", async () => {
      try {
        const r = await SP.api("POST", "/api/alerts/trusted-contact", { assessment_id: a.assessment_id, contact_id: contacts[0].id });
        st.flow.sms = `<div class="sms"><div class="sms-head">${SP.esc(SP.t("alert.title"))} · ${SP.esc(r.to)} (${SP.esc(r.phone_masked)})</div>
          <div class="sms-bubble">${SP.esc(r.message_preview)}</div><div class="small muted">${SP.esc(SP.t("alert.sent"))}</div></div>`;
        $("#smsPreview").innerHTML = st.flow.sms;
        $("#hAlert").disabled = true;
      } catch (e) { SP.toast(e.message, "error"); }
    });
  }

  function rerenderScreen() {
    if (!st.flow || st.screen === "form") return renderForm();
    if (st.screen.startsWith("questions")) return renderQuestions(st.screen.split(":")[1]);
    if (st.screen === "hold") return renderHold();
    if (st.screen === "verified") return renderVerified();
  }

  // ------------------------------------------------------------------ Why? panel
  function gauge(value, markers, band, disabled) {
    // Semicircle gauge: 0..1 left->right. Markers are the decision cut-offs.
    const R = 70, cx = 90, cy = 86;
    const pt = v => { const a = Math.PI * (1 - v); return [cx + R * Math.cos(a), cy - R * Math.sin(a)]; };
    const arc = (v0, v1) => { const [x0, y0] = pt(v0), [x1, y1] = pt(v1); return `M${x0},${y0} A${R},${R} 0 0 1 ${x1},${y1}`; };
    const v = Math.max(0.001, Math.min(1, value || 0));
    return `<svg class="gauge ${disabled ? "off" : "g-" + band}" viewBox="0 0 180 100" aria-hidden="true">
      <path class="g-track" d="${arc(0, 1)}"/>
      ${disabled ? "" : `<path class="g-val" d="${arc(0, v)}"/>`}
      ${markers.map(m => { const [x, y] = pt(m), [x2, y2] = [cx + (R + 9) * Math.cos(Math.PI * (1 - m)), cy - (R + 9) * Math.sin(Math.PI * (1 - m))];
        return `<line class="g-mark" x1="${x}" y1="${y}" x2="${x2}" y2="${y2}"/>`; }).join("")}
    </svg>`;
  }

  const BINARY = new Set(["payee_is_new", "payee_in_contacts", "on_voice_call", "on_video_call", "caller_in_contacts",
    "caller_is_international", "screen_share_active", "remote_access_app_installed_7d", "segmented_typing", "vpa_pasted",
    "fd_or_loan_liquidated_7d", "payee_type_P2M"]);
  const MONEY = new Set(["amount_inr", "avg_txn_amount_90d", "balance_before_txn", "amount_sent_new_payees_24h",
    "total_debit", "total_credit", "avg_transaction_amount"]);

  function compactInr(v) {
    const a = Math.abs(v);
    if (a >= 1e7) return "Rs " + (v / 1e7).toFixed(1) + " Cr";
    if (a >= 1e5) return "Rs " + (v / 1e5).toFixed(1) + " L";
    return SP.inr(v);
  }

  function featValue(id, v) {
    if (v === undefined || v === null) return "";
    if (BINARY.has(id)) return v ? SP.t("q.know.yes") : SP.t("q.know.no");
    if (MONEY.has(id)) return compactInr(v);
    if (id === "amount_to_avg_ratio") return (v >= 10 ? Math.round(v) : v.toFixed(1)) + "×";
    if (id === "amount_pct_of_balance") return Math.round(v) + "%";
    if (id === "call_duration_min") return Math.round(v) + " min";
    if (id === "session_duration_sec") return Math.round(v) + " s";
    if (id === "hour_of_day") return String(v).padStart(2, "0") + ":00";
    if (id === "user_age") return Math.round(v);
    return Math.round(v).toLocaleString("en-IN");
  }

  function featLabel(id, value, inputs) {
    // One-hot pause answers: show the person's ACTUAL answer, not the one-hot column name.
    for (const col of ["ans_know_payee", "ans_someone_guiding", "ans_purpose"]) {
      if (id.startsWith(col + "_")) {
        const ans = inputs && inputs[col] ? inputs[col].value : null;
        const val = !ans ? "" : col === "ans_purpose" ? SP.t("purpose." + ans)
          : SP.t((col === "ans_know_payee" ? "q.know." : "q.guiding.") + ans);
        return `${SP.t("feat." + col)}: ${val}`;
      }
    }
    const label = SP.has("feat." + id) ? SP.t("feat." + id) : id;
    const v = featValue(id, value);
    return v === "" ? label : `${label} · ${v}`;
  }

  function factorLabel(f, inputs) {
    if (f.source === "rule") return SP.has("rule." + f.id) ? SP.t("rule." + f.id) : f.text;
    if (f.source === "rule:splitting") return SP.t("split." + f.id);
    return featLabel(f.id, f.value, inputs);
  }

  function devText(d) {
    if (d.id === "amount_vs_usual") {
      const r = d.params.ratio;
      return SP.t(d.text_key, { ratio: r >= 10 ? Math.round(r) : r });
    }
    return SP.t(d.text_key, d.params);
  }

  function renderWhy() {
    const body = $("#whyBody");
    if (!st.flow) { body.innerHTML = `<p class="muted">${SP.esc(SP.t("why.empty"))}</p>`; return; }
    const { pre, post, final } = st.flow;
    const r = post || pre;
    const ex = r.explanation, c = r.coercion, m = r.mule;
    const row = r.fusion.matrix_row;

    // -- decision + timeline
    let html = `<div class="why-decision d-${final}"><span class="chip c-${final}">${ICON[final]} ${SP.esc(SP.t("decision." + final))}</span>
      ${post ? `<span class="timeline">${SP.esc(SP.t("why.first_check"))}: <b>${SP.esc(SP.t("decision_short." + pre.decision))}</b>
        → ${SP.esc(SP.t("why.after_answers"))}: <b>${SP.esc(SP.t("decision_short." + post.decision))}</b></span>` : ""}</div>
      <p class="why-reason">${SP.esc(SP.t("reason." + row))}</p>`;
    const severe = c.indicators.filter(i => i.severity === "severe" && i.counted);
    if (severe.length) html += `<ul class="severe-list">${severe.map(i => `<li><span class="tag tag-severe">${SP.esc(SP.t("why.severe"))}</span> ${SP.esc(SP.t("rule." + i.id))}</li>`).join("")}</ul>`;

    // -- the key differentiator: decided on coercion alone
    if (ex.decided_on_coercion_only) {
      html += `<div class="callout callout-key"><span class="callout-ico">i</span><div>
        <strong>${SP.esc(SP.t("why.coercion_only_title"))}</strong><p>${SP.esc(SP.t("why.coercion_only_body"))}</p></div></div>`;
    }

    // -- gauges
    const muleOk = m.status === "ok";
    const muleTxt = muleOk
      ? `<div class="g-num">${SP.pct(m.mule_probability)}</div><div class="g-band">${SP.esc(SP.t("band." + m.band))}</div>`
      : `<div class="g-num off">${SP.esc(SP.t("why.not_checked"))}</div><div class="g-note">${SP.esc(m.status === "insufficient_history"
          ? SP.t("why.too_little_history", { n: m.transactions, min: m.min_history }) : SP.t("why.model_unavailable"))}</div>`;
    const cut = c.cutoffs;
    html += `<div class="gauges">
      <div class="gauge-card ${muleOk ? "" : "is-off"}"><h4>${SP.esc(SP.t("why.mule"))}</h4>
        ${gauge(muleOk ? m.mule_probability : 0, [0.10, 0.40], muleOk ? m.band : "low", !muleOk)}${muleTxt}
        <div class="src-tag src-mule">${SP.esc(SP.t("why.legend.mule"))}</div></div>
      <div class="gauge-card"><h4>${SP.esc(SP.t("why.coercion"))}</h4>
        ${gauge(c.band === "high" && c.severe ? 1 : c.coercion_score, [cut.medium, cut.high], c.band, false)}
        <div class="g-num">${SP.pct(c.coercion_score)}</div><div class="g-band">${SP.esc(SP.t("band." + c.band))}${c.severe ? ` · <span class="tag tag-severe">${SP.esc(SP.t("why.severe"))}</span>` : ""}</div>
        <div class="src-tag src-ml">${SP.esc(SP.t("why.legend.rule"))} + ${SP.esc(SP.t("why.legend.ml"))}</div></div>
    </div>`;

    // -- factor bars
    const factors = ex.factors.slice(0, 8);
    const maxImp = Math.max(0.0001, ...factors.map(f => f.impact || 0));
    html += `<h3 class="why-h">${SP.esc(SP.t("why.factors"))}</h3>`;
    if (!factors.length) html += `<p class="muted">${SP.esc(SP.t("why.no_factors"))}</p>`;
    else {
      html += `<ol class="factors">${factors.map(f => {
        const s = SOURCE[f.source] || SOURCE.rule;
        const w = f.impact === null ? 34 : Math.max(4, 100 * (f.impact || 0) / maxImp);
        const tip = `${SP.t(s.key)} · ${f.text}${f.impact !== null ? ` · impact ${f.impact}` : ""}`;
        return `<li title="${SP.esc(tip)}"><div class="f-label">${f.severity === "severe" ? `<span class="tag tag-severe">${SP.esc(SP.t("why.severe"))}</span> ` : ""}${SP.esc(factorLabel(f, r.inputs))}
          ${SP.lang === "en" && f.source === "rule" ? `<span class="f-ev">${SP.esc(f.text)}</span>` : ""}</div>
          <div class="f-track"><span class="f-bar ${s.cls} ${f.impact === null ? "pattern" : ""}" style="width:${w.toFixed(1)}%"></span></div></li>`;
      }).join("")}</ol>
      <div class="legend">${Object.values(SOURCE).map(s => `<span><i class="sw ${s.cls}"></i>${SP.esc(SP.t(s.key))}</span>`).join("")}</div>`;
    }

    // -- contradictions (gentle)
    if (c.contradictions.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.contradictions"))}</h3>` +
      c.contradictions.map(k => `<div class="callout callout-gentle"><span class="callout-ico">?</span><p>${SP.esc(SP.t("contradiction." + k.id))}</p></div>`).join("");

    // -- behaviour context
    const devs = r.behaviour.deviations;
    if (devs.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.context"))}</h3><div class="chips">${devs.map(d =>
      `<span class="ctx-chip">${SP.esc(devText(d))}</span>`).join("")}</div>`;

    // -- notes, built from structured data so they translate
    const notes = [];
    if (c.payee_amount_gate === "applied") notes.push(SP.t("note.gate"));
    r.fusion.trail.forEach(s => { if (s.step === "A2") notes.push(s.note.includes("NOT") ? SP.t("note.trust_blocked") : SP.t("note.trust_applied")); });
    if (r.behaviour.splitting.detected) notes.push(SP.t("note.split"));
    if (notes.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.notes"))}</h3><ul class="notes">${notes.map(n => `<li>${SP.esc(n)}</li>`).join("")}</ul>`;

    html += `<div class="why-foot"><span class="rule-chip">${SP.esc(SP.t("why.decision_rule"))} ${row} · ${SP.esc(SP.t(r.stage === "post_pause" ? "why.after_answers" : "why.first_check"))}</span>
      <span>${Math.round(r.latency_ms)} ms</span></div>
      <p class="muted small">${SP.esc(SP.t("why.sources"))}</p>`;
    body.innerHTML = html;
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    await SP.ready;
    document.querySelectorAll("#callType button").forEach(b => b.addEventListener("click", () => setCallType(b.dataset.val)));
    document.querySelectorAll(".sim input").forEach(i => i.addEventListener("input", syncOutputs));
    $("#resetSim").addEventListener("click", () => { resetSimulator(); st.flow = null; st.form = { vpa: "", amount: "" }; renderForm(); renderWhy(); });
    $("#preset").addEventListener("change", e => loadPreset(e.target.value).catch(err => SP.toast(err.message, "error")));
    st.scenarios = (await SP.api("GET", "/api/scenarios")).scenarios;
    renderPresets();
    resetSimulator();
    renderForm(); renderWhy();
    SP.on("lang", () => { renderPresets(); rerenderScreen(); renderWhy(); });
    SP.on("user", () => {
      if (st.scenario && st.scenario.user_id !== SP.userId) { resetSimulator(); }
      st.flow = null; renderForm(); renderWhy();
    });
  }
  boot();
})();
