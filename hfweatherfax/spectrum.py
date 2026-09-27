from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class SpectrumWidget(QWidget):
    """Live FFT spectrum with a truly scrolling waterfall for HF WeatherFax tuning."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # The widget is intentionally flexible: on a small notebook it can
        # shrink to a compact diagnostic strip, while a larger window gives
        # the spectrum/waterfall more room.
        self.setMinimumHeight(170)
        self.sample_rate = 48000
        self.freqs = np.empty(0, dtype=np.float32)
        self.db = np.empty(0, dtype=np.float32)
        self.peak_hz = None
        self.centroid_hz = None
        self.tune_text = "No signal"
        self.tune_state = "no_signal"
        self.tune_offset_hz = 0.0
        self._smoothed = None

        self.f_min = 700.0
        self.f_max = 3000.0
        self.db_min = -90.0
        self.db_max = -10.0

        self.waterfall_rows = 150
        self.waterfall_cols = 640
        self.waterfall = np.zeros((self.waterfall_rows, self.waterfall_cols), dtype=np.uint8)
        # Event timeline aligned 1:1 with waterfall rows. Codes: 0 none, 1 START, 2 PHASING, 3 IMAGE, 4 STOP.
        self.event_rows = np.zeros(self.waterfall_rows, dtype=np.uint8)

        # Waterfall analysis parameters. Around 25 rows/s gives visibly smooth scrolling.
        self._wf_fft = 2048
        self._wf_hop = 1920
        self._wf_buffer = np.empty(0, dtype=np.float32)
        self._wf_window = np.hanning(self._wf_fft).astype(np.float32)

    def reset(self):
        self.freqs = np.empty(0, dtype=np.float32)
        self.db = np.empty(0, dtype=np.float32)
        self.peak_hz = None
        self.centroid_hz = None
        self.tune_text = "No signal"
        self.tune_state = "no_signal"
        self.tune_offset_hz = 0.0
        self._smoothed = None
        self.waterfall[:] = 0
        self.event_rows[:] = 0
        self._wf_buffer = np.empty(0, dtype=np.float32)
        self.update()

    def update_audio(self, audio: np.ndarray, sample_rate: int):
        x = np.asarray(audio, dtype=np.float32).reshape(-1)
        if x.size < 256:
            return
        self.sample_rate = int(sample_rate)

        # Spectrum display from latest audio.
        self._update_spectrum(x)

        # Waterfall gets its own short-time FFT stream so each call can add multiple rows.
        self._wf_buffer = np.concatenate((self._wf_buffer, x))
        self._consume_waterfall_frames()

        self.update()

    def _update_spectrum(self, x: np.ndarray):
        max_n = min(x.size, int(self.sample_rate * 0.35))
        x = x[-max_n:].astype(np.float64, copy=False)
        nfft = 1 << int(np.floor(np.log2(max(512, x.size))))
        nfft = min(nfft, 16384)
        x = x[-nfft:]
        x = x - np.mean(x)
        x *= np.hanning(x.size)

        spec = np.fft.rfft(x)
        mag = np.abs(spec) / max(1.0, x.size / 2.0)
        db = 20.0 * np.log10(np.maximum(mag, 1e-8))
        freqs = np.fft.rfftfreq(x.size, 1.0 / self.sample_rate)

        mask = (freqs >= self.f_min) & (freqs <= self.f_max)
        f = freqs[mask]
        d = db[mask]
        if d.size == 0:
            return

        if self._smoothed is None or self._smoothed.shape != d.shape:
            self._smoothed = d
        else:
            self._smoothed = 0.72 * self._smoothed + 0.28 * d

        self.freqs = f.astype(np.float32)
        self.db = self._smoothed.astype(np.float32)

        useful = (f >= 1300.0) & (f <= 2500.0)
        if np.any(useful):
            fu = f[useful]
            du = self._smoothed[useful]
            p = 10.0 ** (du / 10.0)
            self.peak_hz = float(fu[int(np.argmax(du))])
            ps = float(np.sum(p))
            self.centroid_hz = float(np.sum(fu * p) / ps) if ps > 0 else None

            peak_db = float(np.max(du))
            if peak_db < -75.0 or self.centroid_hz is None:
                self.tune_text = "Signal too weak"
                self.tune_state = "too_weak"
                self.tune_offset_hz = 0.0
            else:
                off = self.centroid_hz - 1900.0
                self.tune_offset_hz = float(off)
                if abs(off) <= 60.0:
                    self.tune_text = "Tuning OK"
                    self.tune_state = "ok"
                elif off < 0:
                    self.tune_text = f"Signal low by {abs(off):.0f} Hz - tune receiver higher"
                    self.tune_state = "low"
                else:
                    self.tune_text = f"Signal high by {abs(off):.0f} Hz - tune receiver lower"
                    self.tune_state = "high"

    def _consume_waterfall_frames(self):
        # At lower sample rates keep about the same visual time speed.
        target_hop = max(512, int(round(self.sample_rate / 25.0)))
        self._wf_hop = target_hop
        if self._wf_fft > self.sample_rate // 4:
            self._wf_fft = 1024
            self._wf_window = np.hanning(self._wf_fft).astype(np.float32)

        while self._wf_buffer.size >= self._wf_fft:
            frame = self._wf_buffer[:self._wf_fft].astype(np.float64, copy=False)
            self._wf_buffer = self._wf_buffer[self._wf_hop:]
            frame = frame - np.mean(frame)
            frame *= self._wf_window

            spec = np.fft.rfft(frame)
            mag = np.abs(spec) / max(1.0, frame.size / 2.0)
            db = 20.0 * np.log10(np.maximum(mag, 1e-8))
            freqs = np.fft.rfftfreq(frame.size, 1.0 / self.sample_rate)

            target_f = np.linspace(self.f_min, self.f_max, self.waterfall_cols)
            row = np.interp(target_f, freqs, db, left=self.db_min, right=self.db_min)
            scaled = (row - self.db_min) / (self.db_max - self.db_min)
            scaled = np.clip(scaled, 0.0, 1.0)
            scaled = np.power(scaled, 0.60)
            row8 = np.round(scaled * 255.0).astype(np.uint8)

            self.waterfall[:-1] = self.waterfall[1:]
            self.waterfall[-1] = row8
            self.event_rows[:-1] = self.event_rows[1:]
            self.event_rows[-1] = 0


    def mark_event(self, kind: str):
        """Place a confirmed autodetection event on the newest waterfall row."""
        code = {"START": 1, "PHASING": 2, "IMAGE": 3, "STOP": 4}.get(str(kind).upper(), 0)
        if code:
            # Several state transitions can be confirmed in one GUI/audio batch.
            # Preserve their order by moving an existing newest marker one row up.
            if self.event_rows[-1] != 0:
                for i in range(self.waterfall_rows - 2, -1, -1):
                    if self.event_rows[i] == 0:
                        self.event_rows[i] = self.event_rows[-1]
                        break
            self.event_rows[-1] = code
            self.update()

    @staticmethod
    def _event_style(code: int):
        styles = {
            1: ("START", QColor(60, 180, 90)),
            2: ("PHASING", QColor(225, 175, 55)),
            3: ("IMAGE", QColor(65, 170, 220)),
            4: ("STOP", QColor(220, 75, 75)),
        }
        return styles.get(int(code), ("", QColor(160, 160, 160)))

    def _x(self, hz: float, left: float, width: float) -> float:
        return left + (hz - self.f_min) / (self.f_max - self.f_min) * width

    def _y(self, db: float, top: float, height: float) -> float:
        v = (db - self.db_min) / (self.db_max - self.db_min)
        v = float(np.clip(v, 0.0, 1.0))
        return top + (1.0 - v) * height

    def _draw_frequency_refs(self, p: QPainter, left: float, top: float, height: float, width: float, labels=True):
        refs = ((1500.0, "BLACK"), (1900.0, "CENTER"), (2300.0, "WHITE"))
        for hz, label in refs:
            x = self._x(hz, left, width)
            pen = QPen(QColor(70, 150, 90, 200), 2 if hz == 1900 else 1)
            p.setPen(pen)
            p.drawLine(int(x), int(top), int(x), int(top + height))
            if labels:
                p.drawText(int(x + 4), int(top + 14), label)

    def _waterfall_qimage(self) -> QImage:
        # Blue -> cyan -> yellow -> white palette makes weak and strong signals easy to distinguish.
        g = self.waterfall.astype(np.uint8)
        rgb = np.empty((g.shape[0], g.shape[1], 3), dtype=np.uint8)
        v = g.astype(np.float32) / 255.0
        rgb[..., 0] = np.clip((v - 0.45) * 2.2, 0, 1) * 255
        rgb[..., 1] = np.clip(v * 1.5, 0, 1) * 255
        rgb[..., 2] = np.clip(0.25 + v * 1.4, 0, 1) * 255
        self._wf_rgb = np.ascontiguousarray(rgb)
        return QImage(
            self._wf_rgb.data,
            self._wf_rgb.shape[1],
            self._wf_rgb.shape[0],
            self._wf_rgb.strides[0],
            QImage.Format_RGB888,
        ).copy()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        p.fillRect(r, self.palette().base())

        left, right, top, bottom = 48.0, 18.0, 14.0, 28.0
        gap = 18.0
        total_h = max(10.0, r.height() - top - bottom)
        # Scale both panes with the available height. The previous fixed
        # 145 px waterfall made the whole application too tall on notebooks.
        waterfall_h = float(np.clip(total_h * 0.38, 48.0, 135.0))
        spectrum_h = max(48.0, total_h - waterfall_h - gap)
        w = max(10.0, r.width() - left - right)

        band_x1 = self._x(1500.0, left, w)
        band_x2 = self._x(2300.0, left, w)
        p.fillRect(int(band_x1), int(top), int(band_x2 - band_x1), int(spectrum_h), QColor(80, 130, 95, 35))
        c1 = self._x(1850.0, left, w)
        c2 = self._x(1950.0, left, w)
        p.fillRect(int(c1), int(top), int(c2 - c1), int(spectrum_h), QColor(80, 140, 100, 48))

        p.setPen(QPen(QColor(130, 130, 130, 70), 1))
        for hz in (1000, 1500, 1900, 2300, 2500, 3000):
            x = self._x(float(hz), left, w)
            p.drawLine(int(x), int(top), int(x), int(top + spectrum_h))
            txt = str(hz)
            tw = QFontMetrics(p.font()).horizontalAdvance(txt)
            p.drawText(int(x - tw / 2), int(top + spectrum_h + 18), txt)
        for db in (-80, -60, -40, -20):
            y = self._y(float(db), top, spectrum_h)
            p.drawLine(int(left), int(y), int(left + w), int(y))
            p.drawText(3, int(y + 4), f"{db}")

        self._draw_frequency_refs(p, left, top, spectrum_h, w, labels=True)

        if self.freqs.size and self.db.size:
            path = QPainterPath()
            first = True
            for hz, db in zip(self.freqs, self.db):
                x = self._x(float(hz), left, w)
                y = self._y(float(db), top, spectrum_h)
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(self.palette().highlight().color(), 1.6))
            p.drawPath(path)

        if self.centroid_hz is not None and self.f_min <= self.centroid_hz <= self.f_max:
            x = self._x(self.centroid_hz, left, w)
            p.setPen(QPen(QColor(210, 120, 50), 2))
            p.drawLine(int(x), int(top + 4), int(x), int(top + spectrum_h - 4))
            p.drawText(int(min(left + w - 140, x + 5)), int(top + spectrum_h - 8), f"center {self.centroid_hz:.0f} Hz")

        wf_top = top + spectrum_h + gap
        p.setPen(self.palette().text().color())
        p.drawText(int(left), int(wf_top - 8), "Waterfall")

        # Compact legend for the event timeline.
        legend_x = left + 245
        for code in (1, 2, 3, 4):
            label, color = self._event_style(code)
            p.fillRect(int(legend_x), int(wf_top - 19), 9, 9, color)
            p.setPen(self.palette().text().color())
            p.drawText(int(legend_x + 13), int(wf_top - 10), label)
            legend_x += QFontMetrics(p.font()).horizontalAdvance(label) + 34

        wf_img = self._waterfall_qimage()
        target = self.rect().adjusted(int(left), int(wf_top), int(-right), int(-(r.height() - (wf_top + waterfall_h))))
        p.drawImage(target, wf_img)

        self._draw_frequency_refs(p, left, wf_top, waterfall_h, w, labels=False)

        p.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for hz in (1000, 2500, 3000):
            x = self._x(float(hz), left, w)
            p.drawLine(int(x), int(wf_top), int(x), int(wf_top + waterfall_h))

        # Event markers are tied to waterfall rows, so they scroll upward with time.
        row_h = waterfall_h / float(max(1, self.waterfall_rows))
        for row, code in enumerate(self.event_rows):
            if not code:
                continue
            label, color = self._event_style(int(code))
            y = wf_top + (row + 0.5) * row_h
            p.setPen(QPen(color, 2))
            p.drawLine(int(left), int(y), int(left + w), int(y))
            fm = QFontMetrics(p.font())
            tw = fm.horizontalAdvance(label) + 10
            th = max(14, fm.height() + 2)
            bx = int(left + w - tw - 3)
            by = int(y - th / 2)
            bg = QColor(color)
            bg.setAlpha(210)
            p.fillRect(bx, by, tw, th, bg)
            p.setPen(QColor(255, 255, 255))
            p.drawText(bx + 5, by + th - 4, label)

        p.setPen(self.palette().text().color())
        p.drawText(int(left), int(r.height() - 5), "Frequency [Hz]")
