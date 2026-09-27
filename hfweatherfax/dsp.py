from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfilt, resample


class FaxDemodulator:
    """Streaming FM subcarrier demodulator for analogue HF radiofax audio.

    Uses complex quadrature mixing and a stateful low-pass filter rather than
    running a Hilbert transform independently on each audio chunk. This avoids
    periodic edge artefacts at chunk boundaries that can appear as horizontal
    bands in the decoded fax image.
    """

    def __init__(
        self,
        sample_rate: int,
        black_hz: float = 1500.0,
        white_hz: float = 2300.0,
        band_low: float = 1000.0,
        band_high: float = 2800.0,
        center_hz: float = 1900.0,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.black_hz = float(black_hz)
        self.white_hz = float(white_hz)
        self.center_hz = float(center_hz)

        nyq = self.sample_rate * 0.5
        lo = max(50.0, band_low) / nyq
        hi = min(nyq - 50.0, band_high) / nyq
        self.sos = butter(4, [lo, hi], btype="bandpass", output="sos")

        # After mixing 1900 Hz to DC, the WEFAX deviation occupies roughly
        # +/-400 Hz. A 1.2 kHz low-pass keeps image detail while strongly
        # rejecting the real-signal image around twice the carrier frequency.
        lp_hz = min(1200.0, nyq * 0.35)
        self.bb_sos = butter(4, lp_hz / nyq, btype="lowpass", output="sos")

        self._zi = None
        self._bb_zi = None
        self._osc_phase = 0.0
        self._last_complex = None

    def reset(self) -> None:
        self._zi = None
        self._bb_zi = None
        self._osc_phase = 0.0
        self._last_complex = None

    def _bandpass(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        if self._zi is None:
            self._zi = np.zeros((self.sos.shape[0], 2), dtype=np.float64)
        y, self._zi = sosfilt(self.sos, x, zi=self._zi)
        return y

    def _baseband(self, y: np.ndarray) -> np.ndarray:
        n = np.arange(y.size, dtype=np.float64)
        omega = 2.0 * np.pi * self.center_hz / self.sample_rate
        phase = self._osc_phase + omega * n
        osc = np.exp(-1j * phase)
        self._osc_phase = float((self._osc_phase + omega * y.size) % (2.0 * np.pi))

        mixed = 2.0 * y * osc
        if self._bb_zi is None:
            self._bb_zi = np.zeros((self.bb_sos.shape[0], 2), dtype=np.complex128)
        z, self._bb_zi = sosfilt(self.bb_sos, mixed, zi=self._bb_zi)
        return z

    def set_levels(self, black_hz: float, white_hz: float) -> None:
        black_hz = float(black_hz)
        white_hz = float(white_hz)
        if white_hz - black_hz < 300.0:
            return
        self.black_hz = black_hz
        self.white_hz = white_hz

    def demodulate_frequency(self, audio: np.ndarray) -> np.ndarray:
        """Return instantaneous WEFAX subcarrier frequency in Hz.

        The demodulator is fully stateful across calls, so arbitrary sound-card
        block sizes do not introduce periodic raster artefacts.
        """
        if audio is None or len(audio) < 4:
            return np.empty(0, dtype=np.float32)

        y = self._bandpass(audio)
        z = self._baseband(y)
        if z.size == 0:
            return np.empty(0, dtype=np.float32)

        if self._last_complex is not None:
            z2 = np.concatenate(([self._last_complex], z))
        else:
            z2 = z

        if z2.size < 2:
            self._last_complex = z[-1]
            return np.empty(0, dtype=np.float32)

        # Phase difference of consecutive complex baseband samples directly
        # yields the instantaneous frequency offset from center_hz.
        dphi = np.angle(z2[1:] * np.conj(z2[:-1]))
        self._last_complex = z[-1]
        freq = self.center_hz + dphi * self.sample_rate / (2.0 * np.pi)

        # Reject only impossible outliers; keep the full WEFAX deviation.
        return np.clip(freq, 700.0, 3200.0).astype(np.float32)

    def frequency_to_gray(self, freq: np.ndarray) -> np.ndarray:
        span = max(1.0, self.white_hz - self.black_hz)
        gray = (np.asarray(freq, dtype=np.float32) - self.black_hz) * 255.0 / span
        return np.clip(gray, 0.0, 255.0).astype(np.float32)

    def demodulate(self, audio: np.ndarray) -> np.ndarray:
        """Return greyscale samples (0..255) from audio samples."""
        return self.frequency_to_gray(self.demodulate_frequency(audio))


def pixels_per_line(ioc: int) -> int:
    # Practical display width. IOC 576 radiofax is commonly represented at
    # roughly 1800 pixels/line; IOC 288 is half that.
    return 1810 if int(ioc) == 576 else 905


def samples_per_line(sample_rate: int, lpm: int) -> float:
    return float(sample_rate) * 60.0 / float(lpm)


def line_from_gray(gray_line: np.ndarray, width: int) -> np.ndarray:
    if gray_line.size < 2:
        return np.zeros(width, dtype=np.uint8)
    out = resample(gray_line.astype(np.float64), width)
    return np.clip(out, 0, 255).astype(np.uint8)
