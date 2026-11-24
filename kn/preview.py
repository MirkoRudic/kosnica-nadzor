"""'Kako treba da izgleda do sada': the job's G-code drawn up to the printer's current layer.

The G-code is read from the printer (GET only). K2 Plus: Moonraker :7125 (measured). K1 Max: the
address is not known yet - several are tried and the result is logged; if none works, the printer's
own preview image of the job (`/downloads/original/current_print_image.png`, not verified) is shown.
"""
import math
import os
import re
import threading
import urllib.parse
import urllib.request

import cv2
import numpy as np

from . import applog

log = applog.get("preview")
W, H = 640, 400
GHOST = (90, 90, 90)
DONE = (65, 164, 217)      # accent, BGR
CURRENT = (240, 240, 240)
SKIP = (70, 70, 200)       # skipped object footprint (red, BGR)
BG = (14, 12, 11)
YAW, PITCH = math.radians(35), math.radians(25)


def _cam(view):
    key = repr(view)
    if key not in Preview._cams and "K" in view:  # fitted outside (data/views.json)
        rv = np.array(view["rvec"], np.float64).reshape(3, 1)
        tv = np.array(view["tvec"], np.float64).reshape(3, 1)
        Preview._cams[key] = (np.array(view["K"], np.float64), rv, tv, 0.0)
    if key not in Preview._cams:
        Preview._cams[key] = solve_view(view)
        log.info("camera view solved: reprojection error %.1f px", Preview._cams[key][3])
    return Preview._cams[key]


def _urls(ip, name):
    q = urllib.parse.quote(name)
    return [f"http://{ip}:7125/server/files/gcodes/{q}",
            f"http://{ip}/server/files/gcodes/{q}",
            f"http://{ip}/downloads/gcodes/{q}",
            f"http://{ip}/downloads/{q}"]


def parse(text_iter):
    """-> list of layers; each layer = list of Nx3 float arrays (x, y, z) of extruding paths."""
    layers, cur, path = [], [], []
    x = y = z = e = 0.0
    lz = None
    rel_xyz, rel_e = False, False
    num = re.compile(r"([XYZEF])(-?\d*\.?\d+)")

    def flush():
        nonlocal path
        if len(path) > 1:
            cur.append(np.array(path, np.float32))
        path = []

    for raw in text_iter:
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        c = line.split(None, 1)[0].upper()
        if c in ("G0", "G1", "G2", "G3"):
            v = {k: float(n) for k, n in num.findall(line.upper())}
            nx = x + v.get("X", 0) if rel_xyz else v.get("X", x)
            ny = y + v.get("Y", 0) if rel_xyz else v.get("Y", y)
            nz = z + v.get("Z", 0) if rel_xyz else v.get("Z", z)
            de = v.get("E", 0) if rel_e else (v["E"] - e if "E" in v else 0)
            if "E" in v:
                e = e + v["E"] if rel_e else v["E"]
            if de > 0 and (nx != x or ny != y) and c != "G0":
                if lz is None or nz > lz + 0.01:
                    flush()
                    if cur:
                        layers.append(cur)
                    cur, lz = [], nz
                if not path:
                    path = [(x, y, nz)]
                px, py, _ = path[-1]
                if len(path) > 1 and (nx - px) ** 2 + (ny - py) ** 2 < 0.25:
                    path[-1] = (nx, ny, nz)   # thin out tiny segments, keep the end point
                else:
                    path.append((nx, ny, nz))
            else:
                flush()
            x, y, z = nx, ny, nz
        elif c == "G90":
            rel_xyz = False
        elif c == "G91":
            rel_xyz = True
        elif c == "M82":
            rel_e = False
        elif c == "M83":
            rel_e = True
        elif c == "G92":
            v = {k: float(n) for k, n in num.findall(line.upper())}
            e = v.get("E", e)
    flush()
    if cur:
        layers.append(cur)
    return layers


