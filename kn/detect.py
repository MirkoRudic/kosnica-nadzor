"""Image check for a printing job: a sudden change on the bed (part knocked loose, dragged, spaghetti).

How: every few seconds a small grey frame; the per-pixel median of the last ~30 s removes the moving
print head. That median is compared with the one from 60 s earlier. Normal printing changes the
picture slowly; a part that comes loose changes it at once. The threshold adapts to each job
(baseline of its own normal change). Not a trained model - numbers to be tuned on real jobs.
"""
import time
from collections import deque

import cv2
import numpy as np

from . import applog

log = applog.get("detect")

LEVELS = {  # sensitivity -> (minimum share of the frame for the biggest changed blob, x baseline)
    "niska": (0.02, 4.0),
    "srednja": (0.01, 3.0),
    "visoka": (0.005, 2.5),
}
SAMPLE_S = 3
MEDIAN_N = 15        # 15 x 3 s = 45 s
HIST_EVERY_S = 30
WARMUP = 4           # history entries before judging (2 min)
CONFIRM = 4          # checks a change on the job must stay (2 min) before a pause (Mirko 24.11: false pause at 22:42)
CONFIRM_NO_ROI = 4   # without the job area the head can stay in one place for a while: 2 min
PIX_DIFF = 28        # grey level difference that counts as changed
WIDTH = 320
FOREIGN = 3          # x minimum share: size of a new thing beside the job worth an alarm
CONFIRM_OUT = 4      # checks (2 min) a new thing beside the job must stay
COME_BACK = 0.7      # share of the changed place that looked as before in one of the recent frames -> passer-by


