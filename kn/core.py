"""Košnica Nadzor core: printers + cameras + checks + alerts + commands."""
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from . import applog, settings
from .camera import Camera
from .project3mf import job_stem
from .detect import Detector, mark
from .fmt import format_duration, format_time
from .preview import Preview
from .printer import PAUSED, STATES, Printer

log = applog.get("core")

DEFAULTS = {
    "printers": [
        {"id": "k1", "name": "K1 Max", "ip": "192.168.0.106"},
        {"id": "k2", "name": "K2 Plus", "ip": "192.168.0.105"},
    ],
    "detect": True,
    "sensitivity": "srednja",
    "auto_pause": {"slika": False},
    "notify": {"done": True, "start": False, "cleanup": True},
    "telegram": {"chat_id": None, "chat_name": ""},
    "keep_photos": 3000,
    "autostart": True,
    "views": {},
    "projects_dir": r"C:\Users\zzroz\Desktop\3D",
    "remote": True,
    "remote_key": "",
}
BED = {"k1": [300, 300], "k2": [350, 350]}  # K1 Max, K2 Plus print area (mm)
WAIT_S = {"pause": 60, "resume": 300, "stop": 60}  # K1 resume measured 27 s (reheat can take longer)
EXPECT = {"pause": PAUSED, "resume": {1}, "stop": {0, 2, 3, 4}}
ACTION_TEXT = {"pause": "pauza", "resume": "nastavak", "stop": "zaustavljanje"}


def _folder(name: str) -> str:
    """Safe folder name for a project."""
    import re
    n = re.sub(r"[^\w\-]+", "_", name, flags=re.UNICODE).strip("_")
    return (n or "bez_projekta")[:80]


def _small(jpg: bytes, width=960, quality=70) -> bytes:
    """Archive copy of a picture: at most 960 px wide (about 60 kB)."""
    try:
        import cv2
        import numpy as np
        im = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if im is None:
            return jpg
        h, w = im.shape[:2]
        if w > width:
            im = cv2.resize(im, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
        ok, b = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return b.tobytes() if ok else jpg
    except Exception:
        return jpg


def _local_net():
    """First three parts of this computer's LAN address ("192.168.0"), no packet is sent."""
    import socket
    try:
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.connect(("192.168.0.1", 9))
        ip = u.getsockname()[0]
        u.close()
        return ip.rsplit(".", 1)[0] if not ip.startswith("127.") else ""
    except OSError:
        return ""


def _keep_awake(on: bool):
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | (0x00000001 if on else 0))
        log.info("keep computer awake: %s", on)
    except Exception:
        log.exception("keep awake failed")


