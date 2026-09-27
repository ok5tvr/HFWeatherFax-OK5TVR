from __future__ import annotations

import numpy as np


def estimate_wrap_shift(lines: list[np.ndarray] | np.ndarray) -> tuple[int | None, float, int | None]:
    """Estimate a circular horizontal shift that moves a likely raster wrap to x=0.

    Returns (shift_pixels_for_np_roll, confidence, seam_column). The algorithm is
    deliberately conservative: a true wrap tends to create a strong vertical
    discontinuity that persists through many consecutive image rows.
    """
    if isinstance(lines, list):
        if len(lines) < 24:
            return None, 0.0, None
        img = np.vstack(lines)
    else:
        img = np.asarray(lines)
    if img.ndim != 2 or img.shape[0] < 24 or img.shape[1] < 200:
        return None, 0.0, None

    # Use a representative block, avoiding a few initial rows that may still
    # contain phasing/image transition artefacts.
    if img.shape[0] > 220:
        img = img[:220]
    if img.shape[0] > 40:
        img = img[8:]
    x = img.astype(np.float32)

    # Light vertical smoothing suppresses isolated HF noise while retaining a
    # seam that persists across rows.
    if x.shape[0] >= 3:
        x = (x[:-2] + 2.0 * x[1:-1] + x[2:]) * 0.25

    # Circular horizontal gradient. Index j denotes the boundary between j and
    # j+1. A raster wrap is usually a persistent, high-gradient boundary.
    nxt = np.roll(x, -1, axis=1)
    grad = np.abs(nxt - x)

    med = np.median(grad, axis=0)
    # Persistence: fraction of rows where this column is among stronger local
    # edges. This downweights a single weather front/text stroke.
    row_thr = np.percentile(grad, 82.0, axis=1, keepdims=True)
    persistence = np.mean(grad >= row_thr, axis=0)

    # Smooth only a few pixels so a narrow wrap remains localized.
    k = max(3, int(round(img.shape[1] * 0.003)))
    if k % 2 == 0:
        k += 1
    kernel = np.ones(k, dtype=np.float64) / k
    med_s = np.convolve(np.r_[med[-k:], med, med[:k]], kernel, mode="same")[k:k + med.size]
    per_s = np.convolve(np.r_[persistence[-k:], persistence, persistence[:k]], kernel, mode="same")[k:k + persistence.size]

    baseline = float(np.median(med_s))
    mad = float(1.4826 * np.median(np.abs(med_s - baseline))) + 1e-6
    z = np.clip((med_s - baseline) / (4.0 * mad), 0.0, 1.5)
    score = z * (0.35 + 0.65 * np.clip(per_s / 0.45, 0.0, 1.0))

    seam = int(np.argmax(score))
    peak = float(score[seam])
    # Contrast against the next-best broad region, not just the immediate bin.
    exclusion = max(12, int(round(img.shape[1] * 0.025)))
    mask = np.ones(score.size, dtype=bool)
    for d in range(-exclusion, exclusion + 1):
        mask[(seam + d) % score.size] = False
    second = float(np.max(score[mask])) if np.any(mask) else 0.0
    separation = float(np.clip((peak - second + 0.10) / 0.70, 0.0, 1.0))
    persist_score = float(np.clip((per_s[seam] - 0.18) / 0.42, 0.0, 1.0))
    strength = float(np.clip((med_s[seam] - baseline) / (8.0 * mad), 0.0, 1.0))
    confidence = float(np.clip(0.42 * strength + 0.38 * persist_score + 0.20 * separation, 0.0, 1.0))

    # The new first column is the column immediately after the detected seam.
    first_col = (seam + 1) % img.shape[1]
    shift = -int(first_col)
    # Prefer the equivalent shortest circular shift for readability/status.
    if shift < -img.shape[1] // 2:
        shift += img.shape[1]
    return shift, confidence, seam
