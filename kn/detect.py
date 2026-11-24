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
CONFIRM = 2          # consecutive over-threshold checks
CONFIRM_NO_ROI = 4   # without the job area the head can stay in one place for a while: 2 min
PIX_DIFF = 28        # grey level difference that counts as changed
WIDTH = 320
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

    def feed(self, frame, level="srednja", roi=None):
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
            return None
        full = cv2.absdiff(med, prev) > PIX_DIFF
        full = cv2.morphologyEx(full.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)) > 0
        mask, outside, area = full, None, full.size
        if roi is not None:
            r = cv2.resize(roi, (full.shape[1], full.shape[0]), interpolation=cv2.INTER_NEAREST) > 0
            if r.sum() > 50:
                mask, outside = full & r, full & ~r
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
            self.limit *= 0.5
        self.ratio = self.score / self.limit if self.limit else 0.0
        self.max_ratio = max(self.max_ratio, self.ratio)
        need = CONFIRM if outside is not None else CONFIRM_NO_ROI
        if self.cand is not None:
            # a change is being followed: is that place still different from how it looked before?
            ref, blob = self.cand
            still = float((cv2.absdiff(med, ref)[blob] > PIX_DIFF).mean())
            back = _came_back(self.samples, ref, blob)
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
            return self._outside(outside, mn_out, now, w, h)
        blob = lab == big
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

    def _outside(self, outside, mn, now, w, h):
        """A change on the bed beside the job (light, shadow, a hand, or something that fell there).
        The job area is then checked more strictly for a few minutes; if the job is intact, one notice."""
        if outside is None:
            return None
        n, lab, stats, _ = cv2.connectedComponentsWithStats(outside.astype(np.uint8), connectivity=8)
        if n < 2:
            self.over_out = 0
            return None
        big = int(stats[1:, cv2.CC_STAT_AREA].argmax()) + 1
        score = float(stats[big, cv2.CC_STAT_AREA]) / outside.size
        if score < 2 * mn:
            self.over_out = 0
            return None
        ys, xs = np.nonzero(lab == big)
        sx, sy = w / outside.shape[1], h / outside.shape[0]
        box = [int(xs.min() * sx), int(ys.min() * sy), int((xs.max() + 1) * sx), int((ys.max() + 1) * sy)]
        self.over_out = self.over_out + 1 if self.over_out and self.box_out and _overlap(self.box_out, box) > 0.3 else 1
        self.box_out = box
        self.strict_until = now + 180
        log.info("%s: change beside the job %.3f (%d/%d), job area checked strictly", self.id, score, self.over_out, CONFIRM)
        if self.over_out < CONFIRM or now < self.muted_out_until:
            return None
        self.over_out = 0
        self.muted_out_until = now + 30 * 60
        return {"kind": "outside", "score": score, "box": box}


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
