"""Košnica Nadzor - pokretanje (dupli klik na prečicu). `--tiho` = bez prozora (autostart sa Windows-om).

Server na 127.0.0.1:8800, prozor u Edge --app, ikonica pored sata (Otvori / Ugasi).
Test bez štampača i kamera: KN_TEST_VIDEO=<folder sa k1.mp4, k2.mp4> (kamera iz fajla).
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA = APP_DIR / "data"
PORT = int(os.environ.get("KN_PORT", "8800"))
URL = f"http://127.0.0.1:{PORT}/"
TITLE = "Košnica Nadzor"
sys.path.insert(0, str(APP_DIR))

from kn import applog, settings, tempdir, version  # noqa: E402

applog.setup(DATA / "logs", "kosnica-nadzor")
log = applog.get("main")
applog.auto_trim(500)


def health():
    try:
        with urllib.request.urlopen(URL + "health", timeout=1) as r:
            return b"kosnica-nadzor" in r.read()
    except Exception:
        return False


def focus_window():
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        found = []

        def cb(h, _):
            n = u.GetWindowTextLengthW(h)
            if n and u.IsWindowVisible(h):
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.startswith(TITLE):
                    found.append(h)
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        if found:
            if u.IsIconic(found[0]):
                u.ShowWindow(found[0], 9)
            u.SetForegroundWindow(found[0])
            return True
    except Exception as e:
        log.warning("focus window: %s", e)
    return False


def close_window():
    """P4: shutting the app down also closes its window (WM_CLOSE to the Edge --app window)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        found = []

        def cb(h, _):
            n = u.GetWindowTextLengthW(h)
            if n and u.IsWindowVisible(h):
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                c = ctypes.create_unicode_buffer(64)
                u.GetClassNameW(h, c, 64)
                if b.value.startswith(TITLE) and c.value.startswith("Chrome_WidgetWin"):
                    found.append(h)
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        for h in found:
            u.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        log.info("closed %d app window(s)", len(found))
    except Exception as e:
        log.warning("close window: %s", e)


def open_window():
    if focus_window():
        return
    for exe in [shutil.which("msedge"), r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"]:
        if exe and Path(exe).exists():
            subprocess.Popen([exe, f"--app={URL}", f"--user-data-dir={DATA / 'edge'}", "--no-first-run",
                              "--no-default-browser-check", "--disable-sync", "--window-size=1280,860"])
            return
    webbrowser.open(URL)


def refresh_shortcuts():
    """P1: prečice (Desktop, Startup) prate ikonicu app; ponovo se prave kad se ikonica promeni."""
    if os.name != "nt":
        return
    try:
        import hashlib
        mark = DATA / "precice.txt"
        h = hashlib.md5((APP_DIR / "static" / "kosnica.ico").read_bytes()).hexdigest()
        if mark.exists() and mark.read_text().strip() == h:
            return
        args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(APP_DIR / "precice.ps1")]
        if not settings.load().get("autostart", True):
            args.append("-NoStartup")
        r = subprocess.run(args, capture_output=True, text=True, timeout=60,
                           creationflags=0x08000000)  # CREATE_NO_WINDOW
        log.info("shortcuts refreshed (rc=%s) %s", r.returncode, (r.stdout + r.stderr).strip()[:300])
        if r.returncode == 0:
            mark.write_text(h)
    except Exception as e:
        log.warning("shortcuts: %s", e)


def _startup_lnk():
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Košnica Nadzor.lnk"


def _desktop_lnk():
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Košnica Nadzor.lnk"


def autostart(on=None):
    """Start sa Windows-om: prečica u Startup folderu (PRAVILA UI12)."""
    if on is None:
        return _startup_lnk().exists()
    if on:
        (DATA / "precice.txt").unlink(missing_ok=True)  # refresh_shortcuts makes both again
        refresh_shortcuts()
    else:
        _startup_lnk().unlink(missing_ok=True)
    log.info("start with Windows: %s", on)
    return on


def git_ready():
    """V2/V3: update from inside the app needs git on this computer and this folder as a git clone."""
    from kn import update
    return (APP_DIR / ".git").exists() and bool(update.git_exe())


def read_env():
    env = {}
    f = APP_DIR / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


