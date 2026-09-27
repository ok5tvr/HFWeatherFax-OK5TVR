from __future__ import annotations

import numpy as np
from .dsp import FaxDemodulator, line_from_gray, pixels_per_line, samples_per_line


class FaxImageDecoder:
    """Turns audio chunks into raster lines."""

    def __init__(self, sample_rate: int, lpm: int = 120, ioc: int = 576):
        self.sample_rate = int(sample_rate)
        self.lpm = int(lpm)
        self.ioc = int(ioc)
        self.width = pixels_per_line(self.ioc)
        self.demod = FaxDemodulator(self.sample_rate)
        self.buffer = np.empty(0, dtype=np.float32)
        self.phase_offset = 0.0
        self.slant_ppm = 0.0
        self.pending_sync_skip = 0
        self.line_index = 0

    def reset(self) -> None:
        self.demod.reset()
        self.buffer = np.empty(0, dtype=np.float32)
        self.pending_sync_skip = 0
        self.line_index = 0

    def configure(self, lpm: int | None = None, ioc: int | None = None) -> None:
        if lpm is not None:
            self.lpm = int(lpm)
        if ioc is not None:
            self.ioc = int(ioc)
            self.width = pixels_per_line(self.ioc)

    def set_phase_percent(self, pct: float) -> None:
        self.phase_offset = float(pct) / 100.0

    def set_slant_ppm(self, ppm: float) -> None:
        self.slant_ppm = float(ppm)

    def set_levels(self, black_hz: float, white_hz: float) -> None:
        self.demod.set_levels(black_hz, white_hz)

    def set_initial_sync(self, skip_samples: int | None) -> None:
        self.pending_sync_skip = max(0, int(skip_samples or 0))

    def push_audio(self, audio: np.ndarray) -> list[np.ndarray]:
        gray = self.demod.demodulate(audio)
        if gray.size == 0:
            return []
        self.buffer = np.concatenate((self.buffer, gray))
        lines: list[np.ndarray] = []

        if self.pending_sync_skip > 0:
            drop = min(self.pending_sync_skip, int(self.buffer.size))
            self.buffer = self.buffer[drop:]
            self.pending_sync_skip -= drop
            if self.pending_sync_skip > 0:
                return []

        nominal = samples_per_line(self.sample_rate, self.lpm)
        # A ppm correction changes the effective line period slightly.
        corrected = nominal * (1.0 + self.slant_ppm * 1e-6)
        line_n = max(32, int(round(corrected)))

        while self.buffer.size >= line_n:
            raw = self.buffer[:line_n]
            self.buffer = self.buffer[line_n:]

            shift = int(round(self.phase_offset * raw.size)) % raw.size
            if shift:
                raw = np.roll(raw, -shift)

            line = line_from_gray(raw, self.width)
            lines.append(line)
            self.line_index += 1
        return lines