class Core:
    def __init__(self, data: Path, camera_source=None):
        self.data = data
        self.photos = data / "slike"
        self.photos.mkdir(parents=True, exist_ok=True)
        self.events = deque(maxlen=300)
        self.ev_id = 0
        self._load_events()
        self.bot = None
        self.tunnel = None
        self.lock = threading.Lock()
        s = settings.load()
        self.printers: dict[str, Printer] = {}
        self.cams: dict[str, Camera] = {}
        self.det: dict[str, Detector] = {}
        self.track: dict[str, dict] = {}
        self.prev: dict[str, Preview] = {}
        self.t0 = time.time()
        self.on_move = None  # callback(settings printers) when a printer got a new address
        self._scan_t = 0.0
        known = {"k1": "K1Max-0781", "k2": "Monty - K2Plus"}  # measured 21.11.2026
        for p in s["printers"]:
            p.setdefault("host", known.get(p["id"], ""))
        settings.save(s)
        for p in s["printers"]:
            pid = p["id"]
            self.det[pid] = Detector(pid)
            self.prev[pid] = Preview(pid, p["ip"], s.get("projects_dir"))
            self.track[pid] = {"print_id": None, "move_t": time.time(), "move_key": None, "temp_bad_t": None,
                               "warned": set(), "start_jpg": b"", "alarm_jpg": b"", "paused_by_app": False}
            src = camera_source(pid) if camera_source else f"rtsp://127.0.0.1:8554/{pid}"
            self.cams[pid] = Camera(pid, src, loop_file=bool(camera_source))
            self.printers[pid] = Printer(pid, p["name"], p["ip"], self._on_change, p.get("port", 9999))
        threading.Thread(target=self._loop, name="checks", daemon=True).start()

    # ---------- events ----------
    def event(self, pid, level, text, photo: bytes = b"", buttons=None, tg=True):
        """Događaji: kept in data/dogadjaji.jsonl (survive a restart), the picture small in data/slike.
        Telegram gets the picture too; "Obriši poruke" removes it there, the app copy stays."""
        name = self.printers[pid].name if pid in self.printers else ""
        with self.lock:
            self.ev_id += 1
            e = {"id": self.ev_id, "t": time.time(), "time": format_time(time.time()), "printer": pid,
                 "name": name, "level": level, "text": text, "photo": ""}
            pr = self.printers.get(pid)
            job = pr.status.get("printFileName") if pr else ""
            e["job"] = job_stem(job) if job else ""
            if photo:
                folder = _folder(e["job"] or f"{pid}_bez_projekta")
                (self.photos / folder).mkdir(parents=True, exist_ok=True)
                fn = f"{folder}/{datetime.now():%Y-%m-%d_%H%M%S}_{pid}_{self.ev_id}.jpg"
                (self.photos / fn).write_bytes(_small(photo))
                e["photo"] = fn
                self._trim_photos()
            self.events.append(e)
            self._save_event(e)
        (log.warning if level != "info" else log.info)("event %s [%s] %s", pid, level, text)
        if tg and self.bot:
            p = self.printers.get(pid)
            head = f"<b>{name}</b>: {text}" if name else text
            if p and p.status.get("printFileName"):
                head += "\n" + self._job_line(p)
            threading.Thread(target=self._send_job_msg, args=(pid, head, photo or None, buttons), daemon=True).start()
        return e

    def _events_file(self):
        return self.data / "dogadjaji.jsonl"

    def _load_events(self):
        import json
        try:
            lines = self._events_file().read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for ln in lines[-self.events.maxlen:]:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if e.get("photo") and not (self.photos / e["photo"]).exists():
                e["photo"] = ""
            self.events.append(e)
            self.ev_id = max(self.ev_id, int(e.get("id", 0)))
        if len(lines) > 3000:
            self._events_file().write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")

    def _save_event(self, e):
        import json
        try:
            with open(self._events_file(), "a", encoding="utf-8") as f:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError as ex:
            log.warning("event not saved: %s", ex)

    # ---------- Telegram messages of a job (deleted when it ends, if set) ----------
    def _msgs_file(self):
        return self.data / "tg_poruke.json"

    def _msgs(self):
        import json
        try:
            return json.loads(self._msgs_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember_msg(self, pid, mid):
        import json
        tr = self.track.get(pid, {})
        job = tr.get("print_id")
        if not (mid and job) or tr.get("print_id_done") == job:  # final message of an ended job stays
            return
        with self.lock:
            d = self._msgs()
            d.setdefault(f"{pid}:{job}", []).append(mid)
            self._msgs_file().write_text(json.dumps(d), encoding="utf-8")

    def _send_job_msg(self, pid, text, photo=None, buttons=None):
        mid = self.bot.send(text, photo, buttons)
        if pid in self.printers:
            self._remember_msg(pid, mid)
        return mid

    def clean_button(self, pid):
        """On the job's last message: deletes the job's other messages from the group."""
        job = self.track[pid].get("print_id")
        if not job or not self._msgs().get(f"{pid}:{job}"):
            return None
        return [[{"text": "Obriši poruke", "callback_data": f"clean:{pid}:{job}"}]]

    def _cleanup_job(self, pid, job) -> int:
        """Deletes the job's messages and pictures; the message with the button stays."""
        import json
        if not (self.bot and job):
            return 0
        with self.lock:
            d = self._msgs()
            ids = d.pop(f"{pid}:{job}", [])
            self._msgs_file().write_text(json.dumps(d), encoding="utf-8")
        n = self.bot.delete(ids) if ids else 0
        log.info("%s: job %s, deleted %d/%d telegram messages", pid, job, n, len(ids))
        return n

    def _trim_photos(self):
        keep = max(settings.load().get("keep_photos", 3000), 3000)  # ~50 kB each: ~150 MB
        files = sorted(self.photos.rglob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in files[:-keep]:
            try:
                f.unlink()
                if f.parent != self.photos and not any(f.parent.iterdir()):
                    f.parent.rmdir()
            except OSError:
                pass

    def archive(self):
        """Arhiva: every saved picture, by project (one folder each), newest first."""
        texts = {e["photo"]: e for e in self.events if e.get("photo")}
        try:
            import json
            for ln in self._events_file().read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                if e.get("photo"):
                    texts.setdefault(e["photo"], e)
        except OSError:
            pass
        groups = {}
        for f in self.photos.rglob("*.jpg"):
            rel = f.relative_to(self.photos).as_posix()
            name = f.parent.name if f.parent != self.photos else "starije"
            e = texts.get(rel, {})
            t = f.stat().st_mtime
            groups.setdefault(name, []).append({"photo": rel, "t": t, "time": time.strftime("%d.%m. %H:%M", time.localtime(t)),
                                                "name": e.get("name", ""), "text": e.get("text", "")})
        out = [{"project": k, "items": sorted(v, key=lambda x: -x["t"])} for k, v in groups.items()]
        return sorted(out, key=lambda g: -g["items"][0]["t"])

    def _job_line(self, p: Printer):
        s = p.snapshot()
        parts = [s["file"] or "—"]
        if s["layers"]:
            parts.append(f"sloj {s['layer']}/{s['layers']}")
        if s["progress"] is not None:
            parts.append(f"{s['progress']}%")
        if s["left_s"]:
            parts.append("još " + format_duration(s["left_s"]))
        return " · ".join(parts)

    def photo(self, pid, box=None) -> bytes:
        f, t = self.cams[pid].latest()
        if f is None or time.time() - t > 30:
            return b""
        f = mark(f, box) if box else f
        ok, b = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return b.tobytes() if ok else b""

    @staticmethod
    def buttons(pid, paused=False):
        first = {"text": "Nastavi", "callback_data": f"resume:{pid}"} if paused else \
            {"text": "Pauziraj", "callback_data": f"pause:{pid}"}
        return [[first, {"text": "Zaustavi", "callback_data": f"stop:{pid}"}]]

    # ---------- printer status changes ----------
    def _on_change(self, p: Printer, old: dict, new: dict):
        pid, tr = p.id, self.track[p.id]
        if not p.online:
            return
        if (new.get("state") == 1 or new.get("state") in PAUSED) and new.get("printFileName"):
            try:  # the drawing must never stop the checks below
                self.prev[pid].set_job(new["printFileName"], new.get("TotalLayer"), new.get("objects"))
                if new.get("objects") and new.get("objects") != old.get("objects"):
                    self.prev[pid].set_objects(new["objects"])
                    tr["roi_key"] = None
            except Exception:
                log.exception("%s: preview set_job failed", pid)
        pr = new.get("printId")
        if pr and pr != tr["print_id"] and new.get("state") == 1:
            first = tr["print_id"] is None
            tr.update(print_id=pr, warned=set(), temp_bad_t=None, move_t=time.time(), paused_by_app=False,
                      start_jpg=b"", alarm_jpg=b"")
            self.det[pid].reset("new job")
            if not first and settings.load()["notify"].get("start"):
                self.event(pid, "info", "počela štampa")
            elif not first:
                self.event(pid, "info", "počela štampa", tg=False)
        if tr["print_id"] is None and pr:
            tr["print_id"] = pr
        so, sn = old.get("state"), new.get("state")
        if so is not None and sn != so:
            log.info("%s: state %s (%s) -> %s (%s)", pid, so, STATES.get(so, "?"), sn, STATES.get(sn, "?"))
            active_o = so == 1 or so in PAUSED
            if sn in (2, 3, 4) and active_o:
                tr["print_id_done"] = tr.get("print_id")
            if sn == 2 and active_o:
                txt = "štampa završena"
                if new.get("printJobTime"):
                    txt += f" za {format_duration(int(float(new['printJobTime'])))}"
                self.event(pid, "info", txt, self.photo(pid), self.clean_button(pid),
                           tg=settings.load()["notify"].get("done", True))
            elif sn in PAUSED and so not in PAUSED:
                if tr["paused_by_app"]:  # asked for from the page/Telegram: the button already shows Nastavi
                    self.event(pid, "info", f"štampa je pauzirana ({tr.get('pause_who')})", tg=False)
                else:
                    self.event(pid, "warn", "štampa je pauzirana (sa štampača ili njegova AI detekcija)",
                               self.photo(pid), self.buttons(pid, True))
            elif sn == 1 and so in PAUSED:
                self.event(pid, "info", "štampa nastavljena", tg=False)
                tr["paused_by_app"] = False
                self.det[pid].reset("resumed")
            elif sn in (3, 4) and active_o:
                self.event(pid, "bad", "štampa prekinuta" if sn == 4 else "štampa prekinuta greškom",
                           self.photo(pid), self.clean_button(pid))
            elif sn in PAUSED or so in PAUSED:
                pass
            if sn not in STATES:
                log.warning("%s: unknown state %s (was %s)", pid, sn, so)
        if new.get("hostname") and not old.get("hostname"):
            s = settings.load()
            c = next((c for c in s["printers"] if c["id"] == pid), None)
            if c is not None and not c.get("host"):
                c["host"] = str(new["hostname"])
                settings.save(s)
        eo, en = (old.get("err") or {}), (new.get("err") or {})
        if en.get("errcode") and en.get("errcode") != eo.get("errcode"):
            self.event(pid, "bad", f"štampač javlja grešku {en.get('errcode')}"
                       + (f" ({en.get('value')})" if en.get("value") else ""), self.photo(pid), self.buttons(pid))

    # ---------- periodic checks ----------
    def _loop(self):
        awake = None
        while True:
            try:
                s = settings.load()
                for pid, p in self.printers.items():
                    self._check(pid, p, s)
                self._maybe_scan(s)
                self._maybe_skips()
                busy = any(p.printing() or p.paused() for p in self.printers.values())
                if busy != awake:  # computer does not go to sleep while a printer works
                    awake = busy
                    _keep_awake(busy)
            except Exception:
                log.exception("checks failed")
            time.sleep(1)

    # ---------- skipped objects (Moonraker exclude_object) ----------
    def _maybe_skips(self):
        if time.time() - getattr(self, "_skip_t", 0) < 30:
            return
        self._skip_t = time.time()
        for pid, p in self.printers.items():
            if (p.printing() or p.paused()) and not self.track[pid].get("skip_busy"):
                self.track[pid]["skip_busy"] = True
                threading.Thread(target=self._skips, args=(pid,), name=f"skips-{pid}", daemon=True).start()

    def _skips(self, pid):
        from .preview import excluded
        tr, p = self.track[pid], self.printers[pid]
        try:
            names, raw = excluded(p.ip)
            if tr.get("skip_logged") != tr.get("print_id"):  # once per job: what the printer answers
                tr["skip_logged"] = tr.get("print_id")
                log.info("%s: exclude_object %s", pid, f"{len(raw.get('objects') or [])} objects, excluded {sorted(names)}, "
                         f"current {raw.get('current_object')}" if names is not None else f"not available ({raw})")
            if names is None:
                return
            total = len(getattr(self.prev[pid], "polys", {}) or {}) or len((raw or {}).get("objects") or [])
            old = tr.get("skipped") or set()
            tr["skipped"], tr["objects_n"] = names, total
            self.prev[pid].set_skipped(names)
            if tr.get("skip_job") != tr.get("print_id"):
                old = set()  # new job (or Nadzor just started): report what is already skipped
            if names - old:
                self.event(pid, "info", f"preskočeno {len(names)} od {total} predmeta", tg=False)
            tr["skip_job"] = tr.get("print_id")
        finally:
            tr["skip_busy"] = False

    # ---------- printer got another address (router, after power loss) ----------
    def _maybe_scan(self, s):
        lost = [p for p in self.printers.values()
                if not p.online and time.time() - p.since_offline > 60]
        if not lost or time.time() - self._scan_t < 300 or any(c.get("port") for c in s["printers"]):
            return  # test setup (own port) is never scanned
        self._scan_t = time.time()
        threading.Thread(target=self._scan, args=([p.id for p in lost],), name="scan", daemon=True).start()

    def _scan(self, lost):
        from .printer import scan
        s = settings.load()
        nets = {p["ip"].rsplit(".", 1)[0] for p in s["printers"]} | {_local_net()} - {""}
        skip = {p.ip for p in self.printers.values() if p.online}
        log.info("scan %s for %s", sorted(nets), lost)
        found = scan(sorted(nets), skip=skip)
        log.info("scan found %s", found)
        moved = False
        for c in s["printers"]:
            if c["id"] not in lost or not c.get("host"):
                continue
            ip = next((ip for ip, (h, _m) in found.items() if h == c["host"]), None)
            if ip and ip != c["ip"]:
                log.warning("%s: new address %s (was %s)", c["id"], ip, c["ip"])
                self.event(c["id"], "info", f"nova adresa štampača {ip}", tg=False)
                c["ip"] = ip
                self.printers[c["id"]].move(ip)
                self.prev[c["id"]].ip = ip
                moved = True
        if moved:
            settings.save(s)
            if self.on_move:
                self.on_move(s["printers"])

    def _once(self, pid, key, *a, **k):
        tr = self.track[pid]
        if key not in tr["warned"]:
            tr["warned"].add(key)
            self.event(pid, *a, **k)

    def _check(self, pid, p: Printer, s):
        tr, now = self.track[pid], time.time()
        cam = self.cams[pid]
        printing = p.printing()
        was_printing = tr.get("was_printing", False)
        tr["was_printing"] = printing or (was_printing and not p.online)
        # printer lost during a job
        if not p.online and was_printing and now - p.since_offline > 120:
            self._once(pid, "offline", "bad", "štampač se ne javlja više od 2 minuta tokom štampe")
        if p.online:
            tr["warned"].discard("offline")
        if not printing:
            return
        st = p.snapshot()
        # camera
        f, ft = cam.latest()
        if now - max(ft, self.t0) > 120 and (st["job_s"] or 0) > 120:
            self._once(pid, "camera", "warn", "kamera ne daje sliku, praćenje slike ne radi")
        elif now - ft < 10:
            tr["warned"].discard("camera")
            if now - tr.get("snap_t", 0) > 300:
                tr["snap_t"] = now
                try:
                    self._snapshot(pid, f)
                except Exception:
                    log.exception("%s: snapshot failed", pid)
        # nozzle temperature
        if st["nozzle_t"] and st["nozzle"] is not None and (st["job_s"] or 0) > 300:
            if abs(st["nozzle"] - st["nozzle_t"]) > 15:
                tr["temp_bad_t"] = tr["temp_bad_t"] or now
                if now - tr["temp_bad_t"] > 90:
                    self._once(pid, "temp", "bad", f"temperatura dizne {st['nozzle']}° umesto {st['nozzle_t']}°",
                               self.photo(pid), self.buttons(pid))
            else:
                tr["temp_bad_t"] = None
        # stalled
        key = (p.status.get("layer"), p.status.get("curPosition"))
        if key != tr["move_key"]:
            tr["move_key"], tr["move_t"] = key, now
        elif now - tr["move_t"] > 600:
            self._once(pid, "stall", "warn", "štampa stoji već 10 minuta (glava se ne pomera)",
                       self.photo(pid), self.buttons(pid))
        # image check
        layer = int(p.status.get("layer") or 0)
        zc = self.zc(pid)
        if layer < 3 or abs(zc - tr.get("det_z", zc)) > 1.0:
            # warming up, leveling, first layers or the bed moved: nothing to compare yet
            if layer >= 3 or not tr.get("det_hold"):
                self.det[pid].reset("first layers" if layer < 3 else "bed moved")
            tr["det_hold"] = layer < 3
            tr["det_z"] = zc
        elif s.get("detect", True) and now - ft < 10:
            tr["det_hold"], tr["det_z"] = False, zc
            al = self.det[pid].feed(f, s.get("sensitivity", "srednja"), self._roi(pid, f))
            if al and al.get("kind") == "outside":
                pass  # beside the job: only a stricter check of the job (detect.py), no message
            elif al:
                img = self.photo(pid, al["box"])
                tr["alarm_jpg"] = img
                auto = s.get("auto_pause", {}).get("slika") and not tr["paused_by_app"]
                txt = "naglo se promenilo na ploči — moguće odlepljivanje ili špageti"
                if auto:
                    self.event(pid, "bad", txt + ". Aplikacija pauzira štampu.", img, self.buttons(pid, True))
                    self.command(pid, "pause", "aplikacija")
                else:
                    self.event(pid, "bad", txt, img, self.buttons(pid))

    # ---------- commands ----------
    def command(self, pid, action, who="", msg_id=None) -> str:
        p = self.printers.get(pid)
        if not p or action not in EXPECT:
            return "Nepoznata komanda."
        if not p.online:
            return f"{p.name} nije povezan."
        if action == "pause":
            self.track[pid]["paused_by_app"] = True
            self.track[pid]["pause_who"] = who or "aplikacija"
        if not p.command(action):
            return "Komanda nije poslata."
        self.event(pid, "info", f"poslata komanda: {ACTION_TEXT[action]}" + (f" ({who})" if who else ""), tg=False)
        threading.Thread(target=self._confirm, args=(pid, action, msg_id), daemon=True).start()
        return f"{p.name}: poslata komanda ({ACTION_TEXT[action]})."

    def _confirm(self, pid, action, msg_id=None):
        p = self.printers[pid]
        t0 = time.time()
        while time.time() < t0 + WAIT_S[action]:
            if p.status.get("state") in EXPECT[action]:
                log.info("%s: %s confirmed (state %s) after %.0f s", pid, action, p.status.get("state"), time.time() - t0)
                self.event(pid, "info", f"štampač je potvrdio: {ACTION_TEXT[action]}", tg=False)
                return
            time.sleep(0.5)
        if msg_id and self.bot and action in ("pause", "resume"):  # put the button back as it was
            self.bot.edit_buttons(msg_id, self.buttons(pid, action == "resume"))
        self.event(pid, "bad", f"štampač NIJE potvrdio komandu ({ACTION_TEXT[action]}) — proveri ga",
                   self.photo(pid))

    def on_button(self, data, who, msg_id=None):
        act, _, pid = data.partition(":")
        if act == "clean":
            pid, _, job = pid.partition(":")
            n = self._cleanup_job(pid, job)
            self.bot.edit_buttons(msg_id, None)
            return f"Obrisano poruka: {n}."
        if pid not in self.printers:
            return "Nepoznat štampač."
        name = self.printers[pid].name
        if act == "pause":  # confirmation row in place of the buttons, like Zaustavi
            self.bot.edit_buttons(msg_id, [[{"text": "Da, pauziraj", "callback_data": f"pauseok:{pid}"},
                                            {"text": "Ne", "callback_data": f"no:{pid}"}]])
            return f"{name}: potvrdi pauzu."
        if act in ("pauseok", "resume"):
            act = "pause" if act == "pauseok" else act
            r = self.command(pid, act, who, msg_id)
            if "poslata" in r:  # the button turns into its opposite right away
                self.bot.edit_buttons(msg_id, self.buttons(pid, act == "pause"))
            return r
        if act == "stop":
            self.bot.edit_buttons(msg_id, [[{"text": "Da, zaustavi", "callback_data": f"stopok:{pid}"},
                                            {"text": "Ne", "callback_data": f"no:{pid}"}]])
            return f"{name}: potvrdi zaustavljanje."
        if act == "stopok":
            r = self.command(pid, "stop", who, msg_id)
            self.bot.edit_buttons(msg_id, None)
            return r
        if act == "no":
            self.bot.edit_buttons(msg_id, self.buttons(pid, self.printers[pid].paused()))
            return "Odustano."
        return "U redu."

    def tg_link(self):
        """Group link, asked once every 10 min at most (the bot may become admin later)."""
        now = time.time()
        if self.bot and self.bot.ok and now - getattr(self, "_tgl_t", 0) > 600:
            self._tgl_t = now
            threading.Thread(target=lambda: setattr(self, "_tgl", self.bot.group_link()), daemon=True).start()
        return getattr(self, "_tgl", "")

    def link(self):
        t = self.tunnel
        if not (t and t.url):
            return ""
        return f"{t.url}/?k={self.remote_key()}"

    def remote_key(self):
        s = settings.load()
        if not s.get("remote_key"):
            import secrets
            s["remote_key"] = secrets.token_urlsafe(24)
            settings.save(s)
        return s["remote_key"]

    def send_link(self):
        if not self.bot:
            return
        import json
        l = self.link()
        f = self.data / "tg_link.json"
        try:
            old = json.loads(f.read_text(encoding="utf-8")).get("msg")
        except (OSError, ValueError):
            old = None
        mid = self.bot.send(f"Strana nadzora (otvori na telefonu):\n{l}" if l else
                            "Strana za telefon trenutno nije dostupna.")
        if mid:  # only the newest link stays in the group (older links no longer work anyway)
            if old and old != mid:
                self.bot.delete([old])
            f.write_text(json.dumps({"msg": mid}), encoding="utf-8")

    def on_command(self, cmd):
        if cmd == "strana":
            self.send_link()
        elif cmd in ("status", "start"):
            self.bot.send(self.status_text())
        elif cmd == "slika":
            self.send_photos(list(self.printers))

    def send_photos(self, pids):
        """Picture only of printers that work; the others get one line of text together."""
        idle = []
        for pid in pids:
            p = self.printers[pid]
            active = p.printing() or p.paused()
            txt = f"<b>{p.name}</b>: {p.snapshot()['state_text'] if p.online else 'nije povezan'}"
            if not active:
                idle.append(txt)
                continue
            if p.printing():
                txt += "\n" + self._job_line(p)
            img = self.photo(pid)
            mid = self.bot.send(txt, img, self.buttons(pid, p.paused())) if img else \
                self.bot.send(txt + "\n(nema slike sa kamere)")
            self._remember_msg(pid, mid)
        if idle:
            self.bot.send("\n".join(idle))

    def status_text(self):
        rows = []
        for p in self.printers.values():
            s = p.snapshot()
            if not p.online:
                rows.append(f"<b>{p.name}</b>: nije povezan")
                continue
            r = f"<b>{p.name}</b>: {s['state_text']}"
            if s["state"] == 1 or s["paused"]:
                r += "\n" + self._job_line(p)
            r += f"\nDizna {s['nozzle']}° · ploča {s['bed']}°"
            rows.append(r)
        return "\n\n".join(rows)

    # ---------- camera view of the job ----------
    def _roi(self, pid, frame):
        """Where the job is on the camera picture (from the project); refreshed per mm of height."""
        tr = self.track[pid]
        zc = self.zc(pid)
        pv = self.prev[pid]
        key = (round(zc), tuple(sorted(getattr(pv, "skipped", ()))), len(getattr(pv, "polys", None) or {}), frame.shape[:2])
        if tr.get("roi_key") != key:
            tr["roi_key"] = key
            h, w = frame.shape[:2]
            tr["roi"] = self.prev[pid].watch_mask(self.view(pid), zc, (w, h))
            if tr["roi"] is not None and tr.get("roi_logged") != tr.get("print_id"):
                tr["roi_logged"] = tr.get("print_id")
                log.info("%s: image check limited to the job area (%.0f%% of the picture)", pid, 100 * tr["roi"].mean())
        return tr.get("roi")

    def zc(self, pid) -> float:
        m = re.search(r"Z:(-?[\d.]+)", str(self.printers[pid].status.get("curPosition", "")))
        return float(m.group(1)) if m else 0.0

    def view(self, pid):
        v = settings.load().get("views", {}).get(pid)
        if v:
            return v
        try:  # calibration prepared from the saved camera pictures (data/views.json)
            import json
            return json.loads((self.data / "views.json").read_text(encoding="utf-8")).get(pid)
        except (OSError, ValueError):
            return None

    def _snapshot(self, pid, f):
        """Every 5 min: camera picture + printer position, for setting up the camera view."""
        import json
        d = self.data / "kalibracija"
        d.mkdir(exist_ok=True)
        st = self.printers[pid].status
        cv2.imwrite(str(d / f"{pid}.jpg"), f, [cv2.IMWRITE_JPEG_QUALITY, 92])
        (d / f"{pid}.json").write_text(json.dumps({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "z": self.zc(pid), "pos": st.get("curPosition"),
            "layer": st.get("layer"), "file": st.get("printFileName"), "objects": st.get("objects"),
            "size": [f.shape[1], f.shape[0]]}, ensure_ascii=False), encoding="utf-8")

    def save_view(self, pid, pts):
        f, _ = self.cams[pid].latest()
        if f is None:
            return "Nema slike sa kamere."
        h, w = f.shape[:2]
        bed = next((p.get("bed") for p in settings.load()["printers"] if p["id"] == pid), None) or BED.get(pid, [300, 300])
        v = {"pts": [[float(x), float(y)] for x, y in pts], "size": [w, h], "bed": bed, "z": self.zc(pid)}
        from .preview import _cam, project
        try:
            cam = _cam(v)
        except Exception:
            log.exception("%s: view calibration failed", pid)
            return "Pogled nije mogao da se izračuna. Probaj ponovo."
        s = settings.load()
        s.setdefault("views", {})[pid] = v
        settings.save(s)
        log.info("%s: camera view saved %s, error %.1f px", pid, v, cam[3])
        # check picture: bed outline over the live frame (for tuning)
        try:
            chk = f.copy()
            bx, by = bed
            grid = [[x, y, 0] for x in range(0, bx + 1, 50) for y in (0, by)] + [[x, y, 0] for y in range(0, by + 1, 50) for x in (0, bx)]
            uv = project(np.array(grid, np.float32), v, cam, v["z"]).astype(np.int32)
            for i in range(0, len(uv), 2):
                cv2.line(chk, tuple(uv[i]), tuple(uv[i + 1]), (65, 164, 217), 2, cv2.LINE_AA)
            for i, (x, y) in enumerate(pts):
                cv2.circle(chk, (int(x), int(y)), 8, (0, 0, 255), 2)
            cv2.imwrite(str(self.photos / f"kalibracija_{pid}.jpg"), chk)
        except Exception:
            log.exception("%s: check picture failed", pid)
        return ""

    # ---------- page ----------
    def state(self):
        out = []
        for pid, p in self.printers.items():
            s = p.snapshot()
            s.pop("objects", None)
            cam = self.cams[pid]
            _, ft = cam.latest()
            s["camera"] = time.time() - ft < 10
            s["detect"] = self.det[pid].state()
            s["preview"] = self.prev[pid].state
            s["view"] = bool(self.view(pid))
            s["left"] = format_duration(s["left_s"]) if s["left_s"] else ""
            tr = self.track[pid]
            s["skipped"] = len(tr.get("skipped") or ()) if tr.get("skip_job") == tr.get("print_id") else 0
            s["objects_n"] = tr.get("objects_n") or 0
            out.append(s)
        with self.lock:
            ev = [dict(e) for e in list(self.events)[-150:]]
        today = time.strftime("%Y-%m-%d")
        for e in ev:  # older days carry the date
            if time.strftime("%Y-%m-%d", time.localtime(e["t"])) != today:
                e["time"] = time.strftime("%d.%m. %H:%M", time.localtime(e["t"]))
        return {"printers": out, "events": ev[::-1],
                "telegram": {"ok": bool(self.bot and self.bot.ok), "name": self.bot.name if self.bot else "",
                             "token": bool(self.bot and self.bot.token),
                             "chat": (settings.load()["telegram"].get("chat_name")
                                      or getattr(self.bot, "title", "") or "grupa")
                             if settings.load()["telegram"].get("chat_id") else "",
                             "link": self.tg_link()},
                "tunnel": {"named": bool(self.tunnel and self.tunnel.token), "url": self.tunnel.url if self.tunnel else "",
                           "ok": bool(self.tunnel and self.tunnel.connected()),
                           "on": bool(settings.load().get("remote", True))}}
