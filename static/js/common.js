/* SafePay Shield — shared frontend helpers (no build step).
   State: current payer + language. Language is the payer's saved preference
   (PUT /api/users/<id>/language) and can be switched at any time. */
(function () {
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: fine */ } },
  };

  const SP = window.SP = {
    lang: "en", userId: null, users: [], strings: {}, en: {},
    listeners: { user: [], lang: [] },

    on(evt, fn) { this.listeners[evt].push(fn); },
    emit(evt) { this.listeners[evt].forEach(fn => fn()); },

    t(key, params) {
      let s = this.strings[key] || this.en[key] || key;
      if (params) for (const [k, v] of Object.entries(params)) s = s.split("{" + k + "}").join(v);
      return s;
    },
    has(key) { return key in this.strings || key in this.en; },

    async api(method, url, body) {
      const opts = { method, headers: {} };
      if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
      const r = await fetch(url, opts);
      let data = null;
      try { data = await r.json(); } catch (e) { /* non-JSON */ }
      if (!r.ok) {
        const err = new Error((data && data.message) || `HTTP ${r.status}`);
        err.status = r.status; err.data = data;
        throw err;
      }
      return data;
    },

    inr(n) {
      if (n === null || n === undefined || isNaN(n)) return "—";
      return "Rs " + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
    },
    pct(x, d = 0) { return (x === null || x === undefined) ? "—" : (100 * x).toFixed(d) + "%"; },
    esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },

    icon(name, cls = "") { return `<svg class="ico ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`; },

    async engineStatus() {
      const ul = document.getElementById("engineList");
      if (!ul) return;
      try {
        const h = await this.api("GET", "/api/health");
        const row = (ok, name, sub) => `<li><i class="led ${ok ? "on" : "off"}"></i><span>${name}</span><small>${sub}</small></li>`;
        ul.innerHTML = row(h.models.coercion.loaded, "Coercion model", h.models.coercion.loaded ? "2 stages" : "offline")
          + row(h.models.mule.loaded, "Mule model", h.models.mule.loaded ? "RF · RBIH" : "offline")
          + row(h.database, "Rules + fusion", h.database ? "R1–R7" : "no DB");
      } catch (e) { ul.innerHTML = `<li><i class="led off"></i><span>Engine unreachable</span></li>`; }
    },

    toast(msg, kind = "") {
      const el = document.getElementById("toast");
      el.textContent = msg; el.className = "toast show " + kind;
      clearTimeout(this._tt); this._tt = setTimeout(() => (el.className = "toast"), 3200);
    },

    applyI18n(root = document) {
      root.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = this.t(el.dataset.i18n); });
      root.querySelectorAll("[data-i18n-ph]").forEach(el => { el.placeholder = this.t(el.dataset.i18nPh); });
      document.documentElement.lang = this.lang;
      document.body.classList.toggle("deva", this.lang !== "en");
      document.querySelectorAll(".lang-toggle button").forEach(b => b.setAttribute("aria-pressed", b.dataset.lang === this.lang));
    },

    async setLang(lang, persist = true) {
      this.strings = await this.api("GET", `/api/i18n/${lang}`);
      this.lang = lang;
      store.set("sp.lang", lang);
      if (persist && this.userId) {
        try { await this.api("PUT", `/api/users/${this.userId}/language`, { language: lang }); } catch (e) { /* not fatal */ }
      }
      this.applyI18n();
      this.emit("lang");
    },

    async setUser(id, { followLanguage = true } = {}) {
      this.userId = id;
      store.set("sp.user", String(id));
      const picker = document.getElementById("userPicker");
      if (picker) picker.value = String(id);
      const u = this.user();
      if (followLanguage && u && u.language !== this.lang) await this.setLang(u.language, false);
      this.emit("user");
    },
    user() { return this.users.find(u => u.id === this.userId); },

    async init() {
      this.en = await this.api("GET", "/api/i18n/en");
      this.users = (await this.api("GET", "/api/users")).users;
      const picker = document.getElementById("userPicker");
      picker.innerHTML = this.users.map(u => `<option value="${u.id}">${this.esc(u.name.split(" ")[0])}, ${u.age}</option>`).join("");
      picker.addEventListener("change", () => this.setUser(Number(picker.value)));
      document.querySelectorAll(".lang-toggle button").forEach(b => b.addEventListener("click", () => this.setLang(b.dataset.lang)));
      document.querySelectorAll("[data-nav]").forEach(a => a.classList.toggle("active", a.dataset.nav === document.body.dataset.page));

      const tt = document.getElementById("themeToggle");
      if (tt) tt.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next; store.set("sp.theme", next);
      });
      this.engineStatus();

      const saved = Number(store.get("sp.user"));
      const first = this.users.some(u => u.id === saved) ? saved : this.users[0].id;
      this.userId = first; picker.value = String(first);
      await this.setLang(store.get("sp.lang") || this.user().language, false);
    },
  };

  // Shared chart tooltip: any element (HTML or SVG) with data-tip="<html>" shows it on hover/focus.
  // data-tip content is built by our own chart code from escaped values.
  (function chartTooltip() {
    let tip = null;
    const show = (el, x, y) => {
      if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; tip.setAttribute("role", "tooltip"); document.body.appendChild(tip); }
      tip.innerHTML = el.getAttribute("data-tip"); tip.hidden = false;
      const r = tip.getBoundingClientRect(), pad = 14;
      let left = x + pad, top = y + pad;
      if (left + r.width > window.innerWidth - 8) left = x - r.width - pad;
      if (top + r.height > window.innerHeight - 8) top = y - r.height - pad;
      tip.style.left = left + "px"; tip.style.top = top + "px";
    };
    const hide = () => { if (tip) tip.hidden = true; };
    document.addEventListener("mousemove", e => {
      const el = e.target.closest && e.target.closest("[data-tip]");
      el ? show(el, e.clientX, e.clientY) : hide();
    });
    document.addEventListener("focusin", e => {
      const el = e.target.closest && e.target.closest("[data-tip]");
      if (el) { const r = el.getBoundingClientRect(); show(el, r.right, r.top); }
    });
    document.addEventListener("focusout", hide);
    window.addEventListener("scroll", hide, true);
  })();

  SP.ready = SP.init().catch(e => { console.error(e); SP.toast("Could not reach the SafePay Shield server: " + e.message, "error"); });
})();
