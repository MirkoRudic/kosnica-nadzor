"""Creality printer link over the local websocket (port 9999, no root, no cloud).

The printer pushes JSON objects; the first one is complete, later ones carry only changed keys,
so they are merged into one status dict. Commands are sent only when a person or an enabled
auto-pause rule asks for them.
"""
import base64
import json
import os
import socket
import struct
import threading
import time

from . import applog

log = applog.get("printer")

# state codes: 0, 1, 2 measured 20.11.2026; K1 pause measured 20.11: 1 -> 6 -> 5 -> 8 (8 stays while paused,
# exact meaning not known); 3, 4 from ha_creality_ws, not verified
STATES = {0: "miruje", 1: "štampa", 2: "završeno", 3: "greška", 4: "zaustavljeno", 5: "pauzirano",
          6: "pauzira se", 7: "zaustavlja se", 8: "pauzirano", 9: "počinje"}
# 7 and 9 measured on K1 25.11.2026: stop from the screen goes 1 -> 6 -> 5 -> 7 -> 4; a new job 4/2/0 -> 9 -> 1
PAUSED = {5, 6, 8}

# NOT verified on Mirko's printers yet: confirmed at runtime by watching `state` after sending
COMMANDS = {
    "pause": {"method": "set", "params": {"pause": 1}},
    "resume": {"method": "set", "params": {"pause": 0}},
    "stop": {"method": "set", "params": {"stop": 1}},
}


