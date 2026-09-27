from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .dsp import FaxDemodulator


@dataclass
class AutoEvent:
    kind: str
    message: str
    lpm: int | None = None
    confidence: float | None = None
    sync_skip: int | None = None
    line_samples: int | None = None
    phase_percent: float | None = None
    hsync_confidence: float | None = None
    black_hz: float | None = None
    white_hz: float | None = None
    level_confidence: float | None = None


class WefaxAutoDetector:
    """Detects WEFAX APT start/stop tones and phasing line rate.

    This detector is intentionally conservative. It uses a short-tone FFT for
    the 300 Hz START and 450 Hz STOP signals and an independent FM demodulator
    to find the periodic phasing pattern at 60/90/120/240 LPM.
    """

    LPM_CANDIDATES = (60, 90, 120, 240)

    def __init__(self, sample_rate: int, sensitivity: str = "Normal"):
        self.sample_rate = int(sample_rate)
        self.sensitivity = sensitivity if sensitivity in ("Normal", "High", "Weak signal") else "Normal"
        self._profiles = {
            "Normal": {"tone_thr": 0.62, "confirm_s": 1.00, "stop_thr": 0.62, "stop_confirm_s": 1.20, "phase_thr": 0.48, "fallback_thr": 0.67, "spread": 28.0},
            "High": {"tone_thr": 0.54, "confirm_s": 1.10, "stop_thr": 0.54, "stop_confirm_s": 1.35, "phase_thr": 0.42, "fallback_thr": 0.60, "spread": 22.0},
            "Weak signal": {"tone_thr": 0.44, "confirm_s": 1.50, "stop_thr": 0.46, "stop_confirm_s": 1.65, "phase_thr": 0.36, "fallback_thr": 0.54, "spread": 16.0},
        }
        self.profile = self._profiles[self.sensitivity]
        self.state = "WAIT_START"
        self.detected_lpm: int | None = None
        self.last_phasing_confidence = 0.0
        self.locked_phasing_confidence = 0.0
        self.last_hsync_confidence = 0.0
        self.last_tone = ""
        self.tuning_offset_hz = 0.0
        self.stop_target_hz = 450.0
        self.start_target_hz = 300.0

        # APT START/STOP are modulation rates of the WEFAX subcarrier, not
        # standalone low-frequency audio tones.  Detect them after FM demodulation.
        self._apt_rate = 2000.0
        self._apt_block = max(1, int(round(self.sample_rate / self._apt_rate)))
        self._apt_accum = np.empty(0, dtype=np.float32)
        self._apt_series = np.empty(0, dtype=np.float32)
        self._apt_last_processed = 0

        self._tone_frame_s = 0.25
        self._tone_hop_s = 0.125
        self._tone_frame_n = max(512, int(round(self.sample_rate * self._tone_frame_s)))
        self._tone_hop_n = max(256, int(round(self.sample_rate * self._tone_hop_s)))
        self._tone_buffer = np.empty(0, dtype=np.float32)
        self._apt_accum = np.empty(0, dtype=np.float32)
        self._apt_series = np.empty(0, dtype=np.float32)
        self._apt_last_processed = 0
        self._start_seconds = 0.0
        self._stop_seconds = 0.0
        self.last_start_confidence = 0.0
        self.last_stop_confidence = 0.0

        self._demod = FaxDemodulator(self.sample_rate)
        self._demod_accum = np.empty(0, dtype=np.float32)
        self._gray_history = np.empty(0, dtype=np.float32)
        self._freq_history = np.empty(0, dtype=np.float32)
        self._gray_total = 0
        self._phase_rate = 100.0
        self._phase_block = max(1, int(round(self.sample_rate / self._phase_rate)))
        self._phase_series = np.empty(0, dtype=np.float32)
        self._phase_lock_count = 0
        self._phase_only_lock_count = 0

    def set_sensitivity(self, sensitivity: str) -> None:
        if sensitivity not in self._profiles:
            sensitivity = "Normal"
        self.sensitivity = sensitivity
        self.profile = self._profiles[sensitivity]

    def reset(self):
        self.state = "WAIT_START"
        self.detected_lpm = None
        self.last_phasing_confidence = 0.0
        self.locked_phasing_confidence = 0.0
        self.last_hsync_confidence = 0.0
        self.last_tone = ""
        self.tuning_offset_hz = 0.0
        self.stop_target_hz = 450.0
        self.start_target_hz = 300.0

        # APT START/STOP are modulation rates of the WEFAX subcarrier, not
        # standalone low-frequency audio tones.  Detect them after FM demodulation.
        self._apt_rate = 2000.0
        self._apt_block = max(1, int(round(self.sample_rate / self._apt_rate)))
        self._apt_accum = np.empty(0, dtype=np.float32)
        self._apt_series = np.empty(0, dtype=np.float32)
        self._apt_last_processed = 0
        self._tone_buffer = np.empty(0, dtype=np.float32)
        self._apt_accum = np.empty(0, dtype=np.float32)
        self._apt_series = np.empty(0, dtype=np.float32)
        self._apt_last_processed = 0
        self._start_seconds = 0.0
        self._stop_seconds = 0.0
        self.last_start_confidence = 0.0
        self.last_stop_confidence = 0.0
        self._demod.reset()
        self._demod_accum = np.empty(0, dtype=np.float32)
        self._gray_history = np.empty(0, dtype=np.float32)
        self._freq_history = np.empty(0, dtype=np.float32)
        self._gray_total = 0
        self._phase_series = np.empty(0, dtype=np.float32)
        self._phase_lock_count = 0
        self._phase_only_lock_count = 0

    @property
    def status_text(self) -> str:
        if self.state == "WAIT_START":
            if self.last_phasing_confidence >= 0.45:
                return f"AUTO: checking phasing ({self.last_phasing_confidence:.0%})"
            tone = max(self.last_start_confidence, self.last_stop_confidence)
            return f"AUTO: waiting ({self.sensitivity}); tone {tone:.0%}"
        if self.state == "PHASING":
            return f"AUTO: START detected; phasing {self.last_phasing_confidence:.0%} ({self.sensitivity})"
        if self.state == "RECEIVING":
            lpm = self.detected_lpm or "?"
            return f"AUTO: receiving, {lpm} LPM; APT STOP 450 Hz ({self.last_stop_confidence:.0%})"
        return "AUTO"

    def push_audio(self, audio: np.ndarray) -> list[AutoEvent]:
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        if x.size == 0:
            return []

        events: list[AutoEvent] = []
        events.extend(self._process_tones(x))
        events.extend(self._process_phasing(x))
        return events

    def _process_tones(self, x: np.ndarray) -> list[AutoEvent]:
        events: list[AutoEvent] = []
        self._tone_buffer = np.concatenate((self._tone_buffer, x))

        while self._tone_buffer.size >= self._tone_frame_n:
            frame = self._tone_buffer[:self._tone_frame_n]
            self._tone_buffer = self._tone_buffer[self._tone_hop_n:]

            # In SSB reception the whole audio spectrum moves with receiver
            # tuning error. Once phasing has calibrated BLACK/WHITE levels we
            # therefore shift the expected APT START/STOP tones by the same
            # offset. Keep a nominal fallback for a new station or imperfect
            # calibration.
            # START/STOP are deliberately NOT detected from raw audio. In
            # WEFAX they are 300/450 Hz square-wave modulation rates of the FM
            # subcarrier, detected after demodulation in _process_apt_modulation.
            start_conf = 0.0

            # STOP is deliberately NOT detected from raw audio.  In WEFAX it
            # is a 450 Hz square-wave modulation of the FM subcarrier.  The
            # correct detector runs on the demodulated signal below.
            stop_conf = 0.0

            dt = self._tone_hop_n / self.sample_rate
            self.last_start_confidence = start_conf
            tone_thr = float(self.profile["tone_thr"])
            stop_thr = float(self.profile["stop_thr"])


        return events

    def _tone_confidence(self, frame: np.ndarray, target_hz: float) -> float:
        """Adaptive narrow-band tone score robust to weak signals and noise.

        The score combines narrow-band SNR, spectral dominance and peak
        frequency accuracy. Noise is estimated from the surrounding 100-900 Hz
        band while excluding a guard band around the target.
        """
        x = np.asarray(frame, dtype=np.float64)
        rms = float(np.sqrt(np.mean(x * x) + 1e-15))
        if rms < 5e-6:
            return 0.0
        x = x - np.mean(x)
        x *= np.hanning(x.size)
        spec = np.fft.rfft(x)
        power = np.abs(spec) ** 2 + 1e-30
        freqs = np.fft.rfftfreq(x.size, 1.0 / self.sample_rate)

        target = (freqs >= target_hz - 10.0) & (freqs <= target_hz + 10.0)
        ref_low = max(50.0, target_hz - 500.0)
        ref_high = min(1450.0, target_hz + 500.0)
        reference = (freqs >= ref_low) & (freqs <= ref_high)
        guard = (freqs >= target_hz - 35.0) & (freqs <= target_hz + 35.0)
        noise_mask = reference & (~guard)
        if not np.any(target) or np.count_nonzero(noise_mask) < 8:
            return 0.0

        target_bins = power[target]
        target_peak = float(np.max(target_bins))
        target_power = float(np.sum(target_bins))
        reference_power = float(np.sum(power[reference])) + 1e-30
        concentration = target_power / reference_power

        noise_floor = float(np.median(power[noise_mask])) + 1e-30
        snr_db = 10.0 * np.log10(target_peak / noise_floor)

        # Dominance against the strongest competing LF line outside the guard.
        competitor = float(np.max(power[noise_mask])) + 1e-30
        dom_db = 10.0 * np.log10(target_peak / competitor)

        idx = int(np.argmax(power[target]))
        tf = freqs[target]
        peak_hz = float(tf[idx])
        peak_match = float(np.clip(1.0 - abs(peak_hz - target_hz) / 18.0, 0.0, 1.0))

        # A true APT tone concentrates energy into a very narrow band. This
        # extra term is what prevents random noise peaks from looking like a
        # weak START/STOP tone at high sensitivity.
        concentration_score = float(np.clip((concentration - 0.025) / 0.12, 0.0, 1.0))
        snr_score = float(np.clip((snr_db + 2.0) / 18.0, 0.0, 1.0))
        dom_score = float(np.clip((dom_db + 3.0) / 15.0, 0.0, 1.0))
        score = (
            0.45 * concentration_score
            + 0.30 * snr_score
            + 0.15 * dom_score
            + 0.10 * peak_match
        )
        return float(np.clip(score, 0.0, 1.0))

    def _process_phasing(self, x: np.ndarray) -> list[AutoEvent]:
        events: list[AutoEvent] = []

        # Low-frequency START/STOP tones are outside the fax FM passband; the
        # demodulator naturally ignores them. We can therefore keep feeding it.
        freq = self._demod.demodulate_frequency(x)
        if freq.size == 0:
            return events
        gray = self._demod.frequency_to_gray(freq)

        # Correct APT detector: START/STOP are square-wave modulation rates of
        # the demodulated WEFAX subcarrier.  A small decimated stream is enough
        # to identify 300/450 Hz robustly and is independent of SSB tuning.
        events.extend(self._process_apt_modulation(gray))

        self._demod_accum = np.concatenate((self._demod_accum, gray))
        self._gray_total += int(gray.size)
        self._gray_history = np.concatenate((self._gray_history, gray))
        self._freq_history = np.concatenate((self._freq_history, freq))
        max_gray = int(round(self.sample_rate * 8.0))
        if self._gray_history.size > max_gray:
            self._gray_history = self._gray_history[-max_gray:]
        if self._freq_history.size > max_gray:
            self._freq_history = self._freq_history[-max_gray:]
        n = self._phase_block
        take = (self._demod_accum.size // n) * n
        if take:
            blocks = self._demod_accum[:take].reshape(-1, n)
            # Median is robust to FM edge spikes while preserving black phasing pulses.
            reduced = np.median(blocks, axis=1).astype(np.float32)
            self._demod_accum = self._demod_accum[take:]
            self._phase_series = np.concatenate((self._phase_series, reduced))

        max_len = int(round(self._phase_rate * 8.0))
        if self._phase_series.size > max_len:
            self._phase_series = self._phase_series[-max_len:]

        if self._phase_series.size < int(self._phase_rate * 3.5):
            return events

        lpm, conf = self._estimate_lpm(self._phase_series)
        self.last_phasing_confidence = conf

        if self.state == "PHASING":
            if lpm is not None and conf >= float(self.profile["phase_thr"]):
                self._phase_lock_count += 1
            else:
                self._phase_lock_count = max(0, self._phase_lock_count - 1)

            if self._phase_lock_count >= 2 and lpm is not None:
                self.detected_lpm = lpm
                self.state = "RECEIVING"
                self._phase_lock_count = 0
                sync_skip, line_n, phase_pct, hsync_conf = self._estimate_horizontal_sync(lpm)
                black_hz, white_hz, level_conf = self._estimate_fax_levels()
                if black_hz is not None and white_hz is not None:
                    midpoint = 0.5 * (float(black_hz) + float(white_hz))
                    self.tuning_offset_hz = float(np.clip(midpoint - 1900.0, -650.0, 650.0))
                self.locked_phasing_confidence = conf
                self.last_hsync_confidence = hsync_conf or 0.0
                events.append(AutoEvent(
                    "LOCK", f"Phasing locked: {lpm} LPM", lpm=lpm, confidence=conf,
                    sync_skip=sync_skip, line_samples=line_n, phase_percent=phase_pct,
                    hsync_confidence=hsync_conf, black_hz=black_hz, white_hz=white_hz,
                    level_confidence=level_conf,
                ))

        elif self.state == "WAIT_START":
            # Fallback: allow lock if the program was started after the 300 Hz tone.
            if lpm is not None and conf >= float(self.profile["fallback_thr"]):
                self._phase_only_lock_count += 1
            else:
                self._phase_only_lock_count = max(0, self._phase_only_lock_count - 1)

            if self._phase_only_lock_count >= 3 and lpm is not None:
                self.detected_lpm = lpm
                self.state = "RECEIVING"
                self._phase_only_lock_count = 0
                sync_skip, line_n, phase_pct, hsync_conf = self._estimate_horizontal_sync(lpm)
                black_hz, white_hz, level_conf = self._estimate_fax_levels()
                if black_hz is not None and white_hz is not None:
                    midpoint = 0.5 * (float(black_hz) + float(white_hz))
                    self.tuning_offset_hz = float(np.clip(midpoint - 1900.0, -650.0, 650.0))
                self.locked_phasing_confidence = conf
                self.last_hsync_confidence = hsync_conf or 0.0
                events.append(AutoEvent(
                    "LOCK", f"Phasing-only lock: {lpm} LPM", lpm=lpm, confidence=conf,
                    sync_skip=sync_skip, line_samples=line_n, phase_percent=phase_pct,
                    hsync_confidence=hsync_conf, black_hz=black_hz, white_hz=white_hz,
                    level_confidence=level_conf,
                ))

        return events

    def _estimate_fax_levels(self) -> tuple[float | None, float | None, float]:
        """Estimate received BLACK/WHITE subcarrier frequencies from phasing.

        This compensates receiver tuning offset and audio-chain frequency shift.
        A histogram is used because phasing can contain very unequal amounts of
        black and white; simple mean/centroid methods are biased by image content.
        """
        if self._freq_history.size < int(self.sample_rate * 1.5):
            return None, None, 0.0
        f = self._freq_history.astype(np.float64, copy=False)
        f = f[(f >= 1050.0) & (f <= 2850.0)]
        if f.size < 2000:
            return None, None, 0.0

        edges = np.arange(1050.0, 2855.0, 5.0)
        hist, edges = np.histogram(f, bins=edges)
        kernel = np.array([1, 2, 3, 4, 3, 2, 1], dtype=np.float64)
        smooth = np.convolve(hist.astype(np.float64), kernel / kernel.sum(), mode="same")
        centers = 0.5 * (edges[:-1] + edges[1:])

        first = int(np.argmax(smooth))
        sep = np.abs(centers - centers[first])
        candidates = np.where((sep >= 560.0) & (sep <= 1040.0))[0]
        if candidates.size:
            second = int(candidates[np.argmax(smooth[candidates])])
            a, b = sorted((float(centers[first]), float(centers[second])))
            span = b - a
            peak_ratio = min(float(smooth[first]), float(smooth[second])) / max(1.0, max(float(smooth[first]), float(smooth[second])))
            span_score = float(np.exp(-((span - 800.0) / 180.0) ** 2))
            conf = float(np.clip(0.68 * span_score + 0.32 * min(1.0, peak_ratio * 3.0), 0.0, 1.0))
            if 520.0 <= span <= 1080.0 and conf >= 0.42:
                return a, b, conf

        # Fallback for very weak/minority phasing pulses.
        lo = float(np.percentile(f, 2.0))
        hi = float(np.percentile(f, 98.0))
        span = hi - lo
        if 560.0 <= span <= 1050.0:
            conf = float(np.clip(0.45 * np.exp(-((span - 800.0) / 220.0) ** 2), 0.0, 0.55))
            return lo, hi, conf
        return None, None, 0.0

    def _process_apt_modulation(self, gray: np.ndarray) -> list[AutoEvent]:
        events: list[AutoEvent] = []
        g = np.asarray(gray, dtype=np.float32).reshape(-1)
        if g.size == 0:
            return events

        self._apt_accum = np.concatenate((self._apt_accum, g))
        n = self._apt_block
        take = (self._apt_accum.size // n) * n
        if take:
            blocks = self._apt_accum[:take].reshape(-1, n)
            # Median suppresses isolated demodulation spikes while preserving
            # the black/white APT square wave.
            reduced = np.median(blocks, axis=1).astype(np.float32)
            self._apt_accum = self._apt_accum[take:]
            self._apt_series = np.concatenate((self._apt_series, reduced))

        max_len = int(round(self._apt_rate * 4.0))
        if self._apt_series.size > max_len:
            self._apt_series = self._apt_series[-max_len:]

        # Evaluate on about 0.75 s; refresh often, but integrate confidence in
        # time before declaring STOP. This is much harder to false-trigger on
        # chart texture than a single FFT window.
        frame_n = int(round(self._apt_rate * 0.75))
        if self._apt_series.size < frame_n:
            return events

        frame = self._apt_series[-frame_n:]
        start_conf = self._apt_modulation_confidence(frame, 300.0)
        stop_conf = self._apt_modulation_confidence(frame, 450.0)
        self.last_start_confidence = start_conf
        self.last_stop_confidence = stop_conf

        # Estimate elapsed new demodulated time since the previous evaluation.
        # push_audio cadence can vary (live vs WAV), so cap each update.
        dt = min(0.35, max(0.04, g.size / float(self.sample_rate)))

        if self.state == "WAIT_START":
            thr = float(self.profile["tone_thr"])
            if start_conf >= thr:
                self._start_seconds += dt
                self.last_tone = f"APT START {start_conf:.0%}"
            else:
                self._start_seconds = max(0.0, self._start_seconds - 1.8 * dt)

            if self._start_seconds >= float(self.profile["confirm_s"]):
                self.state = "PHASING"
                self.detected_lpm = None
                self.locked_phasing_confidence = 0.0
                self.last_hsync_confidence = 0.0
                self._phase_series = np.empty(0, dtype=np.float32)
                self._phase_lock_count = 0
                self._phase_only_lock_count = 0
                self._demod_accum = np.empty(0, dtype=np.float32)
                self._gray_history = np.empty(0, dtype=np.float32)
                self._freq_history = np.empty(0, dtype=np.float32)
                self._gray_total = 0
                self._start_seconds = 0.0
                events.append(AutoEvent(
                    "START", "APT START 300 Hz modulation detected",
                    confidence=start_conf,
                ))

        if self.state == "RECEIVING":
            thr = float(self.profile["stop_thr"])
            if stop_conf >= thr:
                self._stop_seconds += dt
                self.last_tone = f"APT STOP {stop_conf:.0%}"
            else:
                # Leaky integration tolerates brief fades without letting a
                # historical near-match accumulate forever.
                self._stop_seconds = max(0.0, self._stop_seconds - 1.8 * dt)

            if self._stop_seconds >= float(self.profile["stop_confirm_s"]):
                old_lpm = self.detected_lpm
                self.state = "WAIT_START"
                self.detected_lpm = None
                self.last_phasing_confidence = 0.0
                self.locked_phasing_confidence = 0.0
                self.last_hsync_confidence = 0.0
                self._stop_seconds = 0.0
                self._phase_series = np.empty(0, dtype=np.float32)
                self._phase_lock_count = 0
                self._phase_only_lock_count = 0
                self._demod.reset()
                self._demod_accum = np.empty(0, dtype=np.float32)
                self._gray_history = np.empty(0, dtype=np.float32)
                self._freq_history = np.empty(0, dtype=np.float32)
                self._gray_total = 0
                self._apt_accum = np.empty(0, dtype=np.float32)
                self._apt_series = np.empty(0, dtype=np.float32)
                events.append(AutoEvent(
                    "STOP", "APT STOP 450 Hz modulation detected",
                    lpm=old_lpm, confidence=stop_conf,
                ))
        else:
            self._stop_seconds = 0.0

        return events

    def _apt_modulation_confidence(self, frame: np.ndarray, target_hz: float) -> float:
        x = np.asarray(frame, dtype=np.float64)
        if x.size < 256:
            return 0.0

        # APT square wave should span a substantial fraction of black-to-white.
        p5, p95 = np.percentile(x, [5, 95])
        spread = float(p95 - p5)
        spread_score = float(np.clip((spread - 45.0) / 120.0, 0.0, 1.0))
        if spread_score <= 0.0:
            return 0.0

        x = x - np.mean(x)
        x *= np.hanning(x.size)
        spec = np.abs(np.fft.rfft(x)) ** 2 + 1e-20
        f = np.fft.rfftfreq(x.size, 1.0 / self._apt_rate)

        target = (f >= target_hz - 8.0) & (f <= target_hz + 8.0)
        guard = (f >= target_hz - 25.0) & (f <= target_hz + 25.0)
        ref = (f >= 120.0) & (f <= 900.0) & (~guard)
        if not np.any(target) or np.count_nonzero(ref) < 20:
            return 0.0

        tp = float(np.max(spec[target]))
        noise = float(np.median(spec[ref])) + 1e-20
        competitor = float(np.max(spec[ref])) + 1e-20
        snr_db = 10.0 * np.log10(tp / noise)
        dom_db = 10.0 * np.log10(tp / competitor)

        # Periodicity check by autocorrelation at 450 Hz. This rejects image
        # detail that happens to contain a spectral peak near 450 Hz.
        lag = max(2, int(round(self._apt_rate / target_hz)))
        y = x / (np.std(x) + 1e-12)
        if lag >= y.size // 3:
            corr = 0.0
        else:
            a, b = y[:-lag], y[lag:]
            corr = float(np.dot(a, b) / (np.sqrt(np.dot(a, a) * np.dot(b, b)) + 1e-12))
        corr_score = float(np.clip((corr - 0.20) / 0.65, 0.0, 1.0))

        snr_score = float(np.clip((snr_db - 3.0) / 24.0, 0.0, 1.0))
        dom_score = float(np.clip((dom_db + 1.0) / 14.0, 0.0, 1.0))
        return float(np.clip(
            0.34 * snr_score + 0.24 * dom_score + 0.27 * corr_score + 0.15 * spread_score,
            0.0, 1.0,
        ))

    def _estimate_horizontal_sync(self, lpm: int) -> tuple[int | None, int | None, float | None, float | None]:
        """Estimate line start and an independent H-sync confidence.

        Returns (samples_to_skip, line_samples, phase_percent, hsync_confidence).
        H-sync confidence combines pulse-position consistency across phasing
        lines with pulse contrast, so it is intentionally independent from the
        LPM/phasing lock confidence.
        """
        line_n = max(32, int(round(self.sample_rate * 60.0 / float(lpm))))
        if self._gray_history.size < line_n * 3:
            return None, line_n, None, 0.0

        n_lines = min(12, self._gray_history.size // line_n)
        if n_lines < 3:
            return None, line_n, None, 0.0

        tail = self._gray_history[-(n_lines * line_n):].astype(np.float64, copy=False)
        rows = tail.reshape(n_lines, line_n)

        win = max(7, int(round(line_n * 0.01)))
        if win % 2 == 0:
            win += 1
        kernel = np.ones(win, dtype=np.float64) / win

        # Estimate the black phasing pulse position independently in each line.
        pulse_positions = []
        row_contrasts = []
        for row in rows:
            smooth_row = np.convolve(row, kernel, mode="same")
            hi = float(np.percentile(smooth_row, 75))
            lo = float(np.min(smooth_row))
            row_contrasts.append(max(0.0, hi - lo))

            # Use the leading edge of the darkest contiguous run rather than
            # argmin; argmin wanders inside a flat black phasing pulse.
            thr = lo + 0.35 * max(1.0, hi - lo)
            dark = smooth_row <= thr
            doubled = np.concatenate((dark, dark))
            best_start = 0
            best_len = 0
            run_start = None
            for j, val in enumerate(doubled):
                if val and run_start is None:
                    run_start = j
                elif (not val) and run_start is not None:
                    run_len = j - run_start
                    if run_len > best_len and run_start < line_n:
                        best_start = run_start
                        best_len = min(run_len, line_n)
                    run_start = None
            if run_start is not None:
                run_len = len(doubled) - run_start
                if run_len > best_len and run_start < line_n:
                    best_start = run_start
            pulse_positions.append(int(best_start % line_n))

        angles = 2.0 * np.pi * np.asarray(pulse_positions, dtype=np.float64) / float(line_n)
        vector = np.mean(np.exp(1j * angles))

        # Convert circular phase scatter back to samples. H-sync needs much
        # tighter repeatability than LPM detection, so even a few tenths of a
        # percent of a line should lower the quality indicator noticeably.
        mean_angle = float(np.angle(vector))
        phase_delta = np.angle(np.exp(1j * (angles - mean_angle)))
        sample_delta = phase_delta * float(line_n) / (2.0 * np.pi)
        scatter = float(1.4826 * np.median(np.abs(sample_delta - np.median(sample_delta))))
        tolerance = max(20.0, 0.006 * float(line_n))
        stability = float(np.exp(-((scatter / tolerance) ** 2)))

        contrast = float(np.clip(np.median(row_contrasts) / 90.0, 0.0, 1.0))
        hsync_conf = float(np.clip(0.78 * stability + 0.22 * contrast, 0.0, 1.0))

        # Circular mean gives a stable phase even when the pulse is near line wrap.
        if mean_angle < 0.0:
            mean_angle += 2.0 * np.pi
        pulse_center_seed = int(round(mean_angle * line_n / (2.0 * np.pi))) % line_n

        profile = np.median(rows, axis=0)
        smooth = np.convolve(profile, kernel, mode="same")

        # Refine minimum locally around the circular-mean estimate, avoiding a
        # spurious isolated minimum elsewhere in the line.
        radius = max(win * 3, int(round(line_n * 0.05)))
        offsets = np.arange(-radius, radius + 1)
        idxs = (pulse_center_seed + offsets) % line_n
        pulse_center = int(idxs[int(np.argmin(smooth[idxs]))])

        base = float(np.median(smooth))
        low = float(np.min(smooth[idxs]))
        threshold = low + 0.33 * max(1.0, base - low)

        pulse_start = pulse_center
        for _ in range(line_n):
            prev = (pulse_start - 1) % line_n
            if smooth[prev] > threshold:
                break
            pulse_start = prev

        next_mod = self._gray_total % line_n
        skip = (pulse_start - next_mod) % line_n
        phase_pct = 100.0 * pulse_start / float(line_n)
        return int(skip), int(line_n), float(phase_pct), hsync_conf

    def _estimate_lpm(self, series: np.ndarray) -> tuple[int | None, float]:
        x = np.asarray(series, dtype=np.float64)
        if x.size < 200:
            return None, 0.0

        # Phasing needs visible black/white modulation.
        spread = float(np.percentile(x, 98) - np.percentile(x, 2))
        if spread < float(self.profile["spread"]):
            return None, 0.0

        x = x - np.mean(x)
        std = float(np.std(x))
        if std < 1e-6:
            return None, 0.0
        x /= std

        # Normalized autocorrelation for the candidate line periods.
        scores: list[tuple[int, float]] = []
        for lpm in self.LPM_CANDIDATES:
            f = lpm / 60.0
            lag = int(round(self._phase_rate / f))
            if lag < 2 or lag >= x.size // 2:
                continue
            a = x[:-lag]
            b = x[lag:]
            denom = float(np.sqrt(np.dot(a, a) * np.dot(b, b))) + 1e-12
            corr = float(np.dot(a, b) / denom)

            # Compare against nearby off-period lags to measure prominence.
            neighbors = []
            for frac in (0.82, 0.90, 1.10, 1.18):
                nl = max(2, int(round(lag * frac)))
                if nl >= x.size // 2:
                    continue
                aa = x[:-nl]
                bb = x[nl:]
                dd = float(np.sqrt(np.dot(aa, aa) * np.dot(bb, bb))) + 1e-12
                neighbors.append(float(np.dot(aa, bb) / dd))
            baseline = max(neighbors) if neighbors else 0.0
            prominence = corr - baseline
            score = 0.72 * max(0.0, corr) + 0.28 * max(0.0, prominence * 2.0)
            scores.append((lpm, score))

        if not scores:
            return None, 0.0

        # Harmonic ambiguity: prefer the highest LPM among scores within 0.04 of
        # the best. A true 240-LPM phasing train also correlates at 120/60 LPM.
        best_score = max(s for _, s in scores)
        near = [item for item in scores if item[1] >= best_score - 0.04]
        best_lpm, score = max(near, key=lambda t: t[0])
        return best_lpm, float(np.clip(score, 0.0, 1.0))
