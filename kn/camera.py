"""Printer cameras: go2rtc turns the Creality WebRTC stream into local RTSP, OpenCV reads frames.

go2rtc listens only on 127.0.0.1 (API 1984, RTSP 8554). Verified 20.11.2026: K1 Max H264 1280x720,
K2 Plus H264 1920x1080 via `webrtc:http://IP:8000/call/webrtc_local#format=creality`.
"""
import os
import subprocess
import threading
import time
from pathlib import Path

from . import applog

log = applog.get("camera")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import cv2  # noqa: E402  (env var must be set before import)

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BELOW_NORMAL = 0x00004000 if os.name == "nt" else 0


class Go2rtc:
    def __init__(self, exe: Path, conf: Path, logfile: Path, printers):
        self.exe, self.conf, self.logfile = exe, conf, logfile
        self.proc = None
        self.write(printers)

    def write(self, printers):
        conf = self.conf
        lines = ['api:\n  listen: "127.0.0.1:1984"\n', 'rtsp:\n  listen: "127.0.0.1:8554"\n',
                 'webrtc:\n  listen: ""\n', "log:\n  level: warn\n", "streams:\n"]
        lines += [f"  {p['id']}: webrtc:http://{p['ip']}:8000/call/webrtc_local#format=creality\n" for p in printers]
        conf.write_text("".join(lines), encoding="utf-8")

    def start(self):
        if not self.exe.exists():
            log.error("go2rtc missing: %s", self.exe)
            return
        self.proc = subprocess.Popen([str(self.exe), "-c", str(self.conf)], cwd=self.exe.parent,
                                     stdout=open(self.logfile, "a", encoding="utf-8"), stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW | BELOW_NORMAL)
        log.info("go2rtc started pid=%s", self.proc.pid)
        threading.Thread(target=self._watch, name="go2rtc-watch", daemon=True).start()

    def _watch(self):
        mine = self.proc
        while self.proc is mine and mine:
            code = mine.poll()
            if code is not None:
                log.error("go2rtc exited code=%s, restarting in 5 s", code)
                time.sleep(5)
                if self.proc is mine:
                    self.start()
                return
            time.sleep(2)

    def restart(self, printers):
        """Printer got a new address: new config, go2rtc started again."""
        self.write(printers)
        self.stop()
        self.start()

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
            log.info("go2rtc stopped")


class Camera:
    """Keeps the newest decoded frame. `source` is an RTSP url (or a video file in tests)."""

    def __init__(self, pid, source, loop_file=False):
        self.id, self.source, self.loop_file = pid, source, loop_file
        self.frame = None
        self.frame_t = 0.0
        self.jpeg = b""
        self._jpeg_t = 0.0
        self.ok = False
        self._stop = False
        self._lock = threading.Lock()
        threading.Thread(target=self._run, name=f"cam-{pid}", daemon=True).start()

    def latest(self):
        with self._lock:
            return self.frame, self.frame_t

    def jpg(self, quality=80):
        f, t = self.latest()
        if f is None:
            return b""
        if t != self._jpeg_t:
            ok, b = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok:
                self.jpeg, self._jpeg_t = b.tobytes(), t
        return self.jpeg

    def stop(self):
        self._stop = True

    def _run(self):
        back = 3
        while not self._stop:
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                if self.ok:
                    log.warning("%s: camera lost", self.id)
                self.ok = False
                cap.release()
                time.sleep(back)
                back = min(back * 2, 60)
                continue
            log.info("%s: camera open", self.id)
            fails = 0
            delay = 1 / 8 if self.loop_file else 0
            while not self._stop:
                ok, f = cap.read()
                if not ok:
                    if self.loop_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    fails += 1
                    if fails > 20:
                        break
                    time.sleep(0.1)
                    continue
                fails, back = 0, 3
                with self._lock:
                    self.frame, self.frame_t = f, time.time()
                self.ok = True
                if delay:
                    time.sleep(delay)
            cap.release()
            if self.ok:
                log.warning("%s: camera stream ended, reconnecting", self.id)
            self.ok = False
            time.sleep(back)
