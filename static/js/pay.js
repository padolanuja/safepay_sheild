/* Payment console: simulated UPI app + signal console + risk analysis.

   Signals come from three places, and every one is labelled with its source:
     MEASURED   live capture of how the person actually uses the phone screen: time on screen,
                pauses while typing, amount typed in bursts, UPI ID pasted, leaving the app, clock.
     EMULATED   device events a browser cannot read (call state, screen share, app installs).
                The operator triggers them; on Android they come from OS APIs.
     DB         account history (usual amount, balance, new payee, 24 h velocity) from SQLite.
   Manual override and the guided scenarios set the same inputs by hand, and say so.

   Flow: Pay -> first check (pre_pause) -> ALLOW | WARN (questions -> post_pause) | HOLD.
   The server decides everything (final_decision = higher of the two passes). */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const SIT_BOOL = ["caller_in_contacts", "caller_is_international", "screen_share_active",
    "remote_access_app_installed_7d", "vpa_pasted", "payee_in_contacts", "fd_or_loan_liquidated_7d", "segmented_typing"];
  const SIT_RANGE = ["call_duration_min", "hesitation_events", "app_switches", "session_duration_sec", "hour_of_day"];
  const MEASURED = new Set(["session_duration_sec", "hesitation_events", "segmented_typing", "vpa_pasted", "app_switches", "hour_of_day"]);
  const NIGHT = [23, 0, 1, 2, 3, 4, 5];
  const VPA_RE = /^[a-zA-Z0-9.\-_]{2,256}@[a-zA-Z][a-zA-Z0-9]{1,63}$/;
  const SOURCE = {
    "rule": { key: "why.legend.rule", cls: "src-rule" },
    "model_prediction:coercion": { key: "why.legend.ml", cls: "src-ml" },
    "model_prediction:mule": { key: "why.legend.mule", cls: "src-mule" },
    "rule:splitting": { key: "why.legend.split", cls: "src-split" },
  };
  const ICON = { ALLOW: "check-circle", WARN: "alert", HOLD: "octagon" };
  const ic = (n, c) => SP.icon(n, c);
  // Where each device signal would come from in a production Android SDK.
  const PROD_SOURCE = {
    on_voice_call: "TelephonyManager call state", on_video_call: "Audio mode + foreground VoIP app",
    call_duration_min: "Call-state timestamps", caller_in_contacts: "ContactsContract lookup",
    caller_is_international: "Caller number country code", screen_share_active: "MediaProjection / display listener",
    remote_access_app_installed_7d: "PackageManager firstInstallTime", payee_in_contacts: "ContactsContract lookup",
    fd_or_loan_liquidated_7d: "Core-banking event feed", session_duration_sec: "In-app input telemetry",
    hesitation_events: "In-app keystroke timing", segmented_typing: "In-app keystroke timing",
    vpa_pasted: "Clipboard paste event", app_switches: "Activity lifecycle (onPause)", hour_of_day: "Device clock",
  };

  const st = {
    scenarios: [], scenario: null, callType: "none", flow: null, screen: "form",
    form: { vpa: "", amount: "" }, payee: null, answers: null, timer: null, holdEnds: null,
    mode: "live", tab: "explain", whatif: null, whatifFor: null,
  };

  // ------------------------------------------------------------------ event log
  function log(text, kind = "measured") {
    const ol = $("#eventLog");
    if (!ol) return;
    const t = new Date();
    const ts = [t.getHours(), t.getMinutes(), t.getSeconds()].map(n => String(n).padStart(2, "0")).join(":");
    const li = document.createElement("li");
    li.className = "ev ev-" + kind;
    li.innerHTML = `<time>${ts}</time><span class="ev-k">${kind === "measured" ? "MEAS" : kind === "emulated" ? "EMUL" : kind === "engine" ? "ENGINE" : "INFO"}</span><span>${SP.esc(text)}</span>`;
    ol.prepend(li);
    while (ol.children.length > 60) ol.lastChild.remove();
  }

  // ------------------------------------------------------------------ live capture
  // Thresholds are deliberately simple and stated: a pause >= 2 s between keystrokes is a hesitation;
  // the amount typed in 2+ bursts separated by >= 1.2 s is stop-start (dictated) typing.
  const cap = {
    reset() {
      this.start = null; this.last = {}; this.focusAt = {}; this.typed = {};
      this.hes = 0; this.bursts = 0; this.segmented = false; this.pasted = false; this.switches = 0;
      this.lastWasDelete = false; this.frozen = null;
    },
    begin() { if (!this.start) { this.start = Date.now(); log("Payment session started"); } },
    focus(f) { this.begin(); if (!this.typed[f]) this.focusAt[f] = Date.now(); },
    input(f, e) {
      this.begin();
      const now = Date.now(), last = this.last[f];
      if (!last && this.focusAt[f] && !this.typed[f]) {
        const wait = (now - this.focusAt[f]) / 1000;
        if (wait >= 3) { this.hes++; log(`Waited ${wait.toFixed(1)} s before typing the ${f === "amount" ? "amount" : "UPI ID"}`); }
      }
      if (last) {
        const gap = (now - last) / 1000;
        if (gap >= 2) { this.hes++; log(`Paused ${gap.toFixed(1)} s while typing the ${f === "amount" ? "amount" : "UPI ID"}`); }
        if (f === "amount" && gap >= 1.2) {
          this.bursts++;
          if (this.bursts >= 2 && !this.segmented) { this.segmented = true; log("Amount typed in separate bursts (dictation pattern)"); }
        }
      }
      const del = e && e.inputType && e.inputType.startsWith("delete");
      if (f === "amount" && del && !this.lastWasDelete && this.typed[f]) { this.hes++; log("Deleted and re-typed digits in the amount"); }
      this.lastWasDelete = !!del;
      this.typed[f] = true; this.last[f] = now;
    },
    paste(f) { this.begin(); if (f === "vpa") { this.pasted = true; log("UPI ID pasted from the clipboard"); } },
    leftApp(emulated) {
      this.begin(); this.switches++;
      log(emulated ? `Switched to another app (#${this.switches})` : `Left the payment app: tab hidden (#${this.switches})`, emulated ? "emulated" : "measured");
    },
    seconds() { return this.start ? Math.max(1, Math.round(((this.frozen || Date.now()) - this.start) / 1000)) : 0; },
  };

  const call = { startedAt: null, speed: 10, baseMin: 0 };
  function callMinutes() {
    if (st.callType === "none" || !call.startedAt) return 0;
    return Math.min(180, call.baseMin + (Date.now() - call.startedAt) / 60000 * call.speed);
  }
  function fmtMin(m) { const s = Math.floor(m * 60); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; }

  function tick() {
    // write live values into the inputs the request is built from
    if (st.mode === "live") {
      const now = new Date();
      $("#session_duration_sec").value = Math.min(600, Math.max(5, cap.seconds() || 5));
      $("#hesitation_events").value = Math.min(10, cap.hes);
      $("#app_switches").value = Math.min(10, cap.switches);
      $("#segmented_typing").checked = cap.segmented;
      $("#vpa_pasted").checked = cap.pasted;
      $("#hour_of_day").value = now.getHours();
      if (st.callType !== "none") $("#call_duration_min").value = Math.floor(callMinutes());
    }
    const s = getSituation();
    $("#tSession").textContent = (st.mode === "live" ? cap.seconds() : s.session_duration_sec) + " s";
    $("#tHes").textContent = s.hesitation_events;
    $("#tSeg").textContent = s.segmented_typing ? "Yes" : "No";
    $("#tPaste").textContent = s.vpa_pasted ? "Yes" : "No";
    $("#tSwitch").textContent = s.app_switches;
    const now = new Date();
    const hh = String(s.hour_of_day).padStart(2, "0");
    $("#tHour").textContent = st.mode === "live" ? `${hh}:${String(now.getMinutes()).padStart(2, "0")}` : `${hh}:00`;
    document.querySelectorAll("#tele .tele").forEach(el => {
      const k = el.dataset.k;
      const hot = (k === "hes" && s.hesitation_events >= 4) || (k === "seg" && s.segmented_typing) || (k === "paste" && s.vpa_pasted)
        || (k === "switch" && s.app_switches >= 4) || (k === "hour" && NIGHT.includes(s.hour_of_day));
      el.classList.toggle("hot", !!hot);
    });
    const m = st.mode === "live" ? callMinutes() : Number($("#call_duration_min").value);
    $("#callClock").textContent = fmtMin(m);
    $("#callPill").hidden = st.callType === "none";
    $("#callPillTime").textContent = fmtMin(m);
    syncOutputs();
  }

  // ------------------------------------------------------------------ simulator inputs
  function setCallType(v, { quiet } = {}) {
    const was = st.callType;
    st.callType = v;
    document.querySelectorAll("#callType button").forEach(b => b.setAttribute("aria-pressed", b.dataset.val === v));
    $("#callDetails").classList.toggle("disabled", v === "none");
    $("#callDetails").querySelectorAll("input").forEach(i => (i.disabled = v === "none" || (st.mode === "live" && i.id === "call_duration_min")));
    if (v === "none") { $("#call_duration_min").value = 0; call.startedAt = null; call.baseMin = 0; }
    else if (was === "none") {
      if (st.mode === "live") { call.startedAt = Date.now(); call.baseMin = 0; $("#call_duration_min").value = 0; }
      else if (Number($("#call_duration_min").value) === 0) $("#call_duration_min").value = 10;
    }
    if (!quiet && was !== v) log(v === "none" ? "Call ended" : `${v === "video" ? "Video" : "Voice"} call active${$("#caller_in_contacts").checked ? " (saved contact)" : $("#caller_is_international").checked ? " (international number)" : " (unknown number)"}`, "emulated");
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
    const s = { on_voice_call: st.callType === "voice" ? 1 : 0, on_video_call: st.callType === "video" ? 1 : 0 };
    SIT_BOOL.forEach(k => (s[k] = $("#" + k).checked ? 1 : 0));
    SIT_RANGE.forEach(k => (s[k] = Number($("#" + k).value)));
    if (st.callType === "none") { s.call_duration_min = 0; s.caller_in_contacts = 0; s.caller_is_international = 0; }
    if (st.scenario && st.scenario.situation.simulated_day_of_month) s.simulated_day_of_month = st.scenario.situation.simulated_day_of_month;
    return s;
  }

  function setSituation(s) {
    setCallType(s.on_video_call ? "video" : s.on_voice_call ? "voice" : "none", { quiet: true });
    SIT_BOOL.forEach(k => ($("#" + k).checked = !!s[k]));
    SIT_RANGE.forEach(k => { if (s[k] !== undefined) $("#" + k).value = s[k]; });
    syncOutputs();
  }

  function setMode(m, { quiet } = {}) {
    st.mode = m;
    document.querySelectorAll("#captureMode button").forEach(b => b.setAttribute("aria-pressed", b.dataset.val === m));
    document.querySelector(".console").classList.toggle("is-live", m === "live");
    document.querySelector(".console").classList.toggle("is-manual", m === "manual");
    $("#behaviourPill").textContent = m === "live" ? "Measured" : "Manual";
    $("#behaviourPill").className = "src-pill " + (m === "live" ? "measured" : "manual");
    $("#modeNote").innerHTML = m === "live"
      ? `${ic("radio")} <span><b>Live capture.</b> Type in the phone: your pauses, bursts, pastes and tab switches are measured and sent to the engine. Nothing is preset.</span>`
      : `${ic("sliders")} <span><b>Manual override.</b> Every behaviour value is set by hand and labelled “manual” in the analysis.</span>`;
    $("#call_duration_min").disabled = st.callType === "none" || m === "live";
    if (m === "live" && st.callType !== "none" && !call.startedAt) { call.startedAt = Date.now(); call.baseMin = Number($("#call_duration_min").value); }
    if (!quiet) log(m === "live" ? "Switched to live capture" : "Switched to manual override", "info");
    tick();
  }

  function resetSimulator() {
    st.scenario = null; st.answers = null;
    $("#preset").value = "";
    $("#presetInfo").hidden = true;
    $("#newPayeesWrap").classList.remove("disabled"); $("#new_payees_24h").disabled = false;
    $("#new_payees_24h").value = 0;
    setSituation({ hour_of_day: new Date().getHours(), session_duration_sec: 45, hesitation_events: 0, app_switches: 0, call_duration_min: 0 });
    cap.reset();
    hideOverlay();
  }

  function renderPresets() {
    const sel = $("#preset");
    const cur = sel.value;
    const show = $("#showExpected").checked;
    sel.innerHTML = `<option value="">${SP.esc(SP.t("sim.preset_none"))}</option>` + st.scenarios.map(s =>
      `<option value="${s.id}">${s.id}. ${SP.esc(s.title)}${show ? " → " + s.expected_decisions.join(" / ") : ""}</option>`).join("");
    sel.value = cur;
  }

  function presetInfo() {
    const s = st.scenario, info = $("#presetInfo");
    if (!s) { info.hidden = true; return; }
    const show = $("#showExpected").checked || (st.flow && st.flow.final);
    const matched = st.flow && st.flow.final ? s.expected_decisions.includes(st.flow.final) : null;
    info.hidden = false;
    info.innerHTML = `<p>${SP.esc(s.description)}</p>
      <p class="expected"><span class="muted">${SP.esc(SP.t("sim.expected"))}:</span>
        ${show ? s.expected_decisions.map(d => `<span class="badge b-${d}">${ic(ICON[d])} ${SP.esc(SP.t("decision." + d))}</span>`).join(" ")
          : `<span class="badge b-hidden">${ic("eye-off")} hidden until the engine decides</span>`}
        ${matched === null ? "" : matched ? `<span class="badge b-ALLOW">${ic("check")} engine matched</span>` : `<span class="badge b-HOLD">${ic("x")} engine differed</span>`}</p>
      ${s.prior_payments.length ? `<p class="muted small">${s.prior_payments.length} earlier payment(s) to this payee loaded (demo data).</p>` : ""}
      <p class="muted small">${SP.esc(SP.t("sim.answers_note"))}</p>`;
  }

  async function loadPreset(id) {
    if (!id) { resetSimulator(); setMode("live"); renderForm(); return; }
    const s = await SP.api("POST", `/api/scenarios/${id}/prepare`);
    setMode("manual", { quiet: true });
    st.scenario = s; st.answers = s.answers;
    if (SP.userId !== s.user_id) await SP.setUser(s.user_id, { followLanguage: false });
    st.scenario = s; st.answers = s.answers;
    st.form = { vpa: s.payee_vpa, amount: String(s.amount) };
    setSituation(s.situation);
    $("#newPayeesWrap").classList.add("disabled"); $("#new_payees_24h").disabled = true;
    log(`Guided scenario ${s.id} loaded: signals set by the scenario script`, "info");
    st.flow = null; presetInfo(); renderForm(); renderWhy();
  }

  // ------------------------------------------------------------------ incoming call overlay
  function hideOverlay() { const o = $("#callOverlay"); if (o) { o.hidden = true; o.innerHTML = ""; } }
  function ring() {
    const who = $("#ringWho").value, kind = $("#ringKind").value;
    const num = who === "international" ? "+44 20 •••• 7781" : who === "contact" ? "Ravi (Son)" : "+91 7•••• ••482";
    const sub = who === "contact" ? "Mobile · saved contact" : who === "international" ? "United Kingdom · not in contacts" : "India · not in contacts";
    const o = $("#callOverlay");
    o.innerHTML = `<div class="co-inner"><div class="co-avatar">${ic(who === "contact" ? "user" : "phone")}</div>
      <div class="co-kind">Incoming ${kind} call</div><div class="co-num">${SP.esc(num)}</div><div class="co-sub">${SP.esc(sub)}</div>
      <div class="co-actions"><button type="button" class="co-btn decline" id="coDecline">${ic("phone-off")}<span>Decline</span></button>
      <button type="button" class="co-btn accept" id="coAccept">${ic(kind === "video" ? "video" : "phone")}<span>Accept</span></button></div></div>`;
    o.hidden = false;
    log(`Incoming ${kind} call from ${who === "contact" ? "a saved contact" : who === "international" ? "an international number" : "an unknown number"}`, "emulated");
    $("#coDecline").addEventListener("click", () => { hideOverlay(); log("Call declined", "emulated"); });
    $("#coAccept").addEventListener("click", () => {
      hideOverlay();
      $("#caller_in_contacts").checked = who === "contact";
      $("#caller_is_international").checked = who === "international";
      setCallType("none", { quiet: true });
      setCallType(kind);
    });
  }

  // ------------------------------------------------------------------ phone screens
  const screen = () => $("#screen");
  function stopTimer() { if (st.timer) { clearInterval(st.timer); st.timer = null; } }

  function renderForm() {
    stopTimer(); st.screen = "form";
    const u = SP.user();
    screen().innerHTML = `
      <div class="balance-card"><div><small>${SP.esc(SP.t("pay.balance"))}</small><b>${SP.inr(u.balance_inr)}</b></div>
        <span class="acct">${ic("user")} ${SP.esc(u.name)}</span></div>
      <form id="payForm" class="pay-form" novalidate>
        <label class="field"><span>${SP.esc(SP.t("pay.upi_id"))}</span>
          <input id="vpa" autocomplete="off" inputmode="email" placeholder="${SP.esc(SP.t("pay.upi_placeholder"))}" value="${SP.esc(st.form.vpa)}"></label>
        <div id="payeeInfo" class="payee-info"></div>
        <label class="field"><span>${SP.esc(SP.t("pay.amount"))}</span>
          <div class="amount-wrap"><span>₹</span><input id="amount" inputmode="numeric" placeholder="0" value="${SP.esc(st.form.amount)}"></div></label>
        <p id="formErr" class="form-err" role="alert"></p>
        <button class="btn btn-primary btn-lg btn-block" type="submit">${ic("lock")} ${SP.esc(SP.t("pay.button"))}</button>
        <p class="secure-note">${ic("shield")} Protected by SafePay Shield</p>
      </form>`;
    const vpa = $("#vpa"), amt = $("#amount");
    let deb;
    vpa.addEventListener("focus", () => cap.focus("vpa"));
    amt.addEventListener("focus", () => cap.focus("amount"));
    vpa.addEventListener("paste", () => cap.paste("vpa"));
    vpa.addEventListener("input", e => { cap.input("vpa", e); st.form.vpa = vpa.value.trim(); clearTimeout(deb); deb = setTimeout(lookup, 350); });
    amt.addEventListener("input", e => { cap.input("amount", e); amt.value = amt.value.replace(/[^0-9]/g, ""); st.form.amount = amt.value; });
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
    tick(); cap.frozen = Date.now();
    renderChecking();
    try {
      if (st.scenario) await SP.api("POST", `/api/scenarios/${st.scenario.id}/prepare`);   // fresh earlier payments
      else await SP.api("POST", "/api/simulator/new-payee-payments", { user_id: SP.userId, count: Number($("#new_payees_24h").value) });
      const situation = getSituation();
      const body = { user_id: SP.userId, payee_vpa: vpa, amount, situation, stage: "pre_pause" };
      if (!st.payee) body.payee_type = "P2P";
      log(`Sent to engine: ${SP.inr(amount)} → ${vpa.replace(/^(.{2}).*(@.*)$/, "$1•••$2")}`, "engine");
      const [res] = await Promise.all([SP.api("POST", "/api/assess", body), delay(1100)]);
      st.flow = { pre: res, post: null, final: res.decision, body, mode: st.scenario ? "scenario" : st.mode };
      st.whatif = null; st.whatifFor = null;
      log(`First check: ${res.decision} (rule ${res.fusion.matrix_row}, ${Math.round(res.latency_ms)} ms)`, "engine");
      presetInfo(); renderWhy(); loadWhatIf();
      if (res.decision === "ALLOW") return allow(res);
      if (res.decision === "WARN") return renderQuestions("warn");
      return renderHold();
    } catch (e) {
      cap.frozen = null;
      renderForm(); $("#formErr").textContent = e.message;
    }
  }

  function renderChecking() {
    stopTimer(); st.screen = "checking";
    const steps = ["Payee account history (mule model)", "Your behaviour on this screen", "Call and device context", "Fusion decision"];
    screen().innerHTML = `<div class="checking"><div class="radar"><span></span><span></span>${ic("shield")}</div>
      <h3>${SP.esc(SP.t("pay.checking"))}</h3><p class="muted">${SP.esc(SP.t("pay.checking_sub"))}</p>
      <ul class="check-steps">${steps.map((s, i) => `<li style="animation-delay:${i * 0.25}s">${ic("check")} ${s}</li>`).join("")}</ul></div>`;
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
      <div class="result-icon">${ic(kind === "WARN" ? "alert" : "check")}</div>
      <h3>${SP.esc(SP.t(titleKey))}</h3>
      ${res ? `<p class="amount-big">${SP.inr(res.amount)}</p><p class="muted">${SP.esc(res.payee.display_name || res.payee.vpa)}</p>
        <p class="txn-ref">Ref SIM${String(res.assessment_id).padStart(8, "0")}</p>` : ""}
      <p class="muted small">${SP.esc(SP.t(subKey))}</p>
      <button class="btn btn-outline btn-block" id="newPay">${ic("refresh")} ${SP.esc(SP.t("result.new_payment"))}</button></div>`;
    $("#newPay").addEventListener("click", newPayment);
  }

  function newPayment() {
    st.flow = null; st.whatif = null; cap.reset();
    if (st.mode === "live") st.form = { vpa: "", amount: "" };
    log("New payment", "info");
    presetInfo(); renderForm(); renderWhy();
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
      ? `<div class="banner-warn"><span class="b-ico">${ic("alert")}</span><div><strong>${SP.esc(SP.t("result.warn.title"))}</strong><p>${SP.esc(SP.t("result.warn.sub"))}</p></div></div>`
      : `<div class="banner-hold"><span class="b-ico">${ic("octagon")}</span><div><strong>${SP.esc(SP.t("result.hold.title"))}</strong></div></div>`;
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
        log(`After answers: ${res.decision} → final ${res.final_decision}`, "engine");
        presetInfo(); renderWhy();
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
      <div class="result-icon">${ic("alert")}</div><h3>${SP.esc(SP.t("result.verified.title"))}</h3>
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
    log("Payment cancelled by the user", "info");
    resultScreen("CANCEL", "result.cancelled", "app.simulated", null);
  }

  async function renderHold() {
    st.screen = "hold";
    const a = decisive("HOLD");
    const mins = a.response.cooling_off_minutes;
    if (!st.flow.holdEnds) st.flow.holdEnds = Date.now() + mins * 60000;   // one cooling-off per flow
    st.holdEnds = st.flow.holdEnds;
    let contacts = [];
    try { contacts = (await SP.api("GET", `/api/contacts?user_id=${SP.userId}`)).contacts.filter(c => c.consent_given); } catch (e) { /* none */ }
    const asked = !!st.flow.post;
    screen().innerHTML = `<div class="result r-HOLD hold">
      <div class="result-icon">${ic("octagon")}</div>
      <h3>${SP.esc(SP.t("result.hold.title"))}</h3>
      <p class="muted small">${SP.esc(SP.t("result.hold.sub"))}</p>
      <div class="countdown" aria-live="off"><small>${SP.esc(SP.t("result.hold.countdown"))}</small><span id="cd">--:--</span></div>
      <p class="hold-msg">${SP.esc(SP.t("result.hold.message"))}</p>
      <a class="helpline" href="tel:${a.response.helpline}">${ic("phone")} ${SP.esc(SP.t("result.hold.helpline"))}</a>
      <a class="small" href="${a.response.helpline_url}" target="_blank" rel="noopener">${SP.esc(SP.t("result.hold.website"))}</a>
      <button class="btn btn-primary btn-lg btn-block" id="hCancel">${SP.esc(SP.t("btn.cancel"))}</button>
      ${contacts.length ? `<button class="btn btn-outline btn-block" id="hAlert" ${st.flow.sms ? "disabled" : ""}>${ic("message")} ${SP.esc(SP.t("btn.alert_contact"))} · ${SP.esc(contacts[0].name.split(" ")[0])}</button>`
        : `<p class="muted small">${SP.esc(SP.t("alert.no_contact"))}</p>`}
      <div id="smsPreview">${st.flow.sms || ""}</div>
      ${asked ? "" : `<button class="btn btn-link btn-block" id="hTell">${SP.esc(SP.t("q.checklist").replace(/\s*\(.*\)/, ""))}…</button>`}
      <button class="btn btn-ghost btn-block" id="hPay" disabled>${ic("lock")} ${SP.esc(SP.t("btn.pay_anyway"))}</button>
    </div>`;
    const t = () => {
      const left = Math.max(0, st.holdEnds - Date.now());
      const el = $("#cd"); if (!el) return stopTimer();
      el.textContent = `${String(Math.floor(left / 60000)).padStart(2, "0")}:${String(Math.floor(left / 1000) % 60).padStart(2, "0")}`;
      if (left === 0) { $("#hPay").disabled = false; $("#hPay").textContent = SP.t("btn.pay_anyway"); stopTimer(); }
    };
    stopTimer(); t(); st.timer = setInterval(t, 1000);
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
        log(`Trusted contact alerted (simulated SMS to ${r.to})`, "engine");
      } catch (e) { SP.toast(e.message, "error"); }
    });
  }

  function rerenderScreen() {
    if (!st.flow || st.screen === "form") return renderForm();
    if (st.screen.startsWith("questions")) return renderQuestions(st.screen.split(":")[1]);
    if (st.screen === "hold") return renderHold();
    if (st.screen === "verified") return renderVerified();
  }

  // ------------------------------------------------------------------ Risk analysis panel
  function gauge(value, markers, band, disabled) {
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
    if (typeof v === "string") return v;
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

  function setTab(t) {
    st.tab = t;
    document.querySelectorAll("#whyTabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === t));
    renderWhy();
  }

  function renderWhy() {
    const body = $("#whyBody");
    document.querySelectorAll("#whyTabs button").forEach(b => (b.disabled = !st.flow));
    if (!st.flow) {
      const wt = document.querySelector('#whyTabs [data-tab="whatif"]');
      if (wt) delete wt.dataset.count;
      if (st.tab !== "explain") { st.tab = "explain"; document.querySelectorAll("#whyTabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === "explain")); }
      body.innerHTML = `<div class="why-empty">${ic("gauge")}<p>${SP.esc(SP.t("why.empty"))}</p>
        <ol class="howto"><li><b>Type it yourself.</b> Enter a UPI ID and amount on the phone; hesitate, paste, switch tabs: it is all measured.</li>
        <li><b>Add pressure.</b> Ring the phone with an unknown caller, turn on screen sharing.</li>
        <li><b>Probe it.</b> After a decision, open <em>What-if</em> to see which signal would change it.</li></ol></div>`;
      return;
    }
    if (st.tab === "whatif") return renderWhatIf(body);
    if (st.tab === "inputs") return renderInputs(body);
    renderExplain(body);
  }

  function renderExplain(body) {
    const { pre, post, final } = st.flow;
    const r = post || pre;
    const ex = r.explanation, c = r.coercion, m = r.mule;
    const row = r.fusion.matrix_row;

    let html = `<div class="why-decision d-${final}"><span class="chip c-${final}">${ic(ICON[final])} ${SP.esc(SP.t("decision." + final))}</span>
      ${post ? `<span class="timeline">${SP.esc(SP.t("why.first_check"))}: <b>${SP.esc(SP.t("decision_short." + pre.decision))}</b>
        ${ic("chevron-right")} ${SP.esc(SP.t("why.after_answers"))}: <b>${SP.esc(SP.t("decision_short." + post.decision))}</b></span>` : ""}
      <span class="mode-tag ${st.flow.mode}">${st.flow.mode === "live" ? ic("radio") + " live-captured" : st.flow.mode === "scenario" ? ic("book") + " guided scenario" : ic("sliders") + " manual"}</span></div>
      <p class="why-reason">${SP.esc(SP.t("reason." + row))}</p>`;
    const severe = c.indicators.filter(i => i.severity === "severe" && i.counted);
    if (severe.length) html += `<ul class="severe-list">${severe.map(i => `<li><span class="tag tag-severe">${SP.esc(SP.t("why.severe"))}</span> ${SP.esc(SP.t("rule." + i.id))}</li>`).join("")}</ul>`;

    if (ex.decided_on_coercion_only) {
      html += `<div class="callout callout-key"><span class="callout-ico">${ic("info")}</span><div>
        <strong>${SP.esc(SP.t("why.coercion_only_title"))}</strong><p>${SP.esc(SP.t("why.coercion_only_body"))}</p></div></div>`;
    }

    const muleOk = m.status === "ok";
    const muleTxt = muleOk
      ? `<div class="g-num">${SP.pct(m.mule_probability)}</div><div class="g-band">${SP.esc(SP.t("band." + m.band))}</div>`
      : `<div class="g-num off">${SP.esc(SP.t("why.not_checked"))}</div><div class="g-note">${SP.esc(m.status === "insufficient_history"
          ? SP.t("why.too_little_history", { n: m.transactions, min: m.min_history }) : SP.t("why.model_unavailable"))}</div>`;
    const cut = c.cutoffs;
    html += `<div class="gauges">
      <div class="gauge-card ${muleOk ? "" : "is-off"}"><h4>${ic("bank")} ${SP.esc(SP.t("why.mule"))}</h4>
        ${gauge(muleOk ? m.mule_probability : 0, [0.10, 0.40], muleOk ? m.band : "low", !muleOk)}${muleTxt}
        <div class="src-tag src-mule">${SP.esc(SP.t("why.legend.mule"))}</div></div>
      <div class="gauge-card"><h4>${ic("user")} ${SP.esc(SP.t("why.coercion"))}</h4>
        ${gauge(c.band === "high" && c.severe ? 1 : c.coercion_score, [cut.medium, cut.high], c.band, false)}
        <div class="g-num">${SP.pct(c.coercion_score)}</div><div class="g-band">${SP.esc(SP.t("band." + c.band))}${c.severe ? ` · <span class="tag tag-severe">${SP.esc(SP.t("why.severe"))}</span>` : ""}</div>
        <div class="src-tag src-ml">${SP.esc(SP.t("why.legend.rule"))} + ${SP.esc(SP.t("why.legend.ml"))}</div></div>
    </div>`;

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

    if (c.contradictions.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.contradictions"))}</h3>` +
      c.contradictions.map(k => `<div class="callout callout-gentle"><span class="callout-ico">?</span><p>${SP.esc(SP.t("contradiction." + k.id))}</p></div>`).join("");

    const devs = r.behaviour.deviations;
    if (devs.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.context"))}</h3><div class="chips">${devs.map(d =>
      `<span class="ctx-chip">${SP.esc(devText(d))}</span>`).join("")}</div>`;

    const notes = [];
    if (c.payee_amount_gate === "applied") notes.push(SP.t("note.gate"));
    r.fusion.trail.forEach(s => { if (s.step === "A2") notes.push(s.note.includes("NOT") ? SP.t("note.trust_blocked") : SP.t("note.trust_applied")); });
    if (r.behaviour.splitting.detected) notes.push(SP.t("note.split"));
    if (notes.length) html += `<h3 class="why-h">${SP.esc(SP.t("why.notes"))}</h3><ul class="notes">${notes.map(n => `<li>${SP.esc(n)}</li>`).join("")}</ul>`;

    html += `<div class="why-foot"><span class="rule-chip">${SP.esc(SP.t("why.decision_rule"))} ${row} · ${SP.esc(SP.t(r.stage === "post_pause" ? "why.after_answers" : "why.first_check"))}</span>
      <span class="mono">${Math.round(r.latency_ms)} ms</span></div>
      <p class="muted small">${SP.esc(SP.t("why.sources"))}</p>`;
    body.innerHTML = html;
  }

  // -- What-if: re-run the real engine with one signal changed at a time
  async function loadWhatIf() {
    const f = st.flow;
    if (!f) return;
    const key = f.pre.assessment_id;
    st.whatifFor = key; st.whatif = "loading";
    if (st.tab === "whatif") renderWhy();
    try {
      const b = f.body;
      const r = await SP.api("POST", "/api/lab/what-if", { user_id: b.user_id, payee_vpa: b.payee_vpa, amount: b.amount,
        situation: b.situation, payee_type: b.payee_type });
      if (st.whatifFor !== key) return;
      st.whatif = r;
      const flips = r.probes.filter(p => p.flips).length;
      log(`What-if: ${r.probes.length} counterfactuals run, ${flips} change the decision`, "engine");
    } catch (e) { if (st.whatifFor === key) st.whatif = { error: e.message }; }
    if (st.tab === "whatif") renderWhy();
    const t = document.querySelector('#whyTabs [data-tab="whatif"]');
    if (t && st.whatif && st.whatif.probes) t.dataset.count = st.whatif.probes.filter(p => p.flips).length || "";
  }

  function renderWhatIf(body) {
    const w = st.whatif;
    if (!w || w === "loading") { body.innerHTML = `<div class="loading-row"><span class="spinner"></span> Re-running the engine with each signal changed…</div>`; return; }
    if (w.error) { body.innerHTML = `<p class="form-err">${SP.esc(w.error)}</p>`; return; }
    const base = w.base;
    let html = `<p class="lead">Each row re-runs the <b>same models and rules</b> with one signal removed. A scripted demo can't do this: the answer is computed, not looked up.</p>
      <div class="wi-base"><span>Current first check</span><span class="chip sm c-${base.decision}">${ic(ICON[base.decision])} ${base.decision}</span><span class="mono">coercion ${SP.pct(base.coercion_score)}</span></div>`;
    if (!w.probes.length) html += `<p class="muted">No pressure signals are active, so there is nothing to remove. Add a call or screen share and pay again.</p>`;
    else {
      const maxD = Math.max(0.05, ...w.probes.map(p => Math.abs(p.delta)));
      html += `<ol class="wi-list">${w.probes.map(p => `<li class="${p.flips ? "flip" : ""}">
        <div class="wi-label">${SP.esc(p.label)}${p.flips ? ` <span class="tag tag-flip">changes decision</span>` : ""}</div>
        <div class="wi-row"><div class="wi-bar"><span class="${p.delta <= 0 ? "down" : "up"}" style="width:${(100 * Math.abs(p.delta) / maxD).toFixed(1)}%"></span></div>
          <span class="mono wi-delta">${p.delta > 0 ? "+" : ""}${(100 * p.delta).toFixed(1)} pts</span>
          <span class="chip sm c-${p.decision}">${ic(ICON[p.decision])} ${p.decision}</span></div></li>`).join("")}</ol>`;
      if (base.decision === "HOLD" && !w.probes.some(p => p.flips))
        html += `<div class="callout callout-gentle"><span class="callout-ico">${ic("info")}</span><p>No single change lifts the hold: several independent warning signs agree. That's what makes this decision robust.</p></div>`;
    }
    if (w.amount_sweep && w.amount_sweep.length > 1) html += `<h3 class="why-h">Same situation, different amounts</h3>${sweepChart(w.amount_sweep, st.flow.body.amount)}`;
    body.innerHTML = html;
  }

  function sweepChart(pts, cur) {
    const W = 360, H = 130, P = 26;
    const xs = pts.map(p => Math.log10(p.amount)), x0 = Math.min(...xs), x1 = Math.max(...xs);
    const X = v => P + (W - 2 * P) * (x1 === x0 ? 0.5 : (Math.log10(v) - x0) / (x1 - x0));
    const Y = v => H - P - (H - 2 * P) * v;
    const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p.amount).toFixed(1)},${Y(p.coercion_score).toFixed(1)}`).join(" ");
    return `<svg class="sweep" viewBox="0 0 ${W} ${H}" role="img" aria-label="Coercion score by amount">
      <line class="ax" x1="${P}" y1="${H - P}" x2="${W - P}" y2="${H - P}"/>
      ${[0, 0.5, 1].map(v => `<line class="grid" x1="${P}" x2="${W - P}" y1="${Y(v)}" y2="${Y(v)}"/><text class="tk" x="${P - 4}" y="${Y(v) + 3}" text-anchor="end">${v * 100}</text>`).join("")}
      <path class="ln" d="${path}"/>
      ${pts.map(p => `<circle class="pt d-${p.decision} ${p.amount === Math.round(cur) ? "cur" : ""}" cx="${X(p.amount)}" cy="${Y(p.coercion_score)}" r="${p.amount === Math.round(cur) ? 6 : 4.5}"><title>${SP.inr(p.amount)}: ${p.decision} (${SP.pct(p.coercion_score)})</title></circle>
        <text class="tk" x="${X(p.amount)}" y="${H - P + 14}" text-anchor="middle">${p.amount >= 1000 ? Math.round(p.amount / 1000) + "k" : p.amount}</text>`).join("")}
    </svg><div class="legend"><span><i class="sw d-ALLOW"></i>ALLOW</span><span><i class="sw d-WARN"></i>WARN</span><span><i class="sw d-HOLD"></i>HOLD</span><span class="muted">y = coercion score (%), larger dot = this payment</span></div>`;
  }

  // -- Inputs & sources: every value the engine saw and where it came from
  function renderInputs(body) {
    const r = st.flow.post || st.flow.pre;
    const mode = st.flow.mode;
    const label = (k, src) => {
      if (src === "simulated_device_signal") {
        if (MEASURED.has(k)) return mode === "live" ? ["measured", "Measured live"] : ["manual", mode === "scenario" ? "Scenario script" : "Manual"];
        return ["emulated", "Emulated device event"];
      }
      if (src === "clock") return mode === "live" ? ["measured", "Device clock"] : ["manual", "Clock"];
      return { db_derived: ["db", "Account history (DB)"], user_entered: ["user", "Typed by user"], user_answer: ["user", "User's answer"] }[src] || ["db", src];
    };
    const order = { measured: 0, emulated: 1, user: 2, db: 3, manual: 4 };
    const rows = Object.entries(r.inputs).map(([k, v]) => ({ k, v: v.value, l: label(k, v.source) }))
      .filter(x => x.k !== "stage").sort((a, b) => order[a.l[0]] - order[b.l[0]]);
    const counts = rows.reduce((m, x) => (m[x.l[0]] = (m[x.l[0]] || 0) + 1, m), {});
    body.innerHTML = `<p class="lead">Every value the engine used for this decision, and where it came from. Emulated values show which Android API would provide them in production.</p>
      <div class="src-summary">${["measured", "emulated", "user", "db", "manual"].filter(k => counts[k]).map(k =>
        `<span class="src-pill ${k}">${counts[k]} ${{ measured: "measured", emulated: "emulated", user: "user input", db: "from history", manual: "manual" }[k]}</span>`).join("")}</div>
      <div class="table-wrap"><table class="prov"><thead><tr><th>Signal</th><th class="num">Value</th><th>Source</th><th>Production source</th></tr></thead><tbody>
      ${rows.map(x => `<tr><td>${SP.esc(SP.has("feat." + x.k) ? SP.t("feat." + x.k) : x.k)}</td><td class="num mono">${SP.esc(featValue(x.k, x.v))}</td>
        <td><span class="src-pill ${x.l[0]}">${x.l[1]}</span></td><td class="muted small">${SP.esc(PROD_SOURCE[x.k] || (x.l[0] === "db" ? "Bank's transaction ledger" : "—"))}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  // ------------------------------------------------------------------ boot
  async function boot() {
    await SP.ready;
    cap.reset();
    document.querySelectorAll("#callType button").forEach(b => b.addEventListener("click", () => setCallType(b.dataset.val)));
    document.querySelectorAll("#captureMode button").forEach(b => b.addEventListener("click", () => { if (b.dataset.val !== st.mode) { if (st.scenario && b.dataset.val === "live") { resetSimulator(); } setMode(b.dataset.val); } }));
    document.querySelectorAll("#callSpeed button").forEach(b => b.addEventListener("click", () => {
      if (st.callType !== "none" && call.startedAt) { call.baseMin = callMinutes(); call.startedAt = Date.now(); }
      call.speed = Number(b.dataset.val);
      document.querySelectorAll("#callSpeed button").forEach(x => x.setAttribute("aria-pressed", x === b));
      log(`Call clock speed ${call.speed}× (demo time compression)`, "info");
    }));
    document.querySelector(`#callSpeed button[data-val="10"]`).setAttribute("aria-pressed", "true");
    document.querySelectorAll(".sim input").forEach(i => i.addEventListener("input", syncOutputs));
    ["screen_share_active", "remote_access_app_installed_7d", "payee_in_contacts", "fd_or_loan_liquidated_7d"].forEach(id =>
      $("#" + id).addEventListener("change", e => log(`${e.target.closest("label").textContent.trim()}: ${e.target.checked ? "on" : "off"}`, "emulated")));
    $("#ringBtn").addEventListener("click", ring);
    $("#simSwitch").addEventListener("click", () => cap.leftApp(true));
    $("#clearLog").addEventListener("click", () => { $("#eventLog").innerHTML = ""; });
    $("#showExpected").addEventListener("change", () => { renderPresets(); presetInfo(); });
    document.querySelectorAll("#whyTabs button").forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));
    document.addEventListener("visibilitychange", () => { if (document.hidden && st.mode === "live" && st.screen === "form" && cap.start) cap.leftApp(false); });
    $("#resetSim").addEventListener("click", () => { resetSimulator(); setMode("live", { quiet: true }); st.flow = null; st.form = { vpa: "", amount: "" }; log("Console reset", "info"); renderForm(); renderWhy(); });
    $("#preset").addEventListener("change", e => loadPreset(e.target.value).catch(err => SP.toast(err.message, "error")));
    st.scenarios = (await SP.api("GET", "/api/scenarios")).scenarios;
    renderPresets();
    resetSimulator();
    setMode("live", { quiet: true });
    setTab("explain");
    renderForm();
    log("Signal console ready: live capture on", "info");
    setInterval(tick, 500);
    SP.on("lang", () => { renderPresets(); rerenderScreen(); renderWhy(); });
    SP.on("user", () => {
      if (st.scenario && st.scenario.user_id !== SP.userId) { resetSimulator(); setMode("live", { quiet: true }); }
      st.flow = null; cap.reset(); renderForm(); renderWhy();
    });
  }
  boot();
})();
