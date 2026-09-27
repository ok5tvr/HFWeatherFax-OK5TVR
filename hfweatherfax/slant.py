from __future__ import annotations

from collections import deque
import numpy as np


class AutoSlantEstimator:
    """Estimate raster slant from horizontal drift between adjacent fax rows.

    Adjacent weatherfax lines normally contain strongly related structures.
    A sample-clock / line-period error makes those structures drift horizontally
    by a nearly constant amount per row.  The estimator measures that drift by
    normalized correlation and converts pixels/line to ppm.
    """

    def __init__(self, width: int, max_shift_px: int = 12):
        self.width = int(width)
        self.max_shift_px = int(max_shift_px)
        self.prev: np.ndarray | None = None
        self.measurements: deque[tuple[float, float]] = deque(maxlen=48)
        self.ppm: float | None = None
        self.confidence = 0.0
        self.valid_pairs = 0

    def reset(self, width: int | None = None):
        if width is not None:
            self.width = int(width)
        self.prev = None
        self.measurements.clear()
        self.ppm = None
        self.confidence = 0.0
        self.valid_pairs = 0

    @property
    def status_text(self) -> str:
        if self.ppm is None:
            return "Auto slant: measuring"
        return f"Auto slant: {self.ppm:+.0f} ppm ({self.confidence:.0%})"

    def push_lines(self, lines: list[np.ndarray]) -> tuple[float | None, float]:
        for line in lines:
            self._push_line(np.asarray(line, dtype=np.float64))
        self._update_estimate()
        return self.ppm, self.confidence

    def _prepare(self, x: np.ndarray) -> np.ndarray | None:
        if x.size < 128:
            return None
        # Ignore margins; they can contain framing artefacts.
        a = int(round(x.size * 0.06))
        b = int(round(x.size * 0.94))
        y = x[a:b].astype(np.float64, copy=True)
        # Horizontal gradient emphasizes map/text edges and removes DC level.
        y = np.diff(y)
        if y.size < 64:
            return None
        # Suppress isolated noise with a small moving average.
        kernel = np.ones(5, dtype=np.float64) / 5.0
        y = np.convolve(y, kernel, mode="same")
        y -= np.mean(y)
        s = float(np.std(y))
        if s < 2.0:
            return None
        y /= s
        return y

    def _push_line(self, line: np.ndarray):
        cur = self._prepare(line)
        if cur is None:
            self.prev = None
            return
        if self.prev is not None and self.prev.size == cur.size:
            shift, quality = self._best_shift(self.prev, cur)
            if shift is not None and quality >= 0.18:
                self.measurements.append((float(shift), float(quality)))
                self.valid_pairs += 1
        self.prev = cur

    def _best_shift(self, prev: np.ndarray, cur: np.ndarray) -> tuple[float | None, float]:
        scores = []
        shifts = range(-self.max_shift_px, self.max_shift_px + 1)
        for s in shifts:
            if s < 0:
                a = prev[-s:]
                b = cur[:cur.size + s]
            elif s > 0:
                a = prev[:prev.size - s]
                b = cur[s:]
            else:
                a = prev
                b = cur
            if a.size < 64:
                scores.append(-1.0)
                continue
            denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b))) + 1e-12
            scores.append(float(np.dot(a, b) / denom))

        arr = np.asarray(scores, dtype=np.float64)
        idx = int(np.argmax(arr))
        peak = float(arr[idx])
        shift = float(list(shifts)[idx])

        # Parabolic interpolation gives sub-pixel drift when possible.
        if 0 < idx < arr.size - 1:
            y1, y2, y3 = arr[idx - 1], arr[idx], arr[idx + 1]
            denom = y1 - 2.0 * y2 + y3
            if abs(denom) > 1e-9:
                frac = 0.5 * (y1 - y3) / denom
                if abs(frac) <= 1.0:
                    shift += float(frac)

        # Quality combines absolute correlation with prominence over alternatives.
        tmp = arr.copy()
        lo = max(0, idx - 1)
        hi = min(arr.size, idx + 2)
        tmp[lo:hi] = -1.0
        second = float(np.max(tmp)) if tmp.size else -1.0
        prominence = max(0.0, peak - second)
        quality = np.clip(0.72 * max(0.0, peak) + 2.8 * prominence, 0.0, 1.0)
        return shift, float(quality)

    def _update_estimate(self):
        if len(self.measurements) < 10:
            self.confidence = min(0.45, len(self.measurements) / 22.0)
            return

        shifts = np.asarray([v for v, _ in self.measurements], dtype=np.float64)
        qualities = np.asarray([q for _, q in self.measurements], dtype=np.float64)

        med = float(np.median(shifts))
        mad = float(np.median(np.abs(shifts - med))) + 1e-6
        keep = np.abs(shifts - med) <= max(0.45, 2.8 * mad)
        if np.count_nonzero(keep) < 8:
            self.confidence = 0.2
            return

        s = shifts[keep]
        q = qualities[keep]
        w = np.maximum(q, 0.05)
        drift_px = float(np.sum(s * w) / np.sum(w))
        scatter = float(np.std(s))
        consistency = float(np.clip(1.0 - scatter / 1.2, 0.0, 1.0))
        count_factor = float(np.clip(len(s) / 28.0, 0.0, 1.0))
        quality_factor = float(np.clip(np.mean(q), 0.0, 1.0))
        conf = 0.45 * consistency + 0.30 * count_factor + 0.25 * quality_factor

        # Positive rightward drift means the decoder's line period is too short,
        # so it needs a positive ppm correction (a slightly longer raster line).
        ppm = drift_px / max(1.0, float(self.width)) * 1_000_000.0
        ppm = float(np.clip(ppm, -5000.0, 5000.0))

        if conf >= 0.48:
            if self.ppm is None:
                self.ppm = ppm
            else:
                # Slow loop: changing line period too aggressively causes visible jitter.
                self.ppm = 0.82 * self.ppm + 0.18 * ppm
        self.confidence = float(np.clip(conf, 0.0, 1.0))