class Detector:
    def __init__(self, pid):
        self.id = pid
        self.reset("init")

    def reset(self, why):
        if getattr(self, "max_ratio", 0):
            log.info("%s: job check summary: highest change %.2f of limit (%s)", self.id, self.max_ratio, why)
        self.max_ratio = 0.0
        self.samples = deque(maxlen=MEDIAN_N)
        self.hist = deque(maxlen=20)
        self.hist_t = 0.0
        self.last_sample = 0.0
        self.base = None
        self.over = 0
        self.cand = None
        self.score = 0.0
        self.limit = None
        self.ratio = 0.0
        self.box = None
        self.muted_until = 0.0
        self.ref_small = None
        self.over_out = 0
        self.box_out = None
        self.cand_out = None
        self._ro = None
        self.strict_until = 0.0
        self.muted_out_until = 0.0
        log.debug("%s: reset (%s)", self.id, why)

    def state(self):
        return {"score": round(self.score, 4), "limit": round(self.limit, 4) if self.limit else None,
                "ratio": round(self.ratio, 2), "ready": len(self.hist) > WARMUP, "box": self.box}

    def mute(self, minutes):
        self.muted_until = time.time() + minutes * 60
        self.over = 0
        self.cand = None

    def feed(self, frame, level="srednja", roi=None, grow=None):
        """roi: mask (any size) of where the job is; changes outside it (light on the bed, a hand) do not count."""
        """Returns an alarm dict once when a change is confirmed, else None."""
        now = time.time()
        if frame is None or now - self.last_sample < SAMPLE_S:
            return None
        self.last_sample = now
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (WIDTH, int(h * WIDTH / w)), interpolation=cv2.INTER_AREA)
        g = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        self.samples.append(g)
        if len(self.samples) < MEDIAN_N // 2 or now - self.hist_t < HIST_EVERY_S:
            return None
        self.hist_t = now
        med = np.median(np.stack(self.samples), axis=0).astype(np.uint8)
        self.hist.append(med)
        if len(self.hist) < 3:
            return None
        prev = self.hist[-3]  # 60 s earlier
        if abs(float(med.mean()) - float(prev.mean())) > 25:
            log.info("%s: brightness jump (light/door?), check restarted", self.id)
            last = self.hist[-1]
            self.hist.clear()
            self.hist.append(last)
            self.over = 0
            self.cand = None
            self.cand_out = None
            return None
        prev = _align(prev, med)  # the bed moves down with Z: marks on it must not look like new things
        full = cv2.absdiff(med, prev) > PIX_DIFF
        full = cv2.morphologyEx(full.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        mask, outside, area = full, None, full.size
        if roi is not None:
            r = cv2.resize(roi, (full.shape[1], full.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            if r.sum() > 50:
                mask, outside = full & r, full & ~r
                self._ro = ~r
        g_small = None
        if grow is not None:
            g_small = cv2.resize(grow, (full.shape[1], full.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        mask = mask.astype(np.uint8)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        big = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1 if n > 1 else 0
        self.score = float(stats[big, cv2.CC_STAT_AREA]) / area if big else 0.0
        if len(self.hist) <= WARMUP:
            self.base = self.score if self.base is None else max(self.base, self.score)
            return None
        mn, k = LEVELS.get(level, LEVELS["srednja"])
        mn_out = mn
        self.limit = max(mn, k * (self.base or 0.0))
        if now < self.strict_until:  # something changed beside the job: look closer at the job itself
            self.limit = max(mn, self.limit * 0.5)  # never under the minimum (22:42: 0.005 = one top edge)
        self.ratio = self.score / self.limit if self.limit else 0.0
        self.max_ratio = max(self.max_ratio, self.ratio)
        need = CONFIRM if outside is not None else CONFIRM_NO_ROI
        if self.cand is not None:
            # a change is being followed: is that place still different from how it looked before?
            ref, blob = self.cand
            ref = _align(ref, med)
            still = float((cv2.absdiff(med, ref)[blob] > PIX_DIFF).mean())
            back = _came_back(self.samples, ref, blob)
            if self._growth(blob, g_small):
                log.info("%s: change is where the print grows now, not counted", self.id)
                self.cand, self.over, self.box = None, 0, None
                return None
            if still >= 0.5 and back < COME_BACK:
                self.over += 1
                log.info("%s: change still there (%.0f%%) (%d/%d)", self.id, 100 * still, self.over, need)
                if self.over < need or now < self.muted_until:
                    return None
                self.cand, self.over = None, 0
                self.muted_until = now + 10 * 60  # one alarm, then quiet for 10 min unless reset
                self.ref_small = ref
                return {"kind": "job", "score": self.score, "limit": self.limit, "box": self.box}
            log.info("%s: change gone (%.0f%% still, %.0f%% back), not counted", self.id, 100 * still, 100 * back)
            self.cand, self.over, self.box = None, 0, None
            return None
        if self.score < self.limit:
            if now >= self.strict_until:
                self.base = 0.9 * self.base + 0.1 * self.score if self.base is not None else self.score
            self.over = 0
            self.box = None
            return self._outside(outside, med, prev, mn_out, now, w, h)
        blob = lab == big
        if self._growth(blob, g_small):
            log.info("%s: change %.3f over limit %.3f is where the print grows now (project), not counted",
                     self.id, self.score, self.limit)
            return None
        back = _came_back(self.samples, prev, blob)
        if back >= COME_BACK:
            # in some recent frame that place looked as it did before: something passed over it (the print head)
            log.info("%s: change %.3f over limit %.3f came and went (%.0f%% back), not counted",
                     self.id, self.score, self.limit, 100 * back)
            return None
        ys, xs = np.nonzero(blob)
        sx, sy = w / mask.shape[1], h / mask.shape[0]
        self.box = [int(xs.min() * sx), int(ys.min() * sy), int((xs.max() + 1) * sx), int((ys.max() + 1) * sy)]
        # follow this place: it must stay different from the old picture (the moving head does not)
        self.cand, self.over = (prev, blob), 1
        log.info("%s: change %.3f over limit %.3f (%d/%d)", self.id, self.score, self.limit, self.over, need)
        return None

    def _growth(self, blob, grow):
        """The change lies in the band where the project says the print is growing now (top mm of the objects)
        and is not big: new layers, not a part that came off. Big changes are never explained this way."""
        if grow is None or not blob.any():
            return False
        inside = float(grow[blob].mean())
        return inside >= 0.7 and float(blob.mean()) < 3 * (self.limit or 0.01)

    def _outside(self, outside, med, prev, mn, now, w, h):
        """A change on the bed beside the job: light, shadow, a hand, or something that fell or was put there.
        The job area is then checked more strictly for a few minutes. A new thing that stays on the bed
        beside the job (even pushed around by the head) for 2 min is an alarm; light and shadows are not."""
        if outside is None:
            return None
        if self.cand_out is not None:
            ref = _align(self.cand_out, med)  # how the bed looked before, moved to where the bed is now
            d = (cv2.absdiff(med, ref) > PIX_DIFF) & self._ro
            d = cv2.morphologyEx(d.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            n, lab, stats, _ = cv2.connectedComponentsWithStats(d, connectivity=8)
            big = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1 if n > 1 else 0
            score = float(stats[big, cv2.CC_STAT_AREA]) / d.size if big else 0.0
            back = _came_back(self.samples, ref, lab == big) if big else 1.0
            if score >= FOREIGN * mn and back < COME_BACK:
                self.over_out += 1
                self.strict_until = now + 180
                self.box_out = _box(lab == big, w, h)
                log.info("%s: something beside the job stays %.3f (%d/%d)", self.id, score, self.over_out, CONFIRM_OUT)
                if self.over_out < CONFIRM_OUT or now < self.muted_out_until:
                    return None
                self.cand_out, self.over_out = None, 0
                self.muted_out_until = now + 30 * 60
                return {"kind": "foreign", "score": score, "box": self.box_out}
            log.info("%s: change beside the job gone (%.3f, %.0f%% back)", self.id, score, 100 * back)
            self.cand_out, self.over_out = None, 0
            return None
        n, lab, stats, _ = cv2.connectedComponentsWithStats(outside.astype(np.uint8), connectivity=8)
        if n < 2:
            return None
        big = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1
        score = float(stats[big, cv2.CC_STAT_AREA]) / outside.size
        if score < 2 * mn:
            return None
        self.strict_until = now + 180
        blob = lab == big
        back = _came_back(self.samples, prev, blob)
        log.info("%s: change beside the job %.3f (%.0f%% back), job area checked strictly", self.id, score, 100 * back)
        old = self.hist[-5] if len(self.hist) >= 5 else self.hist[0]
        new_thing = float((cv2.absdiff(med, old)[blob] > PIX_DIFF).mean()) >= 0.5  # not just back to how it was
        if score >= FOREIGN * mn and back < COME_BACK and new_thing:
            self.cand_out, self.over_out, self.box_out = prev, 1, _box(blob, w, h)
        return None


def _box(blob, w, h):
    ys, xs = np.nonzero(blob)
    sx, sy = w / blob.shape[1], h / blob.shape[0]
    return [int(xs.min() * sx), int(ys.min() * sy), int((xs.max() + 1) * sx), int((ys.max() + 1) * sy)]


def _align(src, dst):
    """src moved onto dst the way the bed moved (Z): a homography found on the lower part of the picture
    (the bed; the head and gantry are mostly at the top). Kept only if it really makes them more alike,
    so a failed or odd fit changes nothing. Mirko 26.11: the logo on the bed must not look like a new thing."""
    h, w = dst.shape
    m = np.zeros_like(dst)
    m[int(h * 0.45):, :] = 255
    warp = np.eye(3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(dst.astype(np.float32), src.astype(np.float32), warp, cv2.MOTION_HOMOGRAPHY,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, 1e-4), m, 5)
    except cv2.error:
        return src
    out = cv2.warpPerspective(src, warp, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                              borderMode=cv2.BORDER_REPLICATE)
    sel = m > 0
    before = float(cv2.absdiff(src, dst)[sel].mean())
    after = float(cv2.absdiff(out, dst)[sel].mean())
    return out if after < before else src


def _came_back(samples, prev, blob):
    """Highest share of the changed place that, in one of the recent raw frames, matches the old picture.
    A part that came loose never looks as before again; a place the head only covered for a while does."""
    n = int(blob.sum())
    if not n:
        return 0.0
    best = 0.0
    for g in samples:
        same = cv2.absdiff(g, prev)[blob] <= PIX_DIFF
        best = max(best, float(same.sum()) / n)
    return best


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return ix * iy / small


def mark(frame, box, color=(77, 72, 229)):
    """Copy of the frame with the changed area outlined (for Telegram and the page)."""
    if frame is None:
        return None
    f = frame.copy()
    if box:
        t = max(2, f.shape[1] // 300)
        cv2.rectangle(f, (box[0], box[1]), (box[2], box[3]), color, t)
    return f