def stop_running():
    """--restart (V2 update): ask the running server to stop (its window stays and reloads itself), wait for it."""
    import json as _json
    try:
        req = urllib.request.Request(URL + "api/shutdown", data=_json.dumps({"keep_window": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception as e:
        log.info("restart: stop request: %s", e)
    for _ in range(60):
        if not health():
            return
        time.sleep(0.5)
    log.warning("restart: old server still answers")


def main():
    restart = "--restart" in sys.argv
    quiet = "--tiho" in sys.argv or restart
    if restart:
        log.info("restart requested")
        stop_running()
    elif health():
        if not quiet:
            open_window()
        return
    from kn.runlock import RunLock
    lock = RunLock(DATA / "RUN.lock", max_age_h=24 * 365)
    for _ in range(40 if restart else 1):
        if lock.acquire():
            break
        time.sleep(0.5)
    else:
        log.info("another instance is starting, exit")
        return
    from kn import update
    update.init(APP_DIR, version_file="kn/version.py", launcher="nadzor.pyw")
    tempdir.init(DATA / "tmp")
    from kn.core import DEFAULTS, Core
    settings.init(DATA / "settings.json", DEFAULTS)
    settings.save(settings.load())
    log.info("%s starting (quiet=%s)", version.label(), quiet)
    refresh_shortcuts()

    test = os.environ.get("KN_TEST_VIDEO")
    g2 = None
    if test:
        src = lambda pid: str(Path(test) / f"{pid}.mp4")  # noqa: E731
    else:
        from kn.camera import Go2rtc
        g2 = Go2rtc(APP_DIR / "bin" / "go2rtc.exe", DATA / "go2rtc.yaml", DATA / "logs" / "go2rtc.txt",
                    settings.load()["printers"])
        g2.start()
        src = None
    core = Core(DATA, src)
    if g2:
        core.on_move = g2.restart

    from kn.telegram import Bot

    def set_chat(cid, name):
        s = settings.load()
        name = name or s["telegram"].get("chat_name") or ""  # group moved to supergroup: keep its name
        s["telegram"] = {"chat_id": cid, "chat_name": name}
        settings.save(s)

    def start_bot(token):
        if core.bot:
            core.bot.stop()
        core.bot = Bot(token, lambda: settings.load()["telegram"].get("chat_id"),
                       set_chat, core.on_command, core.on_button)

    from kn.tunnel import Tunnel
    def on_url(url):
        core.send_link()

    core.tunnel = Tunnel(APP_DIR / "bin" / "cloudflared.exe", DATA / "logs" / "cloudflared.txt", PORT, on_url)

    def on_secret(kind, token):
        key = {"telegram": "TELEGRAM_TOKEN", "tunnel": "CLOUDFLARE_TUNNEL_TOKEN"}[kind]
        env = read_env()
        env[key] = token
        (APP_DIR / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        log.info("%s saved to .env", key)
        if kind == "telegram":
            start_bot(token)
        elif not test:
            core.tunnel.start(token)

    start_bot(read_env().get("TELEGRAM_TOKEN", ""))
    if not test and settings.load().get("remote", True):
        core.tunnel.start(read_env().get("CLOUDFLARE_TUNNEL_TOKEN", ""))

    from kn.web import make_server
    tray = {"icon": None}
    done = threading.Event()

    def shutdown(keep_window=False):
        log.info("shutdown requested (keep window: %s)", keep_window)
        time.sleep(0.5)  # let the page get its answer first
        if not keep_window:
            close_window()
        done.set()
        if tray["icon"]:
            tray["icon"].stop()

    def on_remote(on, new_link):
        if test:
            return
        if not on:
            core.tunnel.stop()
        elif new_link and core.tunnel.proc:
            core.send_link()
        elif not core.tunnel.proc:
            core.tunnel.start(read_env().get("CLOUDFLARE_TUNNEL_TOKEN", ""))

    def uninstall():
        """P9, razvojna kopija: skida prečice i start sa Windows-om; folder ostaje."""
        for f in (_desktop_lnk(), _startup_lnk()):
            try:
                f.unlink(missing_ok=True)
            except OSError as e:
                log.warning("uninstall %s: %s", f, e)
        s = settings.load()
        s["autostart"] = False
        settings.save(s)
        log.info("uninstalled (shortcuts removed)")
        shutdown()

    srv = make_server(core, PORT, shutdown, on_secret, on_remote,
                      {"autostart": autostart, "uninstall": uninstall, "git": git_ready})
    threading.Thread(target=srv.serve_forever, name="web", daemon=True).start()
    log.info("server %s", URL)
    if not quiet:
        open_window()
    try:
        if test:
            raise ImportError("test mode")
        import pystray
        from PIL import Image
        img = Image.open(APP_DIR / "static" / "kosnica.ico")
        icon = pystray.Icon("kosnica-nadzor", img, TITLE, pystray.Menu(
            pystray.MenuItem("Otvori", lambda: open_window(), default=True),
            pystray.MenuItem("Ugasi", lambda: shutdown())))
        tray["icon"] = icon
        icon.run()
    except ImportError:
        log.warning("pystray not installed, no tray icon")
    done.wait()
    srv.shutdown()
    if g2:
        g2.stop()
    core.tunnel.stop()
    lock.release()
    log.info("stopped")
    os._exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("start failed")
        raise
