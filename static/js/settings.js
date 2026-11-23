/* Settings: trusted contacts (with consent), trusted payees, planned payments, language. */
(function () {
  const $ = s => document.querySelector(s);

  async function load() {
    const uid = SP.userId;
    const [c, b, p] = await Promise.all([
      SP.api("GET", `/api/contacts?user_id=${uid}`), SP.api("GET", `/api/trusted-beneficiaries?user_id=${uid}`),
      SP.api("GET", `/api/planned-payments?user_id=${uid}`)]);
    $("#contacts").innerHTML = c.contacts.length ? `<thead><tr><th>Name</th><th>Relation</th><th>Phone</th><th>${SP.esc(SP.t("settings.consent"))}</th><th></th></tr></thead><tbody>` +
      c.contacts.map(x => `<tr><td>${SP.esc(x.name)}</td><td>${SP.esc(x.relation)}</td><td><code>${SP.esc(x.phone_masked)}</code></td>
        <td><label class="switch"><input type="checkbox" data-consent="${x.id}" ${x.consent_given ? "checked" : ""}><span class="small">${x.consent_timestamp ? x.consent_timestamp.slice(0, 10) : "—"}</span></label></td>
        <td><button class="btn btn-sm btn-ghost" data-del="${x.id}">${SP.esc(SP.t("btn.delete"))}</button></td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">No trusted contacts yet.</td></tr></tbody>`;
    $("#benef").innerHTML = b.beneficiaries.length ? `<thead><tr><th>UPI ID</th><th>Label</th><th>Added</th></tr></thead><tbody>` +
      b.beneficiaries.map(x => `<tr><td><code>${SP.esc(x.payee_vpa)}</code></td><td>${SP.esc(x.label || "")}</td><td>${x.added_at.slice(0, 10)}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">No trusted payees yet.</td></tr></tbody>`;
    $("#planned").innerHTML = p.planned_payments.length ? `<thead><tr><th>UPI ID</th><th class="num">Amount</th><th>Day</th><th>Purpose</th></tr></thead><tbody>` +
      p.planned_payments.map(x => `<tr><td><code>${SP.esc(x.payee_vpa)}</code></td><td class="num">${SP.inr(x.amount)}</td><td>${x.day_of_month ?? "any"}</td><td>${SP.esc(x.purpose || "")}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="empty">No planned payments yet.</td></tr></tbody>`;
    document.querySelectorAll("[data-consent]").forEach(i => i.addEventListener("change", async () => {
      await SP.api("POST", `/api/contacts/${i.dataset.consent}/consent`, { consent_given: i.checked }); load();
    }));
    document.querySelectorAll("[data-del]").forEach(btn => btn.addEventListener("click", async () => {
      const r = await SP.api("DELETE", `/api/contacts/${btn.dataset.del}`);
      if (r.consent_withdrawn) SP.toast(r.message);
      load();
    }));
    document.querySelectorAll("#langSeg button").forEach(x => x.setAttribute("aria-pressed", x.dataset.lang === SP.lang));
  }

  function submit(form, fn) {
    form.addEventListener("submit", async e => {
      e.preventDefault();
      try { await fn(new FormData(form)); form.reset(); load(); SP.toast("Saved"); }
      catch (err) { SP.toast(err.message, "error"); }
    });
  }

  (async function boot() {
    await SP.ready;
    submit($("#addContact"), f => SP.api("POST", "/api/contacts", { user_id: SP.userId, name: f.get("name"),
      relation: f.get("relation"), phone: f.get("phone"), consent_given: f.get("consent") === "on" }));
    submit($("#addBenef"), f => SP.api("POST", "/api/trusted-beneficiaries", { user_id: SP.userId,
      payee_vpa: f.get("payee_vpa"), label: f.get("label") || null }));
    submit($("#addPlanned"), f => SP.api("POST", "/api/planned-payments", { user_id: SP.userId, payee_vpa: f.get("payee_vpa"),
      amount: Number(f.get("amount")), day_of_month: f.get("day") ? Number(f.get("day")) : null, purpose: f.get("purpose") || null }));
    document.querySelectorAll("#langSeg button").forEach(b => b.addEventListener("click", () => SP.setLang(b.dataset.lang)));
    SP.on("user", load); SP.on("lang", load);
    load().catch(e => SP.toast(e.message, "error"));
  })();
})();