class WS:
    """Minimal websocket client (text frames, ping/pong, close) - no extra packages."""

    def __init__(self, host, port, timeout=8):
        self.s = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            c = self.s.recv(4096)
            if not c:
                raise ConnectionError("handshake closed")
            head += c
        hdr, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in hdr.split(b"\r\n")[0]:
            raise ConnectionError(hdr.split(b"\r\n")[0].decode("latin1"))
        self.lock = threading.Lock()
        self.s.settimeout(1.0)

    def send(self, txt, op=0x81):
        p = txt.encode() if isinstance(txt, str) else txt
        m = os.urandom(4)
        n = len(p)
        h = bytes([op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        with self.lock:
            self.s.sendall(h + m + bytes(b ^ m[i % 4] for i, b in enumerate(p)))

    def recv(self):
        """Yields text messages; returns on timeout (None) so the caller can do housekeeping."""
        try:
            c = self.s.recv(65536)
            if not c:
                raise ConnectionError("closed by printer")
            self.buf += c
        except socket.timeout:
            pass
        out = []
        while len(self.buf) >= 2:
            op, ln, off = self.buf[0] & 0x0F, self.buf[1] & 0x7F, 2
            if ln == 126:
                if len(self.buf) < 4:
                    break
                ln, off = struct.unpack(">H", self.buf[2:4])[0], 4
            elif ln == 127:
                if len(self.buf) < 10:
                    break
                ln, off = struct.unpack(">Q", self.buf[2:10])[0], 10
            if len(self.buf) < off + ln:
                break
            p, self.buf = self.buf[off:off + ln], self.buf[off + ln:]
            if op == 1:
                out.append(p.decode("utf-8", "replace"))
            elif op == 9:
                self.send(p, 0x8A)
            elif op == 8:
                raise ConnectionError("close frame")
        return out

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


class Printer:
    def __init__(self, pid, name, ip, on_change, port=9999):
        self.id, self.name, self.ip, self.port = pid, name, ip, port
        self.status: dict = {}
        self.online = False
        self.last_msg = 0.0
        self.since_offline = time.time()
        self.on_change = on_change  # callback(printer, old_status, new_status)
        self.ws: WS | None = None
        self._stop = False
        self._warned = 0.0
        threading.Thread(target=self._run, name=f"ws-{pid}", daemon=True).start()

    # -- public --
    def snapshot(self) -> dict:
        s = self.status
        st = s.get("state")
        return {
            "id": self.id, "name": self.name, "ip": self.ip, "online": self.online,
            "state": st, "paused": st in PAUSED, "state_text": STATES.get(st, f"nepoznato ({st})" if st is not None else "—"),
            "file": _file_name(s.get("printFileName", "")),
            "progress": _int(s.get("printProgress")), "layer": _int(s.get("layer")), "layers": _int(s.get("TotalLayer")),
            "left_s": _int(s.get("printLeftTime")), "job_s": _int(s.get("printJobTime")),
            "nozzle": _num(s.get("nozzleTemp")), "nozzle_t": _num(s.get("targetNozzleTemp")),
            "bed": _num(s.get("bedTemp0")), "bed_t": _num(s.get("targetBedTemp0")),
            "err": s.get("err") or {}, "ai": {k: s.get(k) for k in ("aiDetection", "aiPausePrint", "aiSw")},
            "objects": s.get("objects"),
        }

    def printing(self) -> bool:
        return self.online and self.status.get("state") == 1

    def paused(self) -> bool:
        return self.online and self.status.get("state") in PAUSED

    def command(self, action: str) -> bool:
        if action not in COMMANDS or not self.ws:
            return False
        try:
            self.ws.send(json.dumps(COMMANDS[action]))
            log.warning("%s: command sent %s", self.id, action)
            return True
        except OSError:
            log.exception("%s: command %s failed", self.id, action)
            return False

    def stop(self):
        self._stop = True

    def move(self, ip):
        """New address (router gave another one): the loop reconnects there."""
        self.ip = ip
        if self.ws:
            self.ws.close()

    # -- loop --
    def _run(self):
        back = 2
        while not self._stop:
            try:
                self.ws = WS(self.ip, self.port)
                log.info("%s: connected %s", self.id, self.ip)
                back = 2
                for q in ({"method": "get", "params": {"ReqPrinterPara": 1}}, {"method": "get", "params": {"reqPrintObjects": 1}}):
                    self.ws.send(json.dumps(q))
                last_req = time.time()
                self.last_msg = time.time()
                while not self._stop:
                    for m in self.ws.recv():
                        self._handle(m)
                    if time.time() - last_req > 30:  # keep-alive + refresh, read-only
                        self.ws.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
                        last_req = time.time()
                    if time.time() - self.last_msg > 60:
                        raise ConnectionError("no data for 60 s")
            except Exception as e:
                if self.online:
                    log.warning("%s: disconnected: %s", self.id, e)
                    self.online = False
                    self.since_offline = time.time()
                    self._notify({**self.status}, self.status)
                elif time.time() - self._warned > 300:  # visible in the log, at most every 5 min
                    self._warned = time.time()
                    log.warning("%s: no connection to %s:%s: %s", self.id, self.ip, self.port, e)
                else:
                    log.debug("%s: connect failed: %s", self.id, e)
            finally:
                if self.ws:
                    self.ws.close()
                self.ws = None
            time.sleep(back)
            back = min(back * 2, 30)

    def _handle(self, m):
        self.last_msg = time.time()
        if m.strip() == "ok":
            return
        try:
            d = json.loads(m)
        except ValueError:
            log.debug("%s: non-json %r", self.id, m[:80])
            return
        if not isinstance(d, dict):
            return
        if d.get("ModeCode") == "heart_beat":
            self.ws.send("ok")
            return
        old = dict(self.status)
        self.status.update(d)
        was = self.online
        self.online = True
        if not was:
            log.info("%s: online, state=%s", self.id, self.status.get("state"))
        self._notify(old, self.status)

    def _notify(self, old, new):
        try:
            self.on_change(self, old, new)
        except Exception:
            log.exception("%s: on_change failed", self.id)


def identify(ip, port=9999, timeout=1.0):
    """(hostname, model) of a Creality printer at ip, or None. Read-only: one status request."""
    try:
        w = WS(ip, port, timeout=timeout)
    except (OSError, ConnectionError):
        return None
    try:
        w.send(json.dumps({"method": "get", "params": {"ReqPrinterPara": 1}}))
        end = time.time() + 6
        while time.time() < end:
            for m in w.recv():
                try:
                    d = json.loads(m)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("hostname"):
                    return str(d["hostname"]), str(d.get("model", ""))
    except (OSError, ConnectionError):
        pass
    finally:
        w.close()
    return None


def scan(subnets, port=9999, skip=()):
    """{ip: (hostname, model)} for every printer answering on port in the given /24 subnets ("192.168.0")."""
    from concurrent.futures import ThreadPoolExecutor
    ips = [f"{n}.{i}" for n in subnets for i in range(1, 255) if f"{n}.{i}" not in skip]

    def probe(ip):
        try:
            socket.create_connection((ip, port), timeout=0.6).close()
        except OSError:
            return ip, None
        return ip, identify(ip, port)

    with ThreadPoolExecutor(64) as ex:
        return {ip: r for ip, r in ex.map(probe, ips) if r}


def _file_name(p: str) -> str:
    n = (p or "").replace("\\", "/").rsplit("/", 1)[-1]
    n = n.removesuffix(".gcode")
    for ext in (".stl", ".3mf", ".obj", ".step"):
        if ext in n:
            n = n.split(ext)[0]
    return n


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return round(float(v))
    except (TypeError, ValueError):
        return None
