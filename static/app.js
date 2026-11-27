// Košnica Nadzor - page. Polls /api/state, updates only what changed (UI2).
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const main = $("#main");
  const cards = {};
  let theme = "dark";

  const api = async (path, body) => {
    const r = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return r.json();
  };
  const setText = (el, v, blank = false) => { v = v == null || v === "" ? (blank ? "" : "—") : String(v); if (el.textContent !== v) el.textContent = v; };
  const formatTemp = (v, t) => v == null ? "—" : (t ? `${v}° / ${t}°` : `${v}°`);
  const formatPct = (v) => v == null ? "" : `${v}%`;

  function makeCard(p) {
    const el = $("#tplCard").content.firstElementChild.cloneNode(true);
    el.dataset.id = p.id;
    $(".pname", el).textContent = p.name;
    main.insertBefore(el, $(".side"));
    const img = $(".live-img", el), start = $(".prev-img", el);
    const cam = $(".cam", el), ref = $(".ref", el);
    const setBig = (b) => {
      cam.classList.toggle("big", b === "cam"); ref.classList.toggle("big", b === "ref");
      try { localStorage.setItem("kn_big_" + p.id, b); } catch (e) {}
    };
    let saved = "cam";
    try { saved = localStorage.getItem("kn_big_" + p.id) || "cam"; } catch (e) {}
    setBig(saved);
    const liveSrc = () => `/api/frame/${p.id}.jpg?t=${Date.now()}`;
    cam.onclick = () => cam.classList.contains("big") ? showPhoto(liveSrc(), liveSrc) : setBig("cam");
    ref.onclick = () => ref.classList.contains("big") ? (start.src && showPhoto(start.src, () => start.src)) : setBig("ref");
    $(".fs", cam).onclick = (e) => { e.stopPropagation(); showPhoto(liveSrc(), liveSrc, true); };
    $(".fs", ref).onclick = (e) => { e.stopPropagation(); if (start.src) showPhoto(start.src, () => start.src, true); };
    $(".b-pause", el).onclick = async () => {
      const paused = el.dataset.paused === "1";
      if (!paused && !(await vd.confirm(`Pauzirati štampu na ${p.name}?`, { ok: "Pauziraj" }))) return;
      const r = await api("/api/cmd", { printer: p.id, action: paused ? "resume" : "pause" });
      await vd.message(r.msg);
    };
    $(".b-stop", el).onclick = async () => {
      if (!(await vd.confirm(`Zaustaviti štampu na ${p.name}?\nNe može da se nastavi.`, { ok: "Zaustavi", danger: true }))) return;
      const r = await api("/api/cmd", { printer: p.id, action: "stop" });
      await vd.message(r.msg);
    };
    cards[p.id] = { el, img, start, loading: false, startKey: undefined };
    return cards[p.id];
  }

  function refreshImg(c, p) {
    const cam = $(".cam", c.el);
    cam.classList.toggle("empty", !p.camera);
    if (!p.camera || c.loading || document.hidden) return;
    c.loading = true;
    const n = new Image();
    n.onload = () => { c.img.src = n.src; c.loading = false; };
    n.onerror = () => { c.loading = false; };
    n.src = `/api/frame/${p.id}.jpg?t=${Date.now()}`;
  }

  function updateCard(p) {
    const c = cards[p.id] || makeCard(p);
    const el = c.el;
    el.dataset.state = p.state;
    el.dataset.paused = p.paused ? "1" : "0";
    el.classList.toggle("off", !p.online);
    const d = p.detect || {};
    const alarm = p.online && p.state === 1 && d.ratio >= 1;
    el.classList.toggle("alarm", alarm);
    const st = $(".st", el);
    const cls = !p.online ? "bad" : p.state === 1 ? (alarm ? "bad" : "ok") : p.paused || p.state === 3 ? "warn" : "";
    st.className = "pill st " + cls;
    setText(st, p.online ? p.state_text : "nije povezan");
    refreshImg(c, p);
    const printing = p.state === 1 || p.paused;
    const PTEXT = { nema: "—", ucitava: "Učitavam projekat…", nedostupno: "Projekat nije dostupan" };
    const ok = p.preview === "gcode" || p.preview === "slika";
    const key = ok ? `${p.file}|${p.preview === "gcode" ? (printing ? p.layer : "") : ""}|${p.view}|${c.viewV || 0}|${p.skipped}` : null;
    $(".ref", el).classList.toggle("empty", !ok);
    $(".ref", el).classList.toggle("camview", !!p.view && p.preview === "gcode");
    setText($(".ptext", el), PTEXT[p.preview] || "—");
    if (key !== c.startKey && !c.pLoading) {
      c.startKey = key;
      if (key) {
        c.pLoading = true;
        const n = new Image();
        n.onload = () => { c.start.src = n.src; c.pLoading = false; };
        n.onerror = () => { c.pLoading = false; c.startKey = undefined; };
        n.src = `/api/preview/${p.id}.jpg?layer=${printing && p.preview === "gcode" ? p.layer : ""}&f=${encodeURIComponent(p.file)}`;
      } else c.start.removeAttribute("src");
    }
    const box = $(".box", el);
    if (alarm && d.box && c.img.naturalWidth) {
      const iw = c.img.naturalWidth, ih = c.img.naturalHeight, r = c.img.getBoundingClientRect();
      const s = Math.max(r.width / iw, r.height / ih), ox = (r.width - iw * s) / 2, oy = (r.height - ih * s) / 2;
      Object.assign(box.style, { display: "block", left: `${ox + d.box[0] * s}px`, top: `${oy + d.box[1] * s}px`,
        width: `${(d.box[2] - d.box[0]) * s}px`, height: `${(d.box[3] - d.box[1]) * s}px` });
    } else box.style.display = "none";
    setText($(".file", el), printing ? p.file : (p.state === 2 ? `${p.file} — završeno` : "nema štampe"));
    setText($(".pct", el), printing ? formatPct(p.progress) : "", true);
    $(".pbar", el).style.width = `${printing ? p.progress || 0 : p.state === 2 ? 100 : 0}%`;
    setText($(".f-layer", el), p.layers ? `${p.layer}/${p.layers}` : "");
    setText($(".f-noz", el), formatTemp(p.nozzle, p.nozzle_t));
    setText($(".f-bed", el), formatTemp(p.bed, p.bed_t));
    setText($(".f-left", el), printing ? p.left : "");
    const bp = $(".b-pause", el);
    bp.disabled = !p.online || !printing;
    $(".b-stop", el).disabled = !p.online || !printing;
    const lbl = p.paused ? "Nastavi" : "Pauziraj";
    if ($("span", bp).textContent !== lbl) {
      $("span", bp).textContent = lbl;
      $("use", bp).setAttribute("href", p.paused ? "#t-player-play" : "#t-player-pause");
    }
  }

  const ICON = { info: "info-circle", warn: "alert-triangle", bad: "alert-triangle" };
  let lastEv = -1;
  function updateEvents(evs) {
    const top = evs.length ? evs[0].id : 0;
    if (top === lastEv) return;
    lastEv = top;
    const box = $("#events");
    if (!evs.length) { box.innerHTML = '<div class="empty-ev">Još nema događaja.</div>'; return; }
    box.innerHTML = "";
    for (const e of evs) {
      const r = document.createElement("div");
      r.className = "ev " + e.level;
      r.innerHTML = `<time></time><svg class="i"><use href="#t-${ICON[e.level]}"/></svg><span class="who"></span><span class="txt"></span>`;
      $("time", r).textContent = e.time;
      $(".who", r).textContent = e.name;
      $(".txt", r).textContent = e.text;
      if (e.photo) {
        const b = document.createElement("button");
        b.setAttribute("aria-label", "Slika");
        b.innerHTML = '<svg class="i"><use href="#t-photo"/></svg>';
        b.onclick = () => showPhoto(`/api/photo/${e.photo}`);
        r.appendChild(b);
      } else r.appendChild(document.createElement("span"));
      box.appendChild(r);
    }
  }

  function updateLive(s) {
    const live = $("#live"), t = s.telegram;
    const ok = t.token && t.ok && t.chat;
    live.className = "live " + (ok ? "ok" : "bad");
    const lt = $("#liveText");
    setText(lt, !t.token ? "Telegram nije podešen" : !t.ok ? "Telegram nije povezan" : !t.chat ? "Pošalji /start botu u grupi" : `Telegram: ${t.chat}`);
    if (t.link && t.chat) lt.href = t.link; else lt.removeAttribute("href");
  }

  let failed = 0;
  async function tick() {
    try {
      const s = await api("/api/state");
      failed = 0;
      if (s.version && window.KN_VERSION && s.version !== window.KN_VERSION) return location.reload();
      s.printers.forEach(updateCard);
      updateEvents(s.events);
      updateLive(s);
      lastState = s;
    } catch (e) {
      if (++failed === 3) { $("#live").className = "live bad"; setText($("#liveText"), "Aplikacija ne odgovara"); }
    }
  }
  let lastState = null;
  setInterval(tick, 2000);
  tick();

  // ---------- photo viewer ----------
  // viewer: `next` keeps the picture live (camera / layer drawing); full = whole screen
  let viewT = null;
  function showPhoto(src, next, full) {
    const ov = $("#photo"), im = $("#photoImg");
    im.src = src;
    openOv(ov);
    clearInterval(viewT);
    if (next) viewT = setInterval(() => {
      if (ov.hidden) return clearInterval(viewT);
      const n = new Image(), s = next();
      if (!s || s === im.src) return;
      n.onload = () => { im.src = n.src; };
      n.src = s;
    }, 1000);
    if (full && ov.requestFullscreen) ov.requestFullscreen().catch(() => {});
  }
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && !$("#photo").hidden) closeOv($("#photo"));
  });

  // ---------- camera view calibration: 4 bed corners ----------
  const CAL = ["prednji levi", "prednji desni", "zadnji desni", "zadnji levi"];
  let cal = null;
  function openCalib(p) {
    cal = { p, pts: [] };
    const img = $("#calImg");
    img.onload = () => drawCal();
    img.src = `/api/frame/${p.id}.jpg?t=${Date.now()}`;
    drawCal();
    openOv($("#calib"));
  }
  function drawCal() {
    const n = cal.pts.length;
    $("#calText").innerHTML = "";
    const t = $("#calText");
    t.append(`${cal.p.name}: klikni ugao ploče `);
    const b = document.createElement("b");
    b.textContent = n < 4 ? `${n + 1} · ${CAL[n]}` : "gotovo";
    t.append(b);
    if (n < 4) t.append(" (može i van slike)");
    $("#calSave").disabled = n !== 4;
    const img = $("#calImg"), st = $("#calStage"), svg = $("#calSvg");
    const ir = img.getBoundingClientRect(), sr = st.getBoundingClientRect();
    const k = ir.width / (img.naturalWidth || 1);
    const pos = cal.pts.map(([x, y]) => [ir.left - sr.left + x * k, ir.top - sr.top + y * k]);
    let h = "";
    if (pos.length > 1) h += `<polyline points="${pos.map((q) => q.join(",")).join(" ")}${pos.length === 4 ? " " + pos[0].join(",") : ""}" fill="none" stroke="#d9a441" stroke-width="2"/>`;
    pos.forEach((q, i) => { h += `<circle cx="${q[0]}" cy="${q[1]}" r="9" fill="none" stroke="#e5484d" stroke-width="2"/><text x="${q[0] + 12}" y="${q[1] - 10}" fill="#fff" font-size="14" font-weight="700">${i + 1}</text>`; });
    svg.innerHTML = h;
  }
  $("#calStage").addEventListener("click", (e) => {
    if (!cal || cal.pts.length >= 4) return;
    const img = $("#calImg"), ir = img.getBoundingClientRect();
    const k = (img.naturalWidth || 1) / ir.width;
    cal.pts.push([Math.round((e.clientX - ir.left) * k), Math.round((e.clientY - ir.top) * k)]);
    drawCal();
  });
  $("#calReset").onclick = () => { cal.pts = []; drawCal(); };
  $("#calSave").onclick = async () => {
    const r = await api("/api/view", { printer: cal.p.id, pts: cal.pts });
    if (!r.ok) return vd.message(r.msg);
    const c = cards[cal.p.id];
    c.viewV = (c.viewV || 0) + 1;
    closeOv($("#calib"));
  };
  window.addEventListener("resize", () => { if (cal && !$("#calib").hidden) drawCal(); });

  // ---------- overlays ----------
  function openOv(ov) {
    ov.hidden = false;
    document.body.classList.add("vd-lock");
    ov._key = (e) => { if (e.key === "Escape" && !document.querySelector(".vd-overlay")) closeOv(ov); };
    document.addEventListener("keydown", ov._key);
  }
  function closeOv(ov) {
    if (document.fullscreenElement === ov) document.exitFullscreen().catch(() => {});
    ov.hidden = true;
    document.body.classList.remove("vd-lock");
    document.removeEventListener("keydown", ov._key);
  }
  document.querySelectorAll(".ov").forEach((ov) => {
    ov.addEventListener("mousedown", (e) => { if (e.target === ov) closeOv(ov); });
    ov.querySelectorAll("[data-close]").forEach((b) => (b.onclick = () => closeOv(ov)));
  });

  // ---------- settings ----------
  const applyTheme = (t) => { theme = t; document.documentElement.dataset.theme = t; };
  const segSet = (seg, v) => seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === String(v)));

  async function loadSettings() {
    const s = await api("/api/settings");
    applyTheme(s.theme || "dark");
    segSet($('.seg[data-key="theme"]'), s.theme);
    segSet($('.seg[data-key="sensitivity"]'), s.sensitivity);
    $("#sDetect").checked = !!s.detect;
    $("#sAuto").checked = !!(s.auto_pause && s.auto_pause.slika);
    $("#sDone").checked = !!(s.notify && s.notify.done);
    $("#sStart").checked = !!(s.notify && s.notify.start);
    $("#sAutostart").checked = !!s.autostart;
    document.querySelectorAll("#settings .win").forEach((e) => (e.hidden = !s.windows || !s.local));
    const vb = $("#verBox"); vb.textContent = ""; vv.section(vb, s.version);
    setText($("#logMb"), formatMb(s.log_mb));
    const t = lastState ? lastState.telegram : {};
    setText($("#tgBot"), t.name ? "@" + t.name : t.token ? "nije povezan" : "nema tokena");
    setText($("#tgChat"), t.chat || "nije povezana");
    const c = lastState ? lastState.tunnel : {};
    renderPlaces(s);
    $("#sRemote").checked = !!s.remote;
    setText($("#cfState"), !s.remote ? "isključena" : c.ok ? "dostupna" : "povezuje se…");
  }
  // places marked "Nije problem" in Telegram: picture, printer and date, Obriši
  function renderPlaces(s) {
    const box = $("#npList");
    box.textContent = "";
    const names = Object.fromEntries((s.printers || []).map((p) => [p.id, p.name]));
    const all = [];
    for (const [pid, lst] of Object.entries(s.ignore || {})) for (const it of lst) all.push({ pid, ...it });
    setText($("#npCount"), all.length ? String(all.length) : "nema");
    for (const it of all.reverse()) {
      const row = document.createElement("div");
      row.className = "row np";
      const left = document.createElement("div");
      left.className = "npl";
      if (it.img) {
        const im = document.createElement("img");
        im.src = "/api/np/" + it.img;
        im.loading = "lazy";
        im.onclick = () => showPhoto(im.src);
        left.append(im);
      }
      const tx = document.createElement("span");
      tx.textContent = `${names[it.pid] || it.pid} · ${it.t}`;
      left.append(tx);
      const del = document.createElement("button");
      del.textContent = "Obriši";
      del.onclick = async () => {
        await api("/api/np/delete", { printer: it.pid, id: it.id });
        renderPlaces(await api("/api/settings"));
      };
      row.append(left, del);
      box.append(row);
    }
  }
  const formatMb = (v) => v == null ? "—" : `${String(v).replace(".", ",")} MB`;
  const save = (b) => api("/api/settings", b);

  // ---------- arhiva slika: po projektima ----------
  $("#btnArchive").onclick = async () => {
    const body = $("#archiveBody");
    body.textContent = "";
    openOv($("#archive"));
    let d;
    try { d = await (await fetch("/api/archive")).json(); } catch { return; }
    if (!d.groups.length) { const e = document.createElement("div"); e.className = "muted"; e.textContent = "Nema slika."; body.append(e); return; }
    for (const g of d.groups) {
      const det = document.createElement("details");
      det.className = "arch";
      const sum = document.createElement("summary");
      sum.innerHTML = `<span class="pname"></span><span class="muted"></span>`;
      sum.querySelector(".pname").textContent = g.project;
      sum.querySelector(".muted").textContent = `${g.items.length} · ${g.items[0].time}`;
      const grid = document.createElement("div");
      grid.className = "agrid";
      det.append(sum, grid);
      det.addEventListener("toggle", () => {
        if (!det.open || grid.childElementCount) return;
        for (const it of g.items) {
          const b = document.createElement("button");
          b.className = "athumb";
          b.title = [it.time, it.name, it.text].filter(Boolean).join(" · ");
          const im = document.createElement("img");
          im.loading = "lazy";
          im.src = "/api/photo/" + it.photo;
          const cap = document.createElement("span");
          cap.textContent = it.time;
          b.append(im, cap);
          b.onclick = () => showPhoto("/api/photo/" + it.photo);
          grid.append(b);
        }
      });
      body.append(det);
    }
    body.firstElementChild.open = true;
  };

  $("#btnSettings").onclick = async () => { await loadSettings(); openOv($("#settings")); };
  $("#logShow").onclick = () => { openOv($("#logov")); loadLog(); };
  $("#aboutMore").onclick = () => { const b = $("#aboutBody"); b.hidden = !b.hidden; $("#aboutMore").textContent = b.hidden ? "Više" : "Manje"; };
  $("#sAutostart").onchange = async (e) => { await save({ autostart: e.target.checked }); };
  $("#btnUninstall").onclick = async () => {
    if (!(await vd.confirm("Deinstalirati Košnicu Nadzor?\nUkloniće se prečice i start sa Windows-om, a Nadzor će se ugasiti. Folder programa i slike ostaju.",
      { ok: "Deinstaliraj", danger: true }))) return;
    await api("/api/uninstall", {}).catch(() => {});
  };
  vv.init({ storeKey: "kosnica-nadzor" });
  vv.startChecks($("#vvBars"));
  document.querySelectorAll(".seg[data-key]").forEach((seg) => seg.querySelectorAll("button").forEach((b) => (b.onclick = () => {
    segSet(seg, b.dataset.v);
    if (seg.dataset.key === "theme") applyTheme(b.dataset.v);
    save({ [seg.dataset.key]: b.dataset.v });
  })));
  $("#sDetect").onchange = (e) => save({ detect: e.target.checked });
  $("#sAuto").onchange = (e) => save({ auto_pause: { slika: e.target.checked } });
  $("#sDone").onchange = (e) => save({ notify: { done: e.target.checked } });
  $("#sStart").onchange = (e) => save({ notify: { start: e.target.checked } });
  $("#live").onclick = (e) => { if (!e.target.closest("a[href]")) $("#btnSettings").click(); };
  $("#tgSave").onclick = async () => {
    const r = await api("/api/secret", { kind: "telegram", token: $("#tgToken").value });
    if (!r.ok) return vd.message(r.msg);
    $("#tgToken").value = "";
    await vd.message("Token je sačuvan. Sada dodaj bota u grupu radionice i u grupi pošalji /start.");
  };
  $("#sRemote").onchange = async (e) => { await save({ remote: e.target.checked }); setText($("#cfState"), e.target.checked ? "povezuje se…" : "isključena"); };
  $("#cfSend").onclick = async () => {
    const r = await api("/api/link/send", {});
    vd.message(r.ok ? "Link je poslat u Telegram grupu." : "Strana još nije dostupna preko interneta.");
  };
  $("#cfNew").onclick = async () => {
    if (!(await vd.confirm("Napraviti novi link? Stari linkovi prestaju da rade, novi ide u Telegram grupu.", { ok: "Novi link" }))) return;
    await save({ new_link: true });
  };
  $("#tgTest").onclick = async () => {
    const r = await api("/api/telegram/test", {});
    vd.message(r.ok ? "Poruka je poslata." : "Poruka nije poslata. Proveri da je bot u grupi i da je poslato /start.");
  };
  $("#tgUnbind").onclick = async () => {
    if (!(await vd.confirm("Odvezati Telegram grupu? Posle toga se povezuje slanjem /start.", { ok: "Odveži" }))) return;
    await save({ unbind_telegram: true });
    setText($("#tgChat"), "nije povezana");
  };
  $("#btnQuit").onclick = async () => {
    if (!(await vd.confirm("Ugasiti Košnicu Nadzor?", { ok: "Ugasi", danger: true }))) return;
    await api("/api/shutdown", {});
    window.close();
    document.body.innerHTML = '<p style="text-align:center;margin-top:30vh;color:var(--muted)">Košnica Nadzor je ugašena.</p>';
  };

  // log
  let logDays = 1;
  segSet($("#logDays"), logDays);
  $("#logDays").querySelectorAll("button").forEach((b) => (b.onclick = () => { logDays = +b.dataset.v; segSet($("#logDays"), logDays); loadLog(); }));
  let qT;
  $("#logQ").oninput = () => { clearTimeout(qT); qT = setTimeout(loadLog, 300); };
  $("#logP").onchange = loadLog;
  async function loadLog() {
    const q = encodeURIComponent($("#logQ").value.trim());
    const r = await api(`/api/log?days=${logDays}&p=${$("#logP").checked ? 1 : 0}&q=${q}`);
    $("#log").textContent = r.lines.join("\n") || "Nema redova.";
    setText($("#logMb"), formatMb(r.mb));
  }
  $("#logDel").onclick = async () => {
    const v = await vd.prompt("Obriši dnevnik starije od koliko dana?", { value: "30", ok: "Obriši" });
    if (v === null) return;
    const n = parseInt(v, 10);
    if (!(n >= 1)) return vd.message("Upiši broj dana.");
    const r = await api("/api/log/delete", { days: n });
    setText($("#logMb"), formatMb(r.mb));
    await vd.message(`Obrisano fajlova: ${r.deleted}.`);
    loadLog();
  };

  api("/api/settings").then((s) => { applyTheme(s.theme || "dark"); $("#btnQuit").hidden = !s.local; });
})();
