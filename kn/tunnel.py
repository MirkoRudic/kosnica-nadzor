"""Cloudflare Tunnel: the page reachable from the phone without opening any port on the workshop PC.

Two modes:
- quick (free, no account/domain): `cloudflared tunnel --url http://127.0.0.1:PORT` gives a random
  https://*.trycloudflare.com address that changes on every start; the app sends it to Telegram.
  Access is guarded by the app itself (secret link, see web.py).
- named (later, with a domain): token from .env CLOUDFLARE_TUNNEL_TOKEN, passed via environment only.
"""
import re
import os
import subprocess
import threading
import time
from pathlib import Path

from . import applog

log = applog.get("tunnel")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class Tunnel:
    def __init__(self, exe: Path, logfile: Path, port: int, on_url=None):
        self.exe, self.logfile, self.port, self.on_url = exe, logfile, port, on_url
        self.proc = None
        self.token = ""
        self.url = ""
        self._stop = False

    def start(self, token: str = ""):
        self.stop()
        self.token, self._stop, self.url = token, False, ""
        if not self.exe.exists():
            log.error("cloudflared missing: %s", self.exe)
            return
        self._spawn()
        threading.Thread(target=self._watch, name="tunnel-watch", daemon=True).start()

    def _spawn(self):
        env = dict(os.environ)
        if self.token:
            env["TUNNEL_TOKEN"] = self.token
            args = [str(self.exe), "tunnel", "--no-autoupdate", "run"]
        else:
            args = [str(self.exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"]
        self.logfile.parent.mkdir(parents=True, exist_ok=True)
        self._log_start = self.logfile.stat().st_size if self.logfile.exists() else 0
        self.proc = subprocess.Popen(args, env=env,
                                     stdout=open(self.logfile, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW)
        log.info("cloudflared started pid=%s", self.proc.pid)

    def _watch(self):
        back = 5
        while not self._stop:
            p = self.proc
            if not self.token and p and p.poll() is None:
                self._find_url()
            if p and p.poll() is not None:
                log.error("cloudflared exited code=%s, restart in %s s", p.returncode, back)
                time.sleep(back)
                back = min(back * 2, 300)
                if not self._stop:
                    self._spawn()
            time.sleep(3)

    def _find_url(self):
        try:
            with open(self.logfile, "rb") as f:
                f.seek(self._log_start)
                m = re.findall(rb"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
        except OSError:
            return
        if m and m[-1].decode() != self.url:
            self.url = m[-1].decode()
            log.info("quick tunnel address %s", self.url)
            if self.on_url:
                try:
                    self.on_url(self.url)
                except Exception:
                    log.exception("on_url failed")

    def connected(self) -> bool:
        if not (self.proc and self.proc.poll() is None):
            return False
        try:
            with open(self.logfile, "rb") as f:
                f.seek(max(0, f.seek(0, 2) - 20000))
                tail = f.read().decode("utf-8", "replace")
            last_ok = tail.rfind("Registered tunnel connection") if self.token else (len(tail) if self.url else -1)
            last_bad = max(tail.rfind("Unauthorized"), tail.rfind("failed to"))
            return last_ok > last_bad
        except OSError:
            return False

    def stop(self):
        self._stop = True
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
            log.info("cloudflared stopped")
