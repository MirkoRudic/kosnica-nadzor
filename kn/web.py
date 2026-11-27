"""Local page on 127.0.0.1 only (RULES.md P2)."""
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import re
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import applog, settings, version

log = applog.get("web")
STATIC = Path(__file__).resolve().parent.parent / "static"
TYPES = {".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".png": "image/png"}


DENY = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<body style='background:#121316;color:#e8e9eb;font:16px system-ui;display:grid;place-items:center;height:90vh;"
        "text-align:center'><p>Košnica Nadzor<br><br>Otvori link koji je bot poslao u Telegram grupu<br>"
        "(komanda /strana).</p>")


def _hash(name):
    return hashlib.sha1((STATIC / name).read_bytes()).hexdigest()[:8]


def make_server(core, port, on_shutdown, on_secret, on_remote, app=None):
    """app: hooks from nadzor.pyw - autostart(get/set), uninstall(), git() (V2/V3 update possible)."""
    app = app or {}
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=b"", ctype="application/json; charset=utf-8", cache=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return {}

        def _remote(self):
            return bool(self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"))

        def _allowed(self):
            """Requests through the Cloudflare tunnel need the secret key (link from Telegram) in a cookie."""
            if not self._remote():
                return True
            key = settings.load().get("remote_key") or ""
            ck = SimpleCookie(self.headers.get("Cookie") or "")
            return bool(key) and "kn_k" in ck and hmac.compare_digest(ck["kn_k"].value, key)

        def _deny(self):
            log.warning("remote request without key from %s: %s", self.headers.get("Cf-Connecting-Ip"), self.path)
            return self._send(403, DENY, "text/html; charset=utf-8")

        def do_GET(self):
            try:
                u = urlsplit(self.path)
                path, q = u.path, parse_qs(u.query)
                if path == "/health":
                    return self._send(200, {"app": "kosnica-nadzor", "pid": os.getpid(), "version": version.VERSION})
                if self._remote() and q.get("k"):
                    key = settings.load().get("remote_key") or ""
                    if key and hmac.compare_digest(q["k"][0], key):
                        self.send_response(303)
                        self.send_header("Set-Cookie", f"kn_k={key}; Max-Age=31536000; Path=/; Secure; HttpOnly; SameSite=Lax")
                        self.send_header("Location", "/")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        log.info("remote login from %s", self.headers.get("Cf-Connecting-Ip"))
                        return
                if not self._allowed():
                    return self._deny()
                if path == "/":
                    html = (STATIC / "index.html").read_text(encoding="utf-8")
                    html = re.sub(r"\{\{(\w+\.\w+)\}\}", lambda m: f"{m.group(1)}?v={_hash(m.group(1))}", html)
                    return self._send(200, html.replace("[[VERSION]]", version.label()), "text/html; charset=utf-8")
                if path.startswith("/static/"):
                    f = STATIC / Path(path).name
                    if f.exists():
                        return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"), True)
                    return self._send(404, {"error": "nema"})
                m = re.fullmatch(r"/api/raw/(\w+)", path)
                if m and m.group(1) in core.printers and not self._remote():
                    raw = {k: v for k, v in core.printers[m.group(1)].status.items()
                           if not (isinstance(v, str) and len(v) > 200)}  # diagnostics: what the printer reports
                    return self._send(200, raw)
                if path == "/api/state":
                    return self._send(200, {**core.state(), "version": version.label()})
                m = re.fullmatch(r"/api/(frame|preview|alarm)/(\w+)\.jpg", path)
                if m and m.group(2) in core.cams:
                    kind, pid = m.groups()
                    if kind == "frame":
                        b = core.cams[pid].jpg()
                    elif kind == "preview":
                        lay = q.get("layer", [""])[0]
                        b = core.prev[pid].render(int(lay) if lay.isdigit() else None, core.view(pid), core.zc(pid))
                    else:
                        b = core.track[pid]["alarm_jpg"]
                    return self._send(200 if b else 404, b, "image/jpeg")
                m = re.fullmatch(r"/api/np/([\w\-]+\.jpg)", path)
                if m and (core.ign_dir / m.group(1)).exists():
                    return self._send(200, (core.ign_dir / m.group(1)).read_bytes(), "image/jpeg", True)
                m = re.fullmatch(r"/api/photo/((?:[\w\-]+/)?[\w\-]+\.jpg)", path)
                if m and (core.photos / m.group(1)).exists():
                    return self._send(200, (core.photos / m.group(1)).read_bytes(), "image/jpeg", True)
                if path == "/api/settings":
                    s = settings.load()
                    s.pop("remote_key", None)
                    return self._send(200, {**s, "version": version.label(), "log_mb": applog.size_mb(),
                                            "local": not self._remote(), "windows": os.name == "nt",
                                            "autostart": bool(app.get("autostart") and app["autostart"]()),
                                            "git": bool(app.get("git") and app["git"]())})
                if path == "/api/update":
                    if not (app.get("git") and app["git"]()):
                        return self._send(200, {"ok": False, "error": "Ažuriranje nije podešeno na ovom računaru."})
                    from . import update
                    return self._send(200, update.status(version.VERSION))
                if path == "/api/archive":
                    return self._send(200, {"groups": core.archive()})
                if path == "/api/log":
                    lines = applog.read(days=int(q.get("days", ["2"])[0]), problems_only=q.get("p", ["0"])[0] == "1",
                                        query=(q.get("q", [""])[0] or None))
                    return self._send(200, {"lines": lines[::-1], "mb": applog.size_mb()})
                return self._send(404, {"error": "nema"})
            except Exception:
                log.exception("GET %s", self.path)
                return self._send(500, {"error": "Greška na serveru."})

        def do_POST(self):
            try:
                if not self._allowed():
                    return self._deny()
                path, b = urlsplit(self.path).path, self._body()
                if self._remote() and path in ("/api/shutdown", "/api/secret", "/api/log/delete", "/api/update", "/api/uninstall"):
                    return self._send(403, {"error": "Samo sa računara u radionici."})
                if path == "/api/cmd":
                    return self._send(200, {"msg": core.command(b.get("printer"), b.get("action"), "sa računara")})
                if path == "/api/mute":
                    pid = b.get("printer")
                    if pid in core.det:
                        core.det[pid].mute(30)
                        core.event(pid, "info", "označeno da je sve u redu, slika se ne prijavljuje 30 min", tg=False)
                    return self._send(200, {"ok": True})
                if path == "/api/settings":
                    s = settings.load()
                    for k in ("detect", "sensitivity", "keep_photos", "theme", "remote"):
                        if k in b:
                            s[k] = b[k]
                    for k in ("auto_pause", "notify"):
                        if isinstance(b.get(k), dict):
                            s[k].update(b[k])
                    if "autostart" in b:
                        s["autostart"] = bool(b["autostart"])
                    if b.get("new_link"):
                        s["remote_key"] = ""
                    if b.get("unbind_telegram"):
                        s["telegram"] = {"chat_id": None, "chat_name": ""}
                    settings.save(s)
                    if "autostart" in b and app.get("autostart"):
                        app["autostart"](bool(b["autostart"]))
                    if "remote" in b or b.get("new_link"):
                        on_remote(bool(s.get("remote")), bool(b.get("new_link")))
                    log.info("settings saved: %s", {k: v for k, v in b.items()})
                    return self._send(200, {"ok": True})
                if path == "/api/secret":
                    kind, t = b.get("kind"), (b.get("token") or "").strip()
                    if t.lower().startswith("cloudflared"):  # whole install command pasted
                        t = t.split()[-1]
                    pat = {"telegram": r"\d{6,}:[\w-]{30,}", "tunnel": r"eyJ[\w+/=-]{50,}"}.get(kind)
                    if not pat or not re.fullmatch(pat, t):
                        return self._send(200, {"ok": False, "msg": "To ne liči na token."})
                    on_secret(kind, t)
                    return self._send(200, {"ok": True})
                if path == "/api/view":
                    pid = b.get("printer")
                    if pid not in core.cams:
                        return self._send(404, {"error": "nema"})
                    if b.get("reset"):
                        s = settings.load()
                        s.get("views", {}).pop(pid, None)
                        settings.save(s)
                        return self._send(200, {"ok": True})
                    pts = b.get("pts") or []
                    if len(pts) != 4:
                        return self._send(200, {"ok": False, "msg": "Treba 4 tačke."})
                    err = core.save_view(pid, pts)
                    return self._send(200, {"ok": not err, "msg": err})
                if path == "/api/np/delete":
                    return self._send(200, {"ok": core.forget_place(b.get("printer"), str(b.get("id")))})
                if path == "/api/link/send":
                    core.send_link()
                    return self._send(200, {"ok": bool(core.link())})
                if path == "/api/telegram/test":
                    ok = bool(core.bot) and core.bot.send("Probna poruka iz Košnice Nadzor.")
                    return self._send(200, {"ok": ok})
                if path == "/api/log/delete":
                    n = applog.delete_older_than(int(b.get("days", 30)))
                    log.info("deleted %s log files older than %s days", n, b.get("days"))
                    return self._send(200, {"deleted": n, "mb": applog.size_mb()})
                if path == "/api/shutdown":
                    self._send(200, {"ok": True})
                    threading.Thread(target=on_shutdown, args=(bool(b.get("keep_window")),), daemon=True).start()
                    return
                if path == "/api/update":
                    if not (app.get("git") and app["git"]()):
                        return self._send(200, {"ok": False, "error": "Ažuriranje nije podešeno na ovom računaru."})
                    from . import update
                    r = update.apply(version.VERSION)
                    return self._send(200, {"ok": r.get("ok"), "changed": r.get("changed"),
                                            "error": None if r.get("ok") else "Ažuriranje nije uspelo (detalji u dnevniku)."})
                if path == "/api/uninstall":
                    self._send(200, {"ok": True})
                    if app.get("uninstall"):
                        threading.Thread(target=app["uninstall"], daemon=True).start()
                    return
                return self._send(404, {"error": "nema"})
            except Exception:
                log.exception("POST %s", self.path)
                return self._send(500, {"error": "Greška na serveru."})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    srv.daemon_threads = True
    return srv
