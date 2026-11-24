"""Job preview from the project files (Creality Print .3mf / exported .gcode) in the projects folder.

Used when the printer does not give its G-code (K1 Max). The job name the printer reports
("ram5_ploca.stl_PLA_1h29m29s.gcode") is matched to an object name in a .3mf; the object's mesh is placed
on the bed with the project's transform and cut into layers with the project's layer heights.
Read only - project files are never changed.
"""
import json
import re
import zipfile
from pathlib import Path

import numpy as np

from . import applog

log = applog.get("project")


def job_stem(job_path: str) -> str:
    n = job_path.replace("\\", "/").rsplit("/", 1)[-1]
    n = n.removesuffix(".gcode")
    n = re.split(r"\.(stl|3mf|obj|step)", n, flags=re.I)[0]
    n = re.split(r"_(PLA|PETG|ABS|TPU|ASA|PA|PC)[-_]", n)[0]
    n = re.split(r"_ID_\d+", n, flags=re.I)[0]
    return n.strip().lower()


def find(projects_dir: Path, job_path: str, max_files=300, names=None):
    """-> ("gcode", path) | ("3mf", path, [object ids]) | None. Newest files first.
    names = object names the printer reports for the job (all objects on the plate)."""
    stem = job_stem(job_path)
    want = {job_stem(n) for n in (names or [])} | {stem}
    if not stem or not projects_dir.exists():
        return None
    files = sorted((p for p in projects_dir.rglob("*") if p.suffix.lower() in (".3mf", ".gcode")),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:max_files]
    for p in files:
        if p.suffix.lower() == ".gcode" and job_stem(p.name) == stem:
            return ("gcode", p)
    for p in files:
        if p.suffix.lower() != ".3mf":
            continue
        try:
            with zipfile.ZipFile(p) as z:
                cfg = z.read("Metadata/model_settings.config").decode("utf-8", "replace")
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
        objs = re.findall(r'<object id="(\d+)">\s*<metadata key="name" value="([^"]*)"', cfg)
        if any(job_stem(name) == stem for _, name in objs):
            ids = [oid for oid, name in objs if job_stem(name) in want]
            return ("3mf", p, ids)
    return None


def _floats(s):
    return [float(v) for v in s.split()]


def load_layers(path: Path, ids, total_layers=None):
    """Mesh of the matched objects cut into layers -> list of layers, each a list of 2x3 segments."""
    with zipfile.ZipFile(path) as z:
        model = z.read("3D/3dmodel.model").decode("utf-8", "replace")
        try:
            ps = json.loads(z.read("Metadata/project_settings.config"))
        except (KeyError, ValueError):
            ps = {}
        tris = []
        for oid in ids:
            item = re.search(rf'<item objectid="{oid}"[^>]*transform="([^"]+)"', model)
            comp = re.search(rf'<object id="{oid}"[^>]*>\s*<components>\s*<component p:path="([^"]+)"[^>]*transform="([^"]+)"',
                             model)
            if not (item and comp):
                continue
            sub = z.read(comp.group(1).lstrip("/")).decode("utf-8", "replace")
            v = np.array(re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"', sub), np.float64)
            t = np.array(re.findall(r'<triangle v1="(\d+)" v2="(\d+)" v3="(\d+)"', sub), np.int64)
            if not len(v) or not len(t):
                continue
            for tr in (comp.group(2), item.group(1)):  # 3MF: 3x4 matrix, row vectors
                m = np.array(_floats(tr)).reshape(4, 3)
                v = v @ m[:3] + m[3]
            tris.append(v[t])
    if not tris:
        return []
    T = np.concatenate(tris).astype(np.float32)
    h0 = float(ps.get("initial_layer_print_height") or 0.2)
    lh = float(ps.get("layer_height") or 0.2)
    top = float(T[:, :, 2].max())
    n = int(round((top - h0) / lh)) + 1
    if total_layers and abs(total_layers - n) > 2:
        log.warning("%s: %d layers from the project, printer says %d", path.name, n, total_layers)
    zs = [h0 / 2] + [h0 + lh * (i - 0.5) for i in range(1, n)]
    zmin, zmax = T[:, :, 2].min(1), T[:, :, 2].max(1)
    layers = []
    for i, zc in enumerate(zs):
        sel = T[(zmin <= zc) & (zmax > zc)]
        segs = _cut(sel, zc)
        zl = h0 if i == 0 else h0 + lh * i
        if len(segs):
            segs[:, :, 2] = zl
        layers.append(list(segs))
    log.info("%s: %d layers from mesh (%d triangles)", path.name, len(layers), len(T))
    return layers


def _cut(tri, z):
    """Triangles crossing plane z -> Nx2x3 segments."""
    if not len(tri):
        return np.zeros((0, 2, 3), np.float32)
    d = tri[:, :, 2] - z
    out = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        da, db = d[:, a], d[:, b]
        cross = (da > 0) != (db > 0)
        t = np.where(cross, da / np.where(da - db == 0, 1, da - db), np.nan)
        p = tri[:, a] + (tri[:, b] - tri[:, a]) * t[:, None]
        out.append(p)
    P = np.stack(out, 1)  # N x 3 edges x 3
    ok = ~np.isnan(P[:, :, 0])
    good = ok.sum(1) == 2
    P, ok = P[good], ok[good]
    first = np.argmax(ok, 1)
    last = 2 - np.argmax(ok[:, ::-1], 1)
    idx = np.arange(len(P))
    return np.stack([P[idx, first], P[idx, last]], 1).astype(np.float32)
