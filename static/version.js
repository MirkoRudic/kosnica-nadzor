// VAJB version.js 1.1 - "Verzija" u Podešavanjima + traka „Nova verzija“ (RULES.md V1, V2, V3). Based on PDFactory.
// Needs base.css + dialog.js. Server: GET /api/update (update.status / selfupdate.check), POST /api/update
// (update.apply / selfupdate.apply), GET /health -> {pid, version}, GET /api/settings -> {version: "<App> X.Y.Z · hash"}.
//
//   vv.init({ storeKey: "mojaapp", beforeUpdate: async () => true, labels: {...} });   // once at start
//   vv.section(container, settings.version)   // row: "<App> X.Y.Z · hash" + under it the state of the last check
//                                             // („Najnovija verzija · provereno 14:40“ / „Nova verzija X“) + one button
//   vv.startChecks(barsElement)               // check at start and every hour -> bar „Nova verzija X“ + Ažuriraj
//
// Every check (start, hourly, by hand) is remembered and shown in the section. Button: „Proveri“, or „Ažuriraj na X“
// when a newer version is known; one click checks and, if newer (or code on disk newer than the running server),
// updates + restarts at once. After the reload a toast says „Ažurirano: X.Y.Z“.
// beforeUpdate: save the workspace / ask about unsaved changes; return false to cancel.
(function () {
  const L = {
    check: "Proveri", updateTo: (v) => `Ažuriraj na ${v}`, latest: "Najnovija verzija", failed: "Provera nije uspela",
    checking: "Provera…", checked: (t) => `provereno ${t}`,
    updating: (v) => `Ažuriranje na ${v}…`, newVersion: (v) => `Nova verzija ${v}`, update: "Ažuriraj",
    updated: (v) => `Ažurirano: ${v}`, theNew: "novu verziju",
  };
  let opt = { storeKey: "app", beforeUpdate: async () => true };
  let pid = null;
  let last = null;                        // {r, at} of the last check
  const isNew = (r) => r && r.ok && (r.behind || r.restart);
  const hhmm = (d) => d.toLocaleTimeString("sr-RS", { hour: "2-digit", minute: "2-digit" });
  async function check() {
    const r = await getJSON("/api/update").catch(() => ({ ok: false }));
    last = { r, at: new Date() };
    return r;
  }
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const getJSON = (u) => fetch(u).then((r) => r.json());

  function toast(text) {
    const t = document.createElement("div"); t.className = "vv-toast"; t.textContent = text;
    document.body.appendChild(t); setTimeout(() => t.remove(), 4000);
  }

  async function init(o = {}) {
    opt = { ...opt, ...o }; Object.assign(L, o.labels || {});
    try { pid = (await getJSON("/health")).pid; } catch (e) { /* server not up yet */ }
    const key = opt.storeKey + ".upd";
    try {
      if (localStorage.getItem(key) !== null) {
        localStorage.removeItem(key);
        const h = await getJSON("/health"); toast(L.updated(h.version));
      }
    } catch (e) { /* no storage */ }
  }

  async function waitRestart() {
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { const h = await getJSON("/health"); if (h.pid !== pid) { location.reload(); return; } } catch (e) { /* restarting */ }
    }
  }

  async function doUpdate(r) {
    if (!(await opt.beforeUpdate())) return false;
    try { localStorage.setItem(opt.storeKey + ".upd", r.version || ""); } catch (e) { /* no storage */ }
    const p = await fetch("/api/update", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then((x) => x.json()).catch(() => null);
    if (p && p.ok) { setTimeout(waitRestart, 1500); return true; }
    try { localStorage.removeItem(opt.storeKey + ".upd"); } catch (e) { /* no storage */ }
    if (p && p.error && window.vd) vd.message(p.error);
    return false;
  }

  function section(container, label) {
    const row = document.createElement("div"); row.className = "vv-row";
    row.innerHTML = `<div class="vv-info"><span>${esc(label)}</span><span class="vv-st"></span></div><button type="button"></button>`;
    const st = row.querySelector(".vv-st"), b = row.querySelector("button");
    const paint = () => {
      if (!last) { st.innerHTML = `<span class="vv-muted">${esc(L.checking)}</span>`; b.textContent = L.check; return; }
      const r = last.r, n = isNew(r);
      st.innerHTML = !r.ok ? `<span class="vv-muted">${esc(L.failed)} · ${hhmm(last.at)}</span>`
        : n ? `<span class="vv-new">${esc(L.newVersion(r.version || ""))}</span>`
        : `<span class="vv-ok">${esc(L.latest)}</span><span class="vv-muted">· ${esc(L.checked(hhmm(last.at)))}</span>`;
      b.textContent = n ? L.updateTo(r.version || "") : L.check;
      b.classList.toggle("primary", !!n);
    };
    b.onclick = async () => {
      let r = last && last.r;
      if (!isNew(r)) { st.innerHTML = `<span class="vv-muted">${esc(L.checking)}</span>`; r = await check(); paint(); }
      if (isNew(r)) {
        b.disabled = true; st.innerHTML = `<span class="vv-ok">${esc(L.updating(r.version || L.theNew))}</span>`;
        if (!(await doUpdate(r))) { b.disabled = false; paint(); }
      }
    };
    container.appendChild(row);
    paint();
    if (!last || Date.now() - last.at > 3600 * 1000) check().then(() => row.isConnected && paint());
    return row;
  }

  function startChecks(bars) {
    const run = async () => {
      const r = await check();
      if (!isNew(r) || document.getElementById("vv-bar")) return;
      const el = document.createElement("div"); el.id = "vv-bar"; el.className = "vv-bar";
      el.innerHTML = `<span>${esc(L.newVersion(r.version || ""))}</span><button type="button" class="primary">${esc(L.update)}</button>`;
      el.querySelector("button").onclick = async () => {
        el.querySelector("span").textContent = L.updating(r.version || L.theNew);
        el.querySelector("button").remove();
        if (!(await doUpdate(r))) el.remove();
      };
      bars.appendChild(el);
    };
    run(); setInterval(run, 3600 * 1000);
  }

  window.vv = { init, section, startChecks, update: doUpdate };
})();