class Preview:
    _cams = {}

    def __init__(self, pid, ip, projects_dir=None):
        self.id, self.ip, self.projects_dir = pid, ip, projects_dir
        self.job = None
        self.state = "nema"          # nema | ucitava | gcode | slika | nedostupno
        self.layers = []
        self.thumb = b""
        self._proj = None
        self._done_img = None
        self._done_n = 0
        self._cache = (None, b"")
        self._lock = threading.Lock()

    def set_objects(self, objects):
        """Object names and outlines from the printer (they can arrive after the file name)."""
        try:
            import json as _j
            obj = _j.loads(objects) if isinstance(objects, str) else (objects or [])
            self.names = [o.get("name", "") for o in obj if isinstance(o, dict)]
            self.polys = {o["name"].upper(): np.array(o["polygon"], np.float32) for o in obj
                          if isinstance(o, dict) and o.get("name") and len(o.get("polygon") or []) >= 3}
        except (ValueError, TypeError, KeyError):
            self.names, self.polys = [], {}
        self._owner = None

    def set_job(self, path: str, total_layers=None, objects=None):
        if not path or path == self.job:
            return
        self.total_layers = total_layers
        self.set_objects(objects)
        self.skipped = set()
        self.job = path
        with self._lock:
            self.layers, self.thumb, self._done_img, self._done_n, self._cache = [], b"", None, 0, (None, b"")
            if hasattr(self, "_flat"):
                del self._flat
            self._owner = None
        self.state = "ucitava"
        threading.Thread(target=self._load, args=(path,), name=f"preview-{self.id}", daemon=True).start()

    def _load(self, path):
        test = os.environ.get("KN_TEST_GCODE")
        if test:
            with self._lock:
                self.layers = parse(open(test))
                self._fit()
            self.state = "gcode"
            return
        name = path.replace("\\", "/").rsplit("/", 1)[-1]
        for url in _urls(self.ip, name):
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    if r.status != 200:
                        continue
                    first = r.read(4096)
                    if b"G1" not in first and b";" not in first[:200]:
                        continue
                    lines = _lines(first, r)
                    layers = parse(lines)
                if not layers:
                    continue
                if path != self.job:
                    return
                with self._lock:
                    self.layers = layers
                    self._fit()
                self.state = "gcode"
                log.info("%s: gcode from %s, %d layers", self.id, url.split("/server")[0].split("/downloads")[0], len(layers))
                return
            except Exception as e:
                log.info("%s: gcode not at %s (%s)", self.id, url.rsplit("/", 1)[0], type(e).__name__)
        if self._from_projects(path):
            return
        try:
            with urllib.request.urlopen(f"http://{self.ip}/downloads/original/current_print_image.png", timeout=10) as r:
                img = cv2.imdecode(np.frombuffer(r.read(), np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
                self.thumb = b.tobytes() if ok else b""
                self.state = "slika" if self.thumb else "nedostupno"
                log.info("%s: no gcode, using printer preview image", self.id)
                return
        except Exception as e:
            log.info("%s: no printer preview image (%s)", self.id, type(e).__name__)
        self.state = "nedostupno"
        log.warning("%s: job preview not available for %s", self.id, path)

    def set_skipped(self, names):
        """Objects the printer will not print (skipped in the slicer / on the printer): drawn grey."""
        names = {n.upper() for n in names}
        if names != getattr(self, "skipped", set()):
            with self._lock:
                self.skipped = names
                self._cache, self._done_img, self._done_n = (None, b""), None, 0

    def _skip_mask(self):
        """Per layer, per path: True if the path belongs to a skipped object (path middle inside its outline)."""
        if not getattr(self, "skipped", None) or not getattr(self, "polys", None):
            return None
        if getattr(self, "_owner", None) is None:
            names = list(self.polys)
            own = []
            for L in self.layers:
                row = []
                for pth in L:
                    m = pth[len(pth) // 2]
                    hit = next((n for n in names if cv2.pointPolygonTest(self.polys[n], (float(m[0]), float(m[1])), True) > -1.0), "")
                    row.append(hit)
                own.append(row)
            self._owner = own
        return [[o in self.skipped for o in row] for row in self._owner]

    def watch_mask(self, view, zc, size, margin=10.0):
        """Camera-pixel mask (h, w) of the area the job occupies: every printed object (not skipped)
        from the bed up to the current height, a little wider. None when unknown (no outlines/view)."""
        polys = {n: p for n, p in (getattr(self, "polys", None) or {}).items() if n not in getattr(self, "skipped", set())}
        if not polys or not view:
            return None
        try:
            cam = _cam(view)
        except Exception as e:
            log.warning("%s: mask: %s", self.id, e)
            return None
        w, h = size
        sx, sy = w / view["size"][0], h / view["size"][1]
        m = np.zeros((h, w), np.uint8)
        top = max(zc, 0.0) + 3.0
        for poly in polys.values():
            c = poly.mean(axis=0)
            d = poly - c
            n = np.linalg.norm(d, axis=1, keepdims=True)
            out = c + d * (1 + margin / np.maximum(n, 1e-3))
            pts = np.concatenate([np.c_[out, np.zeros(len(out))], np.c_[out, np.full(len(out), top)]]).astype(np.float32)
            uv = project(pts, view, cam, zc)
            uv = uv[~np.isnan(uv).any(axis=1)]
            if len(uv) < 3:
                continue
            uv = uv * [sx, sy]
            cv2.fillConvexPoly(m, cv2.convexHull(uv.astype(np.int32)), 1)
        return m if m.any() else None

    def _render_cam(self, layer, view, zc):
        with self._lock:
            return _render_cam_impl(self, layer, view, zc)

    def _from_projects(self, path) -> bool:
        """Printer gave no G-code: look for the job in the projects folder (exported .gcode or .3mf)."""
        if not self.projects_dir:
            return False
        from pathlib import Path
        from . import project3mf
        try:
            hit = project3mf.find(Path(self.projects_dir), path, names=getattr(self, "names", None))
            if not hit:
                log.info("%s: job not found in %s", self.id, self.projects_dir)
                return False
            layers = parse(open(hit[1], encoding="ascii", errors="ignore")) if hit[0] == "gcode" \
                else project3mf.load_layers(hit[1], hit[2], self.total_layers)
            if not layers or path != self.job:
                return False
            with self._lock:
                self.layers = layers
                self._fit()
            self.state = "gcode"
            log.info("%s: job drawn from project %s (%d layers)", self.id, hit[1].name, len(layers))
            return True
        except Exception:
            log.exception("%s: reading project failed", self.id)
            return False

    def _fit(self):
        pts = np.concatenate([p for L in self.layers for p in L[:: max(1, len(L) // 50)]])
        cy, sy = math.cos(YAW), math.sin(YAW)
        cp, sp = math.cos(PITCH), math.sin(PITCH)

        def proj(a):
            xr = a[:, 0] * cy - a[:, 1] * sy
            yr = a[:, 0] * sy + a[:, 1] * cy
            return np.stack([xr, -yr * sp - a[:, 2] * cp], 1)

        uv = proj(pts)
        mn, mx = uv.min(0), uv.max(0)
        s = min((W - 40) / max(mx[0] - mn[0], 1), (H - 40) / max(mx[1] - mn[1], 1))
        off = np.array([W / 2, H / 2]) - (mn + mx) / 2 * s
        self._proj = lambda a: (proj(a) * s + off).astype(np.int32)
        self._bg = np.full((H, W, 3), BG, np.uint8)

    def render(self, layer: int | None, view=None, zc: float = 0.0) -> bytes:
        """JPEG of the job up to `layer` (1-based, as the printer reports it); None = whole job.
        view = camera calibration -> drawn from the printer camera's point of view."""
        if self.state == "slika":
            return self.thumb
        if self.state != "gcode":
            return b""
        if view:
            try:
                return self._render_cam(layer, view, zc)
            except Exception:
                log.exception("%s: camera-view render failed, using default view", self.id)
        with self._lock:
            n = len(self.layers)
            k = n if layer is None else max(0, min(n, int(layer)))
            if self._cache[0] == k:
                return self._cache[1]
            if self._done_img is None or k < self._done_n:
                self._done_img, self._done_n = self._bg.copy(), 0
            sk = self._skip_mask()
            sel = (lambda i, want: [self._proj(p) for j, p in enumerate(self.layers[i]) if (sk[i][j] if sk else False) == want])
            for i in range(self._done_n, max(0, k - 1)):
                cv2.polylines(self._done_img, sel(i, False), False, DONE, 1, cv2.LINE_AA)
            if sk and self.layers:  # skipped object: only its footprint
                cv2.polylines(self._done_img, sel(0, True), False, SKIP, 2, cv2.LINE_AA)
            self._done_n = max(self._done_n, k - 1)
            img = self._done_img.copy()
            if 0 < k <= n:
                cv2.polylines(img, sel(k - 1, False), False, CURRENT, 2, cv2.LINE_AA)
            if k < n:  # what is still to come, drawn faint over the done part (it is above it)
                ghost = np.zeros_like(img)
                for i in range(k, n):
                    cv2.polylines(ghost, sel(i, False), False, GHOST, 1, cv2.LINE_AA)
                img = np.maximum(img, ghost) if k == 0 else cv2.addWeighted(img, 1.0, ghost, 0.6, 0)
            ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            self._cache = (k, b.tobytes() if ok else b"")
            return self._cache[1]


def excluded(ip, timeout=5):
    """Names of objects the printer skips (Klipper exclude_object via Moonraker :7125), None if not available."""
    import json
    try:
        with urllib.request.urlopen(f"http://{ip}:7125/printer/objects/query?exclude_object", timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        eo = d["result"]["status"]["exclude_object"]
        return {str(n).upper() for n in eo.get("excluded_objects") or []}, eo
    except Exception as e:
        return None, type(e).__name__


def solve_view(view):
    """4 clicked bed corners (front-left, front-right, back-right, back-left) -> camera matrix and pose.
    One planar view: focal length and pose are estimated, principal point fixed at the centre, no distortion."""
    w, h = view["size"]
    bx, by = view["bed"]
    obj = np.array([[0, 0, 0], [bx, 0, 0], [bx, by, 0], [0, by, 0]], np.float32)
    img = np.array(view["pts"], np.float32)
    K = np.array([[w * 0.6, 0, w / 2], [0, w * 0.6, h / 2], [0, 0, 1]], np.float64)
    flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO |
             cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
    err, K, dist, rv, tv = cv2.calibrateCamera([obj], [img], (w, h), K, np.zeros(5), flags=flags)
    return K, rv[0], tv[0], float(err)


def project(pts, view, cam, zc):
    """Bed coordinates (mm) -> camera pixels. The bed (and the part on it) moves down as Z grows;
    the camera is fixed to the frame (K1 measured: bed far below at Z 150)."""
    K, rv, tv, _ = cam
    a = pts.astype(np.float64).copy()
    a[:, 2] -= (zc - view.get("z", 0.0))
    dist = np.array(view["dist"], np.float64) if view.get("dist") else None  # wide lens (k1)
    uv, _ = cv2.projectPoints(a, rv, tv, K, dist)
    R, _ = cv2.Rodrigues(rv)
    depth = a @ R[2] + float(np.ravel(tv)[2])
    uv = uv.reshape(-1, 2)
    uv[depth < 5] = np.nan  # behind the camera: not drawn
    return uv


def _render_cam_impl(self, layer, view, zc):
    w, h = view["size"]
    cam = _cam(view)
    n = len(self.layers)
    k = n if layer is None else max(0, min(n, int(layer)))
    key = (k, round(zc, 1), repr(view), tuple(sorted(getattr(self, "skipped", ()))))
    if self._cache[0] == key:
        return self._cache[1]
    if not hasattr(self, "_flat"):
        allp = [p for L in self.layers for p in L]
        self._flat = np.concatenate(allp) if allp else np.zeros((0, 3), np.float32)
        idx, i = [], 0
        for L in self.layers:
            li = []
            for p in L:
                li.append((i, i + len(p)))
                i += len(p)
            idx.append(li)
        self._idx = idx
    uvf = project(self._flat, view, cam, zc)
    bad = np.isnan(uvf).any(1)
    uv = np.clip(np.nan_to_num(uvf), -10000, 10000).astype(np.int32)
    img = np.full((h, w, 3), BG, np.uint8)
    bx, by = view["bed"]
    edge = np.array([[x, 0, 0] for x in np.linspace(0, bx, 36)] + [[bx, y, 0] for y in np.linspace(0, by, 36)] +
                    [[x, by, 0] for x in np.linspace(bx, 0, 36)] + [[0, y, 0] for y in np.linspace(by, 0, 36)], np.float32)
    be = project(edge, view, cam, zc)
    for i in range(len(be) - 1):
        if not (np.isnan(be[i]).any() or np.isnan(be[i + 1]).any()):
            cv2.line(img, tuple(np.clip(be[i], -1e4, 1e4).astype(int)), tuple(np.clip(be[i + 1], -1e4, 1e4).astype(int)),
                     (60, 60, 60), 2, cv2.LINE_AA)
    sk = self._skip_mask()
    okb = lambda a, b: not bad[a:b].any()  # noqa: E731

    def paths(i, skipped=False):
        return [uv[a:b] for j, (a, b) in enumerate(self._idx[i]) if okb(a, b) and (sk[i][j] if sk else False) == skipped]
    # ghost (what is still to come) behind, done layers on top, shaded by height so the shape reads;
    # thin lines (1 px at camera resolution), so nearby layers do not melt into one blob
    if k < n:
        ghost = np.zeros_like(img)
        for i in range(k, n):
            cv2.polylines(ghost, paths(i), False, GHOST, 1, cv2.LINE_AA)
        img = cv2.add(img, (ghost * 0.55).astype(np.uint8))
    for i in range(max(0, k - 1)):
        f = 0.45 + 0.55 * (i + 1) / max(1, k)       # darker at the bottom, full colour at the top
        col = tuple(int(c * f) for c in DONE)
        cv2.polylines(img, paths(i), False, col, 1, cv2.LINE_AA)
    if sk and n:  # skipped object: only its footprint on the bed (when it was dropped is not known)
        cv2.polylines(img, paths(0, True), False, SKIP, 2, cv2.LINE_AA)
    if 0 < k <= n:
        cv2.polylines(img, paths(k - 1), False, CURRENT, 2, cv2.LINE_AA)
    if w > 960:
        img = cv2.resize(img, (960, int(h * 960 / w)), interpolation=cv2.INTER_AREA)
    ok, b = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    self._cache = (key, b.tobytes() if ok else b"")
    return self._cache[1]


def _lines(first, r):
    buf = first
    while True:
        chunk = r.read(1 << 20)
        if not chunk:
            break
        buf += chunk
        *done, buf = buf.split(b"\n")
        for l in done:
            yield l.decode("ascii", "ignore")
    if buf:
        for l in buf.split(b"\n"):
            yield l.decode("ascii", "ignore")
