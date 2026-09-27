from __future__ import annotations

import sys
import os
import json
from pathlib import Path
from datetime import datetime
import numpy as np
import soundfile as sf

from PySide6.QtCore import QTimer, Qt, QSettings, QLocale
from PySide6.QtGui import QImage, QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSlider,
    QSpinBox, QDoubleSpinBox, QVBoxLayout, QWidget, QScrollArea, QProgressBar,
    QLineEdit, QCompleter, QListWidget, QListWidgetItem, QTableWidget,
    QTableWidgetItem, QHeaderView
)

from .audio import LiveAudioInput
from .decoder import FaxImageDecoder
from .spectrum import SpectrumWidget
from .autodetect import WefaxAutoDetector
from .slant import AutoSlantEstimator
from .cat_hamlib import HamlibRig
from .alignment import estimate_wrap_shift
from .i18n import LANGUAGES, tr
from . import APP_ID, APP_NAME, __version__


def resource_path(*parts: str) -> Path:
    """Return a path that works from source and from a PyInstaller bundle."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(getattr(sys, "_MEIPASS"))
    else:
        base = Path(__file__).resolve().parent.parent
    return base.joinpath(*parts)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("OK5TVR", "HFWeatherFax")
        saved_language = str(self.settings.value("language", "") or "")
        if saved_language not in LANGUAGES:
            saved_language = "cs" if QLocale.system().name().lower().startswith("cs") else "en"
        self.language = saved_language
        self._tr_bindings: list[tuple[object, str, str]] = []

        self.setWindowTitle(f"{APP_NAME} {__version__}")
        icon_path = resource_path("assets", "hfweatherfax_ok5tvr.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1500, 960)

        self.audio = None
        self.decoder = FaxImageDecoder(48000, 120, 576)
        self.autodetector = WefaxAutoDetector(48000, "Normal")
        self.slant_estimator = AutoSlantEstimator(self.decoder.width)
        self.image_lines: list[np.ndarray] = []
        self.invert = False
        self.wav_data = None
        self.wav_pos = 0
        self.wav_rate = 48000
        self.current_source_name = "live"
        self.last_autosave_signature = None
        self.image_generation = 0
        self.image_shift_px = 0
        self.auto_realign_done = False
        self.cat = None

        self.station_catalog = self.load_station_catalog()
        self.station_favorites = self.load_station_favorites()

        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.process_audio)

        self.cat_timer = QTimer(self)
        self.cat_timer.setInterval(1000)
        self.cat_timer.timeout.connect(self.poll_cat)
        self.cat_freq_dirty = False
        self.cat_last_rig_hz = None

        self._build_ui()
        self._restore_cat_settings()
        self.refresh_devices()
        self.update_status(self._tr("ready"))

    def _tr(self, key: str, **kwargs) -> str:
        return tr(self.language, key, **kwargs)

    def _bind_tr(self, widget, key: str, setter: str = "setText"):
        self._tr_bindings.append((widget, setter, key))
        getattr(widget, setter)(self._tr(key))
        return widget

    def _label(self, key: str) -> QLabel:
        return self._bind_tr(QLabel(), key)

    def _button(self, key: str) -> QPushButton:
        return self._bind_tr(QPushButton(), key)

    def _check(self, key: str) -> QCheckBox:
        return self._bind_tr(QCheckBox(), key)

    def _group(self, key: str) -> QGroupBox:
        return self._bind_tr(QGroupBox(), key, "setTitle")

    def _populate_sensitivity_combo(self, selected: str | None = None):
        if not hasattr(self, "sensitivity"):
            return
        selected = selected or self.sensitivity.currentData() or "High"
        self.sensitivity.blockSignals(True)
        self.sensitivity.clear()
        self.sensitivity.addItem(self._tr("sens_normal"), "Normal")
        self.sensitivity.addItem(self._tr("sens_high"), "High")
        self.sensitivity.addItem(self._tr("sens_weak"), "Weak signal")
        idx = self.sensitivity.findData(selected)
        self.sensitivity.setCurrentIndex(idx if idx >= 0 else 1)
        self.sensitivity.blockSignals(False)

    def _populate_station_band_combo(self, selected: str | None = None):
        if not hasattr(self, "station_band"):
            return
        selected = selected or self.station_band.currentData() or "all"
        self.station_band.blockSignals(True)
        self.station_band.clear()
        self.station_band.addItem(self._tr("band_all"), "all")
        self.station_band.addItem("< 4 MHz", "lt4")
        self.station_band.addItem("4–8 MHz", "4-8")
        self.station_band.addItem("8–12 MHz", "8-12")
        self.station_band.addItem("12–18 MHz", "12-18")
        self.station_band.addItem("> 18 MHz", "gt18")
        idx = self.station_band.findData(selected)
        self.station_band.setCurrentIndex(idx if idx >= 0 else 0)
        self.station_band.blockSignals(False)

    def _populate_station_sort_combo(self, selected: str | None = None):
        if not hasattr(self, "station_sort"):
            return
        selected = selected or self.station_sort.currentData() or "country"
        self.station_sort.blockSignals(True)
        self.station_sort.clear()
        self.station_sort.addItem(self._tr("sort_country"), "country")
        self.station_sort.addItem(self._tr("sort_service"), "service")
        self.station_sort.addItem(self._tr("sort_frequency"), "frequency")
        self.station_sort.addItem(self._tr("sort_name"), "name")
        idx = self.station_sort.findData(selected)
        self.station_sort.setCurrentIndex(idx if idx >= 0 else 0)
        self.station_sort.blockSignals(False)

    def _language_changed(self, _index: int = -1):
        if not hasattr(self, "language_combo"):
            return
        code = self.language_combo.currentData()
        if code not in LANGUAGES or code == self.language:
            return
        self.language = str(code)
        self.settings.setValue("language", self.language)
        self.retranslate_ui()

    def retranslate_ui(self):
        """Refresh all user-facing texts without restarting the decoder."""
        for widget, setter, key in self._tr_bindings:
            try:
                getattr(widget, setter)(self._tr(key))
            except RuntimeError:
                pass

        if hasattr(self, "cat_width"):
            self.cat_width.setSpecialValueText(self._tr("no_change"))

        if hasattr(self, "cat_vfo"):
            selected = self.cat_vfo.currentData()
            self.cat_vfo.blockSignals(True)
            self.cat_vfo.clear()
            self.cat_vfo.addItem(self._tr("vfo_current"), "Current")
            self.cat_vfo.addItem("A", "A")
            self.cat_vfo.addItem("B", "B")
            idx = self.cat_vfo.findData(selected)
            self.cat_vfo.setCurrentIndex(idx if idx >= 0 else 0)
            self.cat_vfo.blockSignals(False)

        if hasattr(self, "sensitivity"):
            self._populate_sensitivity_combo(self.sensitivity.currentData())
        if hasattr(self, "station_band"):
            self._populate_station_band_combo(self.station_band.currentData())
        if hasattr(self, "station_sort"):
            self._populate_station_sort_combo(self.station_sort.currentData())

        if hasattr(self, "station_schedule"):
            self.station_schedule.setHorizontalHeaderLabels(["UTC", self._tr("table_product"), "RPM/IOC"])

        if hasattr(self, "station_country") and hasattr(self, "station_service"):
            country = self.station_country.currentData()
            service = self.station_service.currentData()
            self.populate_station_filters(country, service)
            self.filter_station_list()

        if hasattr(self, "phasing_quality"):
            self._set_lock_quality(
                self.phasing_quality_label,
                self.phasing_quality,
                self.phasing_quality.value() / 100.0,
                "phasing_lock",
            )
        if hasattr(self, "hsync_quality"):
            self._set_lock_quality(
                self.hsync_quality_label,
                self.hsync_quality,
                self.hsync_quality.value() / 100.0,
                "hsync_lock",
            )

        if hasattr(self, "cat_model") and self.cat_model.count() == 1 and self.cat_model.itemData(0) == 1:
            self.cat_model.setItemText(0, self._tr("load_hamlib_models"))

        if hasattr(self, "auto_status"):
            self.auto_status.setText(self._auto_status_text())
        if hasattr(self, "auto_slant_status"):
            self.auto_slant_status.setText(self._slant_status_text())
        if hasattr(self, "level_cal_status") and getattr(self.autodetector, "state", "WAIT_START") != "RECEIVING":
            self.level_cal_status.setText(self._tr("levels_nominal"))
        if hasattr(self, "line_start_status") and not self.auto_realign_done:
            self.line_start_status.setText(self._tr("line_start_not_evaluated"))

        if hasattr(self, "spectrum_info"):
            if getattr(self.spectrum, "peak_hz", None) is None:
                self.spectrum_info.setText(self._tr("spectrum_expected"))
            else:
                peak = f"{self.spectrum.peak_hz:.0f} Hz"
                tuning = (
                    self._tr("image_active_tuning_disabled")
                    if self.auto_detect.isChecked() and self.autodetector.state == "RECEIVING"
                    else self._spectrum_tuning_text()
                )
                self.spectrum_info.setText(self._tr("spectrum_live", peak=peak, tuning=tuning))

        if hasattr(self, "image_label") and not self.image_lines:
            self.image_label.setText(self._tr("no_image"))

        if hasattr(self, "level"):
            if self.audio is not None:
                self.level.setText(self._tr("level_value", db=self.audio.last_level_db))
            else:
                self.level.setText(self._tr("level"))

        if hasattr(self, "status"):
            if self.audio is not None:
                if self.image_lines:
                    lpm = self.autodetector.detected_lpm or self.lpm.currentText()
                    self.status.setText(self._tr("receiving_lpm", lpm=lpm, lines=len(self.image_lines)))
                else:
                    self.status.setText(self._tr(
                        "live_receiving",
                        sr=self.audio.sample_rate,
                        lpm=self.lpm.currentText(),
                        ioc=self.ioc.currentText(),
                    ))
            elif self.wav_data is not None:
                self.status.setText(self._tr("decoding_file", name=self.current_source_name, sr=self.wav_rate))
            else:
                self.status.setText(self._tr("ready"))

        if hasattr(self, "cat_connect_btn"):
            if self.cat is not None and self.cat.connected:
                self.cat_connect_btn.setText(self._tr("disconnect_cat"))
                self.read_cat(silent=True)
            else:
                self.cat_connect_btn.setText(self._tr("connect_cat"))
                if hasattr(self, "cat_status"):
                    self.cat_status.setText(self._tr("cat_disconnected"))

        if hasattr(self, "station_info"):
            station = self._current_station()
            if station is not None:
                self.station_selected(self.station_list.currentItem())
            else:
                self.station_info.setText(self._tr("select_station"))
                self.station_favorite_btn.setText(self._tr("add_favorite"))

    def _display_sensitivity(self, value: str) -> str:
        return {
            "Normal": self._tr("sens_normal"),
            "High": self._tr("sens_high"),
            "Weak signal": self._tr("sens_weak"),
        }.get(value, value)

    def _auto_status_text(self) -> str:
        d = self.autodetector
        if d.state == "WAIT_START":
            if d.last_phasing_confidence >= 0.45:
                return self._tr("auto_checking_phasing", conf=d.last_phasing_confidence)
            tone = max(d.last_start_confidence, d.last_stop_confidence)
            return self._tr(
                "auto_waiting_detail",
                sensitivity=self._display_sensitivity(d.sensitivity),
                tone=tone,
            )
        if d.state == "PHASING":
            return self._tr(
                "auto_start_phasing",
                conf=d.last_phasing_confidence,
                sensitivity=self._display_sensitivity(d.sensitivity),
            )
        if d.state == "RECEIVING":
            lpm = d.detected_lpm or "?"
            return self._tr("auto_receiving_detail", lpm=lpm, conf=d.last_stop_confidence)
        return "AUTO"

    def _slant_status_text(self) -> str:
        if self.slant_estimator.ppm is None:
            return self._tr("auto_slant_measuring")
        return self._tr(
            "auto_slant_value",
            ppm=self.slant_estimator.ppm,
            conf=self.slant_estimator.confidence,
        )

    def _spectrum_tuning_text(self) -> str:
        state = getattr(self.spectrum, "tune_state", "no_signal")
        offset = abs(float(getattr(self.spectrum, "tune_offset_hz", 0.0)))
        if state == "too_weak":
            return self._tr("signal_too_weak")
        if state == "ok":
            return self._tr("tuning_ok")
        if state == "low":
            return self._tr("signal_low", hz=offset)
        if state == "high":
            return self._tr("signal_high", hz=offset)
        return self._tr("no_signal")

    def _build_ui(self):
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # --- shared widgets -------------------------------------------------
        controls = self._group("controls")
        g = QGridLayout(controls)

        self.device = QComboBox()
        self.refresh_btn = self._button("refresh")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.rate = QComboBox()
        self.rate.addItems(["48000", "44100"])
        self.lpm = QComboBox()
        self.lpm.addItems(["60", "90", "120", "240"])
        self.lpm.setCurrentText("120")
        self.ioc = QComboBox()
        self.ioc.addItems(["576", "288"])

        self.auto_detect = self._check("auto_detect")
        self.auto_detect.setChecked(True)
        self.auto_hsync = self._check("auto_hsync")
        self.auto_hsync.setChecked(True)
        self.auto_clear = self._check("auto_clear")
        self.auto_clear.setChecked(True)
        self.auto_status = QLabel(self._tr("auto_waiting"))
        self.sensitivity = QComboBox()
        self._populate_sensitivity_combo("High")
        self.sensitivity.currentIndexChanged.connect(self._change_detection_sensitivity)
        self.phasing_quality_label = QLabel()
        self.phasing_quality = QProgressBar()
        self.phasing_quality.setRange(0, 100)
        self.phasing_quality.setValue(0)
        self.phasing_quality.setFormat("0%")
        self.hsync_quality_label = QLabel()
        self.hsync_quality = QProgressBar()
        self.hsync_quality.setRange(0, 100)
        self.hsync_quality.setValue(0)
        self.hsync_quality.setFormat("0%")

        self.start_btn = self._button("start_live")
        self.stop_btn = self._button("stop")
        self.open_btn = self._button("open_audio")
        self.save_btn = self._button("save_png")
        self.clear_btn = self._button("clear_image")
        self.auto_save = self._check("auto_save_png")
        self.auto_save.setChecked(True)
        self.auto_save_folder = QLineEdit()
        self.auto_save_folder.setReadOnly(True)
        self.auto_save_folder.setText(str(self.default_autosave_dir()))
        self.auto_save_folder_btn = self._button("folder")
        self.auto_save_folder_btn.clicked.connect(self.choose_autosave_folder)

        self.start_btn.clicked.connect(self.start_live)
        self.stop_btn.clicked.connect(self.stop_all)
        self.open_btn.clicked.connect(self.open_audio_file)
        self.save_btn.clicked.connect(self.save_png)
        self.clear_btn.clicked.connect(self.clear_image)

        g.addWidget(self._label("audio_input"), 0, 0)
        g.addWidget(self.device, 0, 1, 1, 3)
        g.addWidget(self.refresh_btn, 0, 4)
        g.addWidget(self._label("sample_rate"), 1, 0)
        g.addWidget(self.rate, 1, 1)
        g.addWidget(QLabel("LPM"), 1, 2)
        g.addWidget(self.lpm, 1, 3)
        g.addWidget(QLabel("IOC"), 1, 4)
        g.addWidget(self.ioc, 1, 5)
        g.addWidget(self.auto_detect, 2, 0, 1, 3)
        g.addWidget(self.auto_hsync, 2, 3, 1, 3)
        g.addWidget(self.auto_clear, 3, 0, 1, 3)
        g.addWidget(self._label("detection_sensitivity"), 3, 3)
        g.addWidget(self.sensitivity, 3, 4, 1, 2)
        g.addWidget(self.auto_status, 4, 0, 1, 6)
        g.addWidget(self.phasing_quality_label, 5, 0)
        g.addWidget(self.phasing_quality, 5, 1, 1, 2)
        g.addWidget(self.hsync_quality_label, 5, 3)
        g.addWidget(self.hsync_quality, 5, 4, 1, 2)

        btns = QGridLayout()
        btns.addWidget(self.start_btn, 0, 0)
        btns.addWidget(self.stop_btn, 0, 1)
        btns.addWidget(self.open_btn, 1, 0)
        btns.addWidget(self.save_btn, 1, 1)
        btns.addWidget(self.clear_btn, 2, 0, 1, 2)
        g.addLayout(btns, 6, 0, 1, 3)

        g.addWidget(self.auto_save, 6, 3, 1, 3)
        g.addWidget(self._label("auto_save_folder"), 7, 0)
        g.addWidget(self.auto_save_folder, 7, 1, 1, 4)
        g.addWidget(self.auto_save_folder_btn, 7, 5)

        self.language_label = self._label("language")
        self.language_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.language_combo.addItem(name, code)
        lang_index = self.language_combo.findData(self.language)
        if lang_index >= 0:
            self.language_combo.setCurrentIndex(lang_index)
        self.language_combo.currentIndexChanged.connect(self._language_changed)
        g.addWidget(self.language_label, 8, 0)
        g.addWidget(self.language_combo, 8, 1, 1, 2)

        cat_box = QGroupBox("CAT / Hamlib")
        catg = QGridLayout(cat_box)
        self.cat_dll = QLineEdit()
        self._bind_tr(self.cat_dll, "hamlib_placeholder", "setPlaceholderText")
        self.cat_dll_btn = QPushButton("Hamlib…")
        self.cat_dll_btn.clicked.connect(self.browse_hamlib_dll)
        self.cat_model = QComboBox()
        self.cat_model.setEditable(True)
        self.cat_model.setInsertPolicy(QComboBox.NoInsert)
        self.cat_model.setMinimumContentsLength(22)
        self.cat_model.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.cat_model.completer().setFilterMode(Qt.MatchContains)
        self.cat_model.addItem(self._tr("load_hamlib_models"), 1)
        self.cat_models_btn = self._button("load_models")
        self.cat_models_btn.clicked.connect(self.load_hamlib_models)
        self.cat_port = QComboBox()
        self.cat_port.setEditable(True)
        self.cat_port.addItems([f"COM{i}" for i in range(1, 33)])
        self.cat_port.setCurrentText("COM3")
        self.cat_baud = QComboBox()
        self.cat_baud.addItems(["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200", "230400"])
        self.cat_baud.setCurrentText("115200")
        self.cat_vfo = QComboBox()
        self.cat_vfo.addItem("Current", "Current")
        self.cat_vfo.addItem("A", "A")
        self.cat_vfo.addItem("B", "B")
        self.cat_radio_mode = QComboBox()
        self.cat_radio_mode.addItems([
            "USB", "LSB", "AM", "FAX",
            "PKTUSB", "PKTLSB", "PKTFM",
            "CW", "CWR", "FM", "RTTY", "RTTYR",
        ])
        self.cat_radio_mode.setCurrentText("USB")
        self.cat_freq = QDoubleSpinBox()
        self.cat_freq.setRange(0.001, 1300.0)
        self.cat_freq.setDecimals(6)
        self.cat_freq.setSingleStep(0.001)
        self.cat_freq.setSuffix(" MHz")
        self.cat_freq.setValue(10.100000)
        self.cat_freq.setKeyboardTracking(False)
        self.cat_freq.lineEdit().textEdited.connect(self._cat_freq_edited)
        self.cat_freq.lineEdit().returnPressed.connect(self.set_cat_frequency)
        self.cat_width = QSpinBox()
        self.cat_width.setRange(-1, 20000)
        self.cat_width.setSpecialValueText(self._tr("no_change"))
        self.cat_width.setValue(-1)
        self.cat_width.setSuffix(" Hz")
        self.cat_connect_btn = self._button("connect_cat")
        self.cat_connect_btn.clicked.connect(self.toggle_cat)
        self.cat_read_btn = self._button("read_rig")
        self.cat_read_btn.clicked.connect(self.read_cat)
        self.cat_set_btn = self._button("set_frequency")
        self.cat_set_btn.clicked.connect(self.set_cat_frequency)
        self.cat_mode_btn = self._button("set_mode")
        self.cat_mode_btn.clicked.connect(self.set_cat_mode)
        self.cat_poll = self._check("poll_1s")
        self.cat_poll.setChecked(True)
        self.cat_status = QLabel(self._tr("cat_disconnected"))

        catg.addWidget(QLabel("Hamlib"), 0, 0)
        catg.addWidget(self.cat_dll, 0, 1, 1, 3)
        catg.addWidget(self.cat_dll_btn, 0, 4)
        catg.addWidget(self._label("rig"), 1, 0)
        catg.addWidget(self.cat_model, 1, 1, 1, 3)
        catg.addWidget(self.cat_models_btn, 1, 4)
        catg.addWidget(self._label("port"), 2, 0)
        catg.addWidget(self.cat_port, 2, 1)
        catg.addWidget(self._label("baud"), 2, 2)
        catg.addWidget(self.cat_baud, 2, 3)
        catg.addWidget(self._label("frequency"), 3, 0)
        catg.addWidget(self.cat_freq, 3, 1)
        catg.addWidget(self._label("mode"), 3, 2)
        catg.addWidget(self.cat_radio_mode, 3, 3)
        catg.addWidget(self._label("vfo"), 4, 0)
        catg.addWidget(self.cat_vfo, 4, 1)
        catg.addWidget(self._label("filter"), 4, 2)
        catg.addWidget(self.cat_width, 4, 3)
        catg.addWidget(self.cat_connect_btn, 5, 0)
        catg.addWidget(self.cat_read_btn, 5, 1)
        catg.addWidget(self.cat_set_btn, 5, 2)
        catg.addWidget(self.cat_mode_btn, 5, 3)
        catg.addWidget(self.cat_poll, 5, 4)
        catg.addWidget(self.cat_status, 6, 0, 1, 5)

        correction = self._group("correction")
        cg = QGridLayout(correction)
        self.phase = QDoubleSpinBox()
        self.phase.setRange(-100.0, 100.0)
        self.phase.setDecimals(2)
        self.phase.setSingleStep(0.25)
        self.phase.setSuffix(" %")
        self.slant = QDoubleSpinBox()
        self.slant.setRange(-5000.0, 5000.0)
        self.slant.setDecimals(1)
        self.slant.setSingleStep(10.0)
        self.slant.setSuffix(" ppm")
        self.auto_slant = self._check("auto_slant")
        self.auto_slant.setChecked(True)
        self.auto_slant_status = QLabel(self._tr("auto_slant_measuring"))
        self.invert_box = self._check("invert")
        self.invert_box.toggled.connect(self.set_invert)
        self.auto_levels = self._check("auto_levels")
        self.auto_levels.setChecked(True)
        self.level_cal_status = QLabel(self._tr("levels_nominal"))
        self.auto_line_start = self._check("auto_line_start")
        self.auto_line_start.setChecked(True)
        self.realign_btn = self._button("realign_image")
        self.realign_btn.clicked.connect(self.realign_image)
        self.shift_left_5_btn = QPushButton("← 5%")
        self.shift_left_1_btn = QPushButton("← 1%")
        self.shift_right_1_btn = QPushButton("1% →")
        self.shift_right_5_btn = QPushButton("5% →")
        self.shift_left_5_btn.clicked.connect(lambda: self.nudge_image(-5.0))
        self.shift_left_1_btn.clicked.connect(lambda: self.nudge_image(-1.0))
        self.shift_right_1_btn.clicked.connect(lambda: self.nudge_image(1.0))
        self.shift_right_5_btn.clicked.connect(lambda: self.nudge_image(5.0))
        self.line_start_status = QLabel(self._tr("line_start_not_evaluated"))
        cg.addWidget(self._label("horizontal_phase"), 0, 0)
        cg.addWidget(self.phase, 0, 1)
        cg.addWidget(self._label("slant_correction"), 0, 2)
        cg.addWidget(self.slant, 0, 3)
        cg.addWidget(self.invert_box, 0, 4)
        cg.addWidget(self.auto_slant, 1, 0, 1, 2)
        cg.addWidget(self.auto_slant_status, 1, 2, 1, 3)
        cg.addWidget(self.auto_levels, 2, 0, 1, 2)
        cg.addWidget(self.level_cal_status, 2, 2, 1, 3)
        cg.addWidget(self.auto_line_start, 3, 0, 1, 2)
        cg.addWidget(self.realign_btn, 3, 2)
        cg.addWidget(self.shift_left_5_btn, 3, 3)
        cg.addWidget(self.shift_left_1_btn, 3, 4)
        cg.addWidget(self.shift_right_1_btn, 4, 3)
        cg.addWidget(self.shift_right_5_btn, 4, 4)
        cg.addWidget(self.line_start_status, 5, 0, 1, 5)

        spectrum_box = self._group("spectrum")
        sg = QVBoxLayout(spectrum_box)
        self.spectrum = SpectrumWidget()
        self.spectrum_info = QLabel(self._tr("spectrum_expected"))
        self.spectrum_info.setAlignment(Qt.AlignCenter)
        sg.addWidget(self.spectrum)
        sg.addWidget(self.spectrum_info)

        image_box = self._group("images")
        ig = QVBoxLayout(image_box)
        self.image_title = self._label("decoded_image")
        self.image_title.setAlignment(Qt.AlignCenter)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.image_label.setText(self._tr("no_image"))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.image_label)
        self.status = QLabel(self._tr("ready"))
        self.level = QLabel(self._tr("level"))
        status_row = QHBoxLayout()
        status_row.addWidget(self.status, 1)
        status_row.addWidget(self.level)
        ig.addWidget(self.image_title)
        ig.addWidget(self.scroll, 1)
        ig.addLayout(status_row)

        stations_box = self._group("stations")
        stg = QVBoxLayout(stations_box)

        self.station_search = QLineEdit()
        self._bind_tr(self.station_search, "station_search", "setPlaceholderText")
        self.station_search.textChanged.connect(self.filter_station_list)

        filter_row = QGridLayout()
        self.station_country = QComboBox()
        self.station_service = QComboBox()
        self.station_band = QComboBox()
        self._populate_station_band_combo()
        self.station_sort = QComboBox()
        self._populate_station_sort_combo()
        self.station_fav_only = self._check("favorites_only")
        self.station_country.currentIndexChanged.connect(self.filter_station_list)
        self.station_service.currentIndexChanged.connect(self.filter_station_list)
        self.station_band.currentIndexChanged.connect(self.filter_station_list)
        self.station_sort.currentIndexChanged.connect(self.filter_station_list)
        self.station_fav_only.toggled.connect(self.filter_station_list)
        filter_row.addWidget(self._label("country"), 0, 0)
        filter_row.addWidget(self.station_country, 0, 1)
        filter_row.addWidget(self._label("service"), 1, 0)
        filter_row.addWidget(self.station_service, 1, 1)
        filter_row.addWidget(self._label("band"), 2, 0)
        filter_row.addWidget(self.station_band, 2, 1)
        filter_row.addWidget(self._label("sort_by"), 3, 0)
        filter_row.addWidget(self.station_sort, 3, 1)
        filter_row.addWidget(self.station_fav_only, 4, 0, 1, 2)

        self.station_list = QListWidget()
        self.station_list.currentItemChanged.connect(self.station_selected)
        self.station_list.itemDoubleClicked.connect(lambda _item: self.apply_station_to_cat())

        self.station_favorite_btn = QPushButton(self._tr("add_favorite"))
        self.station_favorite_btn.clicked.connect(self.toggle_station_favorite)
        self.station_info = QLabel(self._tr("select_station"))
        self.station_info.setWordWrap(True)

        self.station_frequency = QComboBox()
        self.station_frequency.currentIndexChanged.connect(self.station_frequency_changed)
        self.station_fill_btn = self._button("copy_to_cat")
        self.station_fill_btn.clicked.connect(self.copy_station_to_cat_field)
        self.station_apply_btn = self._button("tune_via_cat")
        self.station_apply_btn.clicked.connect(self.apply_station_to_cat)

        self.station_schedule = QTableWidget(0, 3)
        self.station_schedule.setHorizontalHeaderLabels(["UTC", self._tr("table_product"), "RPM/IOC"])
        self.station_schedule.setEditTriggers(QTableWidget.NoEditTriggers)
        self.station_schedule.setSelectionBehavior(QTableWidget.SelectRows)
        self.station_schedule.verticalHeader().setVisible(False)
        self.station_schedule.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.station_schedule.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.station_schedule.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.station_schedule.setMinimumHeight(230)
        self.station_schedule_note = self._label("schedule_note")
        self.station_schedule_note.setWordWrap(True)

        stg.addWidget(self.station_search)
        stg.addLayout(filter_row)
        stg.addWidget(self.station_list, 1)
        stg.addWidget(self.station_favorite_btn)
        stg.addWidget(self.station_info)
        stg.addWidget(self._label("frequency_callsign"))
        stg.addWidget(self.station_frequency)
        st_btns = QHBoxLayout()
        st_btns.addWidget(self.station_fill_btn)
        st_btns.addWidget(self.station_apply_btn)
        stg.addLayout(st_btns)
        stg.addWidget(self._label("schedule"))
        stg.addWidget(self.station_schedule, 2)
        stg.addWidget(self.station_schedule_note)

        # --- three-column layout matching the sketch -----------------------
        left_col = QVBoxLayout()
        left_col.addWidget(controls)
        left_col.addWidget(cat_box)
        left_col.addWidget(correction)
        left_col.addWidget(spectrum_box, 1)

        center_col = QVBoxLayout()
        center_col.addWidget(image_box, 1)

        right_col = QVBoxLayout()
        right_col.addWidget(stations_box, 1)

        left_widget = QWidget()
        left_widget.setLayout(left_col)
        center_widget = QWidget()
        center_widget.setLayout(center_col)
        right_widget = QWidget()
        right_widget.setLayout(right_col)

        outer.addWidget(left_widget, 4)
        outer.addWidget(center_widget, 6)
        outer.addWidget(right_widget, 3)

        self.setCentralWidget(root)
        self.populate_station_filters()
        self.filter_station_list()
        self.retranslate_ui()

    def station_catalog_path(self) -> Path:
        return Path(__file__).resolve().parent / "station_catalog.json"

    def load_station_catalog(self):
        try:
            return json.loads(self.station_catalog_path().read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Station catalog load error: {exc}")
            return []

    def station_favorites_path(self) -> Path:
        if os.name == "nt" and os.environ.get("APPDATA"):
            base = Path(os.environ["APPDATA"]) / "HFWeatherFax"
        else:
            base = Path.home() / ".hfweatherfax"
        base.mkdir(parents=True, exist_ok=True)
        return base / "favorites.json"

    def load_station_favorites(self) -> set[str]:
        try:
            data = json.loads(self.station_favorites_path().read_text(encoding="utf-8"))
            return set(data.get("station_ids", []))
        except Exception:
            return set()

    def save_station_favorites(self):
        try:
            self.station_favorites_path().write_text(
                json.dumps({"station_ids": sorted(self.station_favorites)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            self.update_status(self._tr("favorites_save_error", error=exc))

    def populate_station_filters(self, selected_country=None, selected_service=None):
        countries = sorted({s.get("country", "") for s in self.station_catalog if s.get("country")})
        services = sorted({s.get("service", "") for s in self.station_catalog if s.get("service")})
        if selected_country is None and hasattr(self, "station_country"):
            selected_country = self.station_country.currentData()
        if selected_service is None and hasattr(self, "station_service"):
            selected_service = self.station_service.currentData()
        self.station_country.blockSignals(True)
        self.station_service.blockSignals(True)
        self.station_country.clear()
        self.station_country.addItem(self._tr("all_countries"), None)
        for country in countries:
            self.station_country.addItem(country, country)
        self.station_service.clear()
        self.station_service.addItem(self._tr("all_services"), None)
        for service in services:
            self.station_service.addItem(service, service)
        cidx = self.station_country.findData(selected_country)
        sidx = self.station_service.findData(selected_service)
        self.station_country.setCurrentIndex(cidx if cidx >= 0 else 0)
        self.station_service.setCurrentIndex(sidx if sidx >= 0 else 0)
        self.station_country.blockSignals(False)
        self.station_service.blockSignals(False)

    def _station_in_band(self, station, band: str) -> bool:
        if band == "all":
            return True
        vals = [float(f.get("khz", 0.0))/1000.0 for f in station.get("frequencies", [])]
        if not vals:
            return False
        if band == "lt4": return any(v < 4 for v in vals)
        if band == "4-8": return any(4 <= v < 8 for v in vals)
        if band == "8-12": return any(8 <= v < 12 for v in vals)
        if band == "12-18": return any(12 <= v <= 18 for v in vals)
        if band == "gt18": return any(v > 18 for v in vals)
        return True

    def filter_station_list(self, _value=None):
        if not hasattr(self, "station_list"):
            return
        query = self.station_search.text().strip().lower() if hasattr(self, "station_search") else ""
        country = self.station_country.currentData() if hasattr(self, "station_country") else None
        service = self.station_service.currentData() if hasattr(self, "station_service") else None
        band = self.station_band.currentData() if hasattr(self, "station_band") else "all"
        fav_only = self.station_fav_only.isChecked() if hasattr(self, "station_fav_only") else False
        current_id = None
        cur = self.station_list.currentItem()
        if cur is not None:
            current_id = cur.data(Qt.UserRole)
        self.station_list.clear()
        visible=[]
        for station in self.station_catalog:
            if country and station.get("country") != country:
                continue
            if service and station.get("service") != service:
                continue
            if fav_only and station.get("id") not in self.station_favorites:
                continue
            if not self._station_in_band(station, band):
                continue
            freqs = " ".join(str(f.get("khz", "")) for f in station.get("frequencies", []))
            hay = " ".join([station.get("country",""), station.get("location",""), station.get("service",""),
                            " ".join(station.get("call_signs",[])), freqs, station.get("notes","")]).lower()
            if query and query not in hay:
                continue
            visible.append(station)
        sort_mode = self.station_sort.currentData() if hasattr(self, "station_sort") else "country"
        def min_freq(station):
            vals = [float(f.get("khz", 0.0)) for f in station.get("frequencies", []) if f.get("khz") is not None]
            return min(vals) if vals else 1e12
        if sort_mode == "service":
            visible.sort(key=lambda s: (s.get("service",""), s.get("country",""), s.get("location","")))
        elif sort_mode == "frequency":
            visible.sort(key=lambda s: (min_freq(s), s.get("country",""), s.get("location","")))
        elif sort_mode == "name":
            visible.sort(key=lambda s: (s.get("location",""), s.get("country","")))
        else:
            visible.sort(key=lambda s: (s.get("country",""), s.get("service",""), s.get("location","")))
        select_row = -1
        for i,station in enumerate(visible):
            fav = "★" if station.get("id") in self.station_favorites else "☆"
            status = station.get("status","active")
            status_mark = " ⛔" if status == "discontinued" else (" ?" if status == "uncertain" else "")
            calls = "/".join(station.get("call_signs",[])) or "—"
            freq_txt = ", ".join(f.get("display","") for f in station.get("frequencies",[])) or self._tr("no_active_frequency_short")
            label = (f"{fav} {station.get('country','')} | {station.get('location','')} [{calls}]{status_mark}\n"
                     f"{station.get('service','')} • {freq_txt}")
            item=QListWidgetItem(label)
            item.setData(Qt.UserRole, station.get("id"))
            self.station_list.addItem(item)
            if station.get("id") == current_id:
                select_row=i
        if self.station_list.count():
            self.station_list.setCurrentRow(select_row if select_row >= 0 else 0)
        else:
            self.station_info.setText(self._tr("no_station_match"))
            self.station_schedule.setRowCount(0)
            self.station_frequency.clear()

    def _current_station(self):
        item = self.station_list.currentItem() if hasattr(self, "station_list") else None
        if item is None:
            return None
        sid=item.data(Qt.UserRole)
        return next((s for s in self.station_catalog if s.get("id")==sid), None)

    def station_selected(self, current, previous=None):
        station = self._current_station()
        if station is None:
            return
        sid=station.get("id")
        fav = sid in self.station_favorites
        self.station_favorite_btn.setText(self._tr("remove_favorite") if fav else self._tr("add_favorite"))
        status=station.get("status","active")
        status_text={
            "active": self._tr("status_active"),
            "uncertain": self._tr("status_uncertain"),
            "discontinued": self._tr("status_discontinued"),
        }.get(status,status)
        calls=", ".join(station.get("call_signs",[])) or "—"
        self.station_info.setText(
            f"<b>{station.get('location','')}</b> — {station.get('country','')}<br>"
            f"{self._tr('service')}: {station.get('service','')}<br>{self._tr('callsign')}: {calls}<br>{self._tr('status')}: {status_text}<br>"
            f"{self._tr('source')}: {self._tr('page_short')} {station.get('source_pages','?')}, {self._tr('updated')} {station.get('source_updated',self._tr('unspecified'))}<br>"
            f"{station.get('notes','')}"
        )
        self.station_frequency.blockSignals(True)
        self.station_frequency.clear()
        for f in station.get("frequencies",[]):
            assigned=float(f.get("khz",0.0)); cat=float(f.get("cat_khz",assigned))
            call=f.get("call_sign","") or calls
            basis=f.get("basis","assigned")
            basis_text=self._tr("assigned_to_usb") if basis=="assigned" else self._tr("listed_carrier")
            label=f"{assigned:g} kHz ({call}) • CAT {cat:g} kHz • {f.get('hours','')} • {basis_text}"
            self.station_frequency.addItem(label, f)
        if self.station_frequency.count()==0:
            self.station_frequency.addItem(self._tr("no_active_frequency"), None)
        self.station_frequency.blockSignals(False)
        self.populate_station_schedule(station)
        self.station_frequency_changed()

    def populate_station_schedule(self, station):
        schedule=station.get("schedule",[])
        self.station_schedule.setSortingEnabled(False)
        self.station_schedule.setRowCount(len(schedule))
        for r,entry in enumerate(schedule):
            self.station_schedule.setItem(r,0,QTableWidgetItem(str(entry.get("utc",""))))
            self.station_schedule.setItem(r,1,QTableWidgetItem(str(entry.get("product",""))))
            self.station_schedule.setItem(r,2,QTableWidgetItem(str(entry.get("rpm_ioc",""))))
        self.station_schedule.setSortingEnabled(True)
        self.station_schedule.sortItems(0, Qt.AscendingOrder)

    def station_frequency_changed(self, _index=None):
        f=self.station_frequency.currentData() if hasattr(self,"station_frequency") else None
        if not f:
            return
        note=f.get("note","")
        if note:
            self.update_status(note)

    def toggle_station_favorite(self):
        station=self._current_station()
        if station is None: return
        sid=station.get("id")
        if sid in self.station_favorites:
            self.station_favorites.remove(sid)
        else:
            self.station_favorites.add(sid)
        self.save_station_favorites()
        self.filter_station_list()

    def copy_station_to_cat_field(self):
        station=self._current_station()
        f=self.station_frequency.currentData() if hasattr(self,"station_frequency") else None
        if station is None or not f:
            return
        cat_khz=float(f.get("cat_khz", f.get("khz",0.0)))
        self.cat_freq.blockSignals(True)
        self.cat_freq.setValue(cat_khz/1000.0)
        self.cat_freq.blockSignals(False)
        # Selecting/copying a station from the library must affect only
        # the CAT frequency. Keep the operator-selected modulation and
        # filter bandwidth unchanged.
        self.cat_freq_dirty=True
        self.cat_status.setText(
            self._tr(
                "cat_prepared_station",
                location=station.get("location", ""),
                listed=float(f.get("khz", 0.0)),
                cat=cat_khz,
            )
        )

    def apply_station_to_cat(self):
        station=self._current_station()
        f=self.station_frequency.currentData() if hasattr(self,"station_frequency") else None
        if station is None or not f:
            return

        # "Tune frequency via CAT" is intentionally frequency-only.
        # A station-library action must never change the operator-selected
        # modulation or filter bandwidth. Those are sent only by the separate
        # CAT mode/filter control.
        cat_khz=float(f.get("cat_khz", f.get("khz", 0.0)))
        self.cat_freq.blockSignals(True)
        self.cat_freq.setValue(cat_khz/1000.0)
        self.cat_freq.blockSignals(False)
        self.cat_freq_dirty=True

        if self.cat is not None and self.cat.connected:
            try:
                self.set_cat_frequency()
                self.cat_status.setText(self._tr(
                    "cat_tuned_station",
                    location=station.get("location", ""),
                    mhz=self.cat_freq.value(),
                ))
            except Exception as exc:
                QMessageBox.warning(self, self._tr("cat_station_title"), str(exc))
        else:
            self.update_status(self._tr(
                "station_prepared",
                location=station.get("location", ""),
                mhz=self.cat_freq.value(),
            ))

    def _change_detection_sensitivity(self, _index: int = -1):
        value = self.sensitivity.currentData() or "Normal"
        if hasattr(self, "autodetector"):
            self.autodetector.set_sensitivity(value)
        if hasattr(self, "auto_status"):
            self.auto_status.setText(self._tr("auto_sensitivity", value=self.sensitivity.currentText()))

    def _settings_bool(self, key: str, default: bool) -> bool:
        value = self.settings.value(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    def _restore_cat_settings(self) -> None:
        """Restore Hamlib path and rig configuration from QSettings."""
        path = str(self.settings.value("cat/hamlib_path", "") or "")
        self.cat_dll.setText(path)

        port = str(self.settings.value("cat/port", "COM3") or "COM3")
        self.cat_port.setCurrentText(port)

        baud = str(self.settings.value("cat/baud", "115200") or "115200")
        idx = self.cat_baud.findText(baud)
        if idx >= 0:
            self.cat_baud.setCurrentIndex(idx)

        vfo = str(self.settings.value("cat/vfo", "Current") or "Current")
        idx = self.cat_vfo.findData(vfo)
        self.cat_vfo.setCurrentIndex(idx if idx >= 0 else 0)

        mode = str(self.settings.value("cat/mode", "USB") or "USB")
        idx = self.cat_radio_mode.findText(mode)
        if idx >= 0:
            self.cat_radio_mode.setCurrentIndex(idx)

        try:
            self.cat_width.setValue(int(self.settings.value("cat/width", -1)))
        except (TypeError, ValueError):
            self.cat_width.setValue(-1)
        try:
            self.cat_freq.setValue(float(self.settings.value("cat/frequency_mhz", 10.100000)))
        except (TypeError, ValueError):
            self.cat_freq.setValue(10.100000)
        self.cat_poll.setChecked(self._settings_bool("cat/poll", True))

        saved_model_id = self.settings.value("cat/model_id", None)
        saved_model_label = str(self.settings.value("cat/model_label", "") or "")
        try:
            saved_model_id = int(saved_model_id) if saved_model_id not in (None, "") else None
        except (TypeError, ValueError):
            saved_model_id = None

        if saved_model_id is not None:
            # Put the stored rig into the control immediately. If Hamlib can be
            # loaded, refresh the full model list and keep this model selected.
            self.cat_model.clear()
            self.cat_model.addItem(saved_model_label or f"Rig [#{saved_model_id}]", saved_model_id)
            loaded = self.load_hamlib_models(silent=True, preferred_model_id=saved_model_id)
        else:
            loaded = None

        self.cat_freq_dirty = False
        if (path or saved_model_id is not None) and loaded is not False:
            self.cat_status.setText(self._tr("cat_settings_restored"))

    def _save_cat_settings(self) -> None:
        """Persist Hamlib path and rig settings for the next program start."""
        if not hasattr(self, "cat_dll"):
            return
        self.settings.setValue("cat/hamlib_path", self.cat_dll.text().strip())
        try:
            model_id = self._selected_cat_model_id()
        except Exception:
            model_id = None
        if model_id is not None:
            self.settings.setValue("cat/model_id", int(model_id))
            self.settings.setValue("cat/model_label", self._selected_cat_model_label())
        self.settings.setValue("cat/port", self.cat_port.currentText().strip())
        self.settings.setValue("cat/baud", self.cat_baud.currentText())
        self.settings.setValue("cat/vfo", self.cat_vfo.currentData() or "Current")
        self.settings.setValue("cat/mode", self.cat_radio_mode.currentText())
        self.settings.setValue("cat/width", self.cat_width.value())
        self.settings.setValue("cat/frequency_mhz", self.cat_freq.value())
        self.settings.setValue("cat/poll", self.cat_poll.isChecked())
        self.settings.sync()

    def browse_hamlib_dll(self):
        # Prefer selecting the whole Hamlib bin directory because libhamlib-4.dll
        # may depend on other DLLs located next to it.
        folder = QFileDialog.getExistingDirectory(self, self._tr("select_hamlib_folder"), "")
        if folder:
            self.cat_dll.setText(folder)
            self.load_hamlib_models(silent=True)
            self._save_cat_settings()
            return
        fn, _ = QFileDialog.getOpenFileName(
            self, self._tr("select_hamlib_dll"), "",
            f"Hamlib DLL (*.dll);;{self._tr('all_files')}"
        )
        if fn:
            self.cat_dll.setText(fn)
            self.load_hamlib_models(silent=True)
            self._save_cat_settings()

    def load_hamlib_models(self, silent: bool = False, preferred_model_id: int | None = None):
        current_id = preferred_model_id if preferred_model_id is not None else self.cat_model.currentData()
        try:
            loader = HamlibRig(self.cat_dll.text().strip() or None)
            models = loader.list_models()
            self.cat_model.blockSignals(True)
            self.cat_model.clear()
            selected_index = -1
            for model_id, mfg, model in models:
                label = f"{mfg} {model}  [#{model_id}]"
                self.cat_model.addItem(label, model_id)
                if current_id is not None and int(current_id) == int(model_id):
                    selected_index = self.cat_model.count() - 1
            if selected_index >= 0:
                self.cat_model.setCurrentIndex(selected_index)
            elif self.cat_model.count() > 0:
                self.cat_model.setCurrentIndex(0)
            self.cat_model.blockSignals(False)
            if self.cat_model.completer() is not None:
                self.cat_model.completer().setCaseSensitivity(Qt.CaseInsensitive)
                self.cat_model.completer().setFilterMode(Qt.MatchContains)
            self.cat_status.setText(self._tr("cat_loaded_models", count=len(models)))
            self._save_cat_settings()
            return True
        except Exception as exc:
            if not silent:
                QMessageBox.warning(self, self._tr("hamlib_models_title"), str(exc))
            self.cat_status.setText(self._tr("cat_model_list_error", error=exc))
            return False

    def _selected_cat_model_id(self) -> int:
        data = self.cat_model.currentData()
        if data is not None:
            return int(data)
        # Fallback for a manually typed string containing [#1234] or a bare ID.
        text = self.cat_model.currentText().strip()
        import re
        m = re.search(r"\[#(\d+)\]", text) or re.fullmatch(r"(\d+)", text)
        if m:
            return int(m.group(1))
        raise ValueError(self._tr("select_radio_model"))

    def _selected_cat_model_label(self) -> str:
        text = self.cat_model.currentText().strip()
        return text.rsplit("  [#", 1)[0].strip() if text else self._tr("radio")

    def _cat_vfo_value(self) -> int:
        name = self.cat_vfo.currentData() or "Current"
        if name == "A":
            return HamlibRig.RIG_VFO_A
        if name == "B":
            return HamlibRig.RIG_VFO_B
        return HamlibRig.RIG_VFO_CURR

    def _cat_freq_edited(self, _text: str):
        self.cat_freq_dirty = True
        if self.cat_last_rig_hz is not None:
            self.cat_status.setText(self._tr("cat_editing_with_rig", mhz=self.cat_last_rig_hz/1e6))
        else:
            self.cat_status.setText(self._tr("cat_editing"))

    def toggle_cat(self):
        if self.cat is not None and self.cat.connected:
            self.disconnect_cat()
            return
        try:
            rig = HamlibRig(self.cat_dll.text().strip() or None)
            rig.connect(
                model_id=self._selected_cat_model_id(),
                rig_pathname=self.cat_port.currentText().strip(),
                serial_speed=int(self.cat_baud.currentText()),
            )
            self.cat = rig
            self.cat_freq_dirty = False
            self.cat_connect_btn.setText(self._tr("disconnect_cat"))
            self.cat_status.setText(self._tr(
                "cat_connected",
                model=self._selected_cat_model_label(),
                path=rig.loaded_path,
            ))
            self.cat_timer.start()
            # Read the actual rig state for the status line, but keep the
            # operator's restored/preselected mode and filter untouched.
            self.read_cat(silent=True, update_controls=False)
            self._save_cat_settings()
        except Exception as exc:
            self.disconnect_cat()
            QMessageBox.critical(self, "CAT / Hamlib", str(exc))

    def disconnect_cat(self):
        if hasattr(self, "cat_timer"):
            self.cat_timer.stop()
        if self.cat is not None:
            try:
                self.cat.disconnect()
            except Exception:
                pass
        self.cat = None
        self.cat_freq_dirty = False
        self.cat_last_rig_hz = None
        if hasattr(self, "cat_connect_btn"):
            self.cat_connect_btn.setText(self._tr("connect_cat"))
        if hasattr(self, "cat_status"):
            self.cat_status.setText(self._tr("cat_disconnected"))

    def read_cat(self, silent: bool = False, update_controls: bool = True):
        if self.cat is None or not self.cat.connected:
            if not silent:
                QMessageBox.information(self, "CAT", self._tr("cat_not_connected"))
            return
        try:
            vfo = self._cat_vfo_value()
            hz = self.cat.get_frequency(vfo)
            self.cat_last_rig_hz = hz
            mode, width = self.cat.get_mode(vfo)
            # Never overwrite a value the user is currently typing or has typed
            # but not yet sent. Polling continues in the background and the
            # actual rig frequency remains visible in CAT status.
            freq_editor_has_focus = self.cat_freq.hasFocus() or self.cat_freq.lineEdit().hasFocus()
            if not self.cat_freq_dirty and not freq_editor_has_focus:
                self.cat_freq.blockSignals(True)
                self.cat_freq.setValue(hz / 1e6)
                self.cat_freq.blockSignals(False)
            # Periodic CAT polling is monitoring only. It must not overwrite
            # the mode/filter values the operator has selected for the next
            # command. Only the explicit "Read rig" action updates these
            # controls from the transceiver.
            if update_controls:
                self.cat_radio_mode.blockSignals(True)
                try:
                    if self.cat_radio_mode.findText(mode) >= 0:
                        self.cat_radio_mode.setCurrentText(mode)
                finally:
                    self.cat_radio_mode.blockSignals(False)

                self.cat_width.blockSignals(True)
                try:
                    if -1 <= width <= 20000:
                        self.cat_width.setValue(width)
                finally:
                    self.cat_width.blockSignals(False)
            if self.cat_freq_dirty:
                pending = self.cat_freq.value()
                self.cat_status.setText(self._tr(
                    "cat_pending",
                    rig=hz/1e6,
                    pending=pending,
                    mode=mode,
                ))
            else:
                self.cat_status.setText(self._tr(
                    "cat_rig_status",
                    model=self._selected_cat_model_label(),
                    mhz=hz/1e6,
                    mode=mode,
                    width=width,
                ))
        except Exception as exc:
            self.cat_status.setText(self._tr("cat_error", error=exc))
            if not silent:
                QMessageBox.warning(self, self._tr("cat_read_title"), str(exc))

    def set_cat_frequency(self):
        if self.cat is None or not self.cat.connected:
            QMessageBox.information(self, "CAT", self._tr("cat_not_connected"))
            return
        try:
            vfo = self._cat_vfo_value()
            hz = self.cat_freq.value() * 1e6
            self.cat.set_frequency(hz, vfo)
            self.cat_freq_dirty = False
            self.cat_status.setText(self._tr("cat_frequency_sent", mhz=hz/1e6))
            self._save_cat_settings()
            QTimer.singleShot(250, lambda: self.read_cat(silent=True, update_controls=False))
        except Exception as exc:
            self.cat_status.setText(self._tr("cat_error", error=exc))
            QMessageBox.warning(self, self._tr("cat_frequency_title"), str(exc))

    def set_cat_mode(self):
        if self.cat is None or not self.cat.connected:
            QMessageBox.information(self, "CAT", self._tr("cat_not_connected"))
            return
        try:
            vfo = self._cat_vfo_value()
            self.cat.set_mode(self.cat_radio_mode.currentText(), self.cat_width.value(), vfo)
            self.cat_status.setText(self._tr("cat_mode_sent", mode=self.cat_radio_mode.currentText()))
            self._save_cat_settings()
            QTimer.singleShot(250, lambda: self.read_cat(silent=True, update_controls=False))
        except Exception as exc:
            self.cat_status.setText(self._tr("cat_error", error=exc))
            QMessageBox.warning(self, self._tr("cat_mode_title"), str(exc))

    def set_cat(self):
        # Backward-compatible helper: send both when called from older code.
        self.set_cat_frequency()
        if self.cat is not None and self.cat.connected:
            self.set_cat_mode()

    def poll_cat(self):
        if self.cat_poll.isChecked() and self.cat is not None and self.cat.connected:
            # Polling reports the real rig state in CAT status only. Manual
            # selections in Mode and Filter are never overwritten here.
            self.read_cat(silent=True, update_controls=False)

    def default_autosave_dir(self) -> Path:
        base = Path.home() / "Pictures" / "HFWeatherFax"
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            base = Path.cwd() / "HFWeatherFax_Autosave"
            base.mkdir(parents=True, exist_ok=True)
        return base

    def autosave_dir(self) -> Path:
        txt = self.auto_save_folder.text().strip() if hasattr(self, "auto_save_folder") else ""
        path = Path(txt) if txt else self.default_autosave_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def choose_autosave_folder(self):
        start = str(self.autosave_dir())
        folder = QFileDialog.getExistingDirectory(self, self._tr("choose_autosave_folder"), start)
        if folder:
            self.auto_save_folder.setText(folder)

    def refresh_devices(self):
        self.device.clear()
        try:
            devices = LiveAudioInput.input_devices()
            for idx, name in devices:
                self.device.addItem(f"{idx}: {name}", idx)
            if not devices:
                self.device.addItem(self._tr("no_input_devices"))
        except Exception as e:
            self.device.addItem(self._tr("audio_error", error=e))

    def configure_decoder(self, sample_rate: int):
        self.decoder = FaxImageDecoder(
            sample_rate=sample_rate,
            lpm=int(self.lpm.currentText()),
            ioc=int(self.ioc.currentText()),
        )
        self.decoder.set_phase_percent(self.phase.value())
        self.decoder.set_slant_ppm(self.slant.value())
        if hasattr(self, "slant_estimator"):
            self.slant_estimator.reset(self.decoder.width)

    def start_live(self):
        self.stop_all()
        try:
            sr = int(self.rate.currentText())
            dev = self.device.currentData()
            self.configure_decoder(sr)
            self.autodetector = WefaxAutoDetector(sr, self.sensitivity.currentData() or "Normal")
            self.slant_estimator = AutoSlantEstimator(self.decoder.width)
            self.audio = LiveAudioInput(device=dev, sample_rate=sr)
            self.audio.start()
            self.wav_data = None
            self.current_source_name = "live"
            self.last_autosave_signature = None
            self.timer.start()
            self.update_status(self._tr(
                "live_receiving",
                sr=sr,
                lpm=self.lpm.currentText(),
                ioc=self.ioc.currentText(),
            ))
        except Exception as e:
            QMessageBox.critical(self, self._tr("audio_input_error"), str(e))
            self.stop_all()

    def open_audio_file(self):
        fn, _ = QFileDialog.getOpenFileName(
            self, self._tr("open_audio_title"), "", self._tr("open_audio_filter")
        )
        if not fn:
            return
        self.stop_all()
        try:
            data, sr = sf.read(fn, dtype="float32", always_2d=True)
            self.wav_data = data[:, 0]
            self.wav_pos = 0
            self.wav_rate = int(sr)
            self.current_source_name = Path(fn).stem
            self.last_autosave_signature = None
            self.configure_decoder(self.wav_rate)
            self.autodetector = WefaxAutoDetector(self.wav_rate, self.sensitivity.currentData() or "Normal")
            self.slant_estimator = AutoSlantEstimator(self.decoder.width)
            self.timer.start()
            self.update_status(self._tr("decoding_file", name=Path(fn).name, sr=sr))
        except Exception as e:
            QMessageBox.critical(self, self._tr("open_audio_error"), str(e))

    def stop_all(self):
        self.timer.stop()
        if self.audio is not None:
            try:
                self.audio.stop()
            except Exception:
                pass
            self.audio = None
        self.wav_data = None
        self.wav_pos = 0
        if hasattr(self, "spectrum"):
            self.spectrum.reset()
            self.spectrum_info.setText(self._tr("spectrum_expected"))
        if hasattr(self, "autodetector"):
            self.autodetector.reset()
        if hasattr(self, "auto_status"):
            self.auto_status.setText(self._tr("auto_waiting"))
        if hasattr(self, "phasing_quality"):
            self._set_lock_quality(self.phasing_quality_label, self.phasing_quality, 0.0, "phasing_lock")
        if hasattr(self, "hsync_quality"):
            self._set_lock_quality(self.hsync_quality_label, self.hsync_quality, 0.0, "hsync_lock")
        if hasattr(self, "slant_estimator"):
            self.slant_estimator.reset(self.decoder.width)
        if hasattr(self, "auto_slant_status"):
            self.auto_slant_status.setText(self._tr("auto_slant_measuring"))
        if hasattr(self, "level_cal_status"):
            self.level_cal_status.setText(self._tr("levels_nominal"))
        self.auto_realign_done = False

    def clear_image(self):
        self.image_lines.clear()
        self.image_generation += 1
        self.last_autosave_signature = None
        self.image_shift_px = 0
        self.auto_realign_done = False
        if hasattr(self, "line_start_status"):
            self.line_start_status.setText(self._tr("line_start_not_evaluated"))
        self.decoder.reset()
        if hasattr(self, "slant_estimator"):
            self.slant_estimator.reset(self.decoder.width)
        if hasattr(self, "auto_slant_status"):
            self.auto_slant_status.setText(self._tr("auto_slant_measuring"))
        self.image_label.clear()
        self.image_label.setText(self._tr("no_image"))

    def set_invert(self, checked: bool):
        self.invert = bool(checked)
        self.render_image()

    def process_audio(self):
        self.decoder.set_phase_percent(self.phase.value())
        if not self.auto_slant.isChecked():
            self.decoder.set_slant_ppm(self.slant.value())

        chunks = []
        if self.audio is not None:
            for _ in range(8):
                try:
                    chunks.append(self.audio.queue.get_nowait())
                except Exception:
                    break
            self.level.setText(self._tr("level_value", db=self.audio.last_level_db))
        elif self.wav_data is not None:
            # Faster than real-time file decoding while keeping GUI responsive.
            n = int(self.wav_rate * 1.5)
            if self.wav_pos < len(self.wav_data):
                chunks.append(self.wav_data[self.wav_pos:self.wav_pos+n])
                self.wav_pos += n
            else:
                self.timer.stop()
                saved = self.auto_save_image(reason="file_end")
                if saved is not None:
                    self.update_status(self._tr("file_finished_saved", name=saved.name))
                else:
                    self.update_status(self._tr("file_finished"))

        added = 0
        if chunks:
            spectrum_audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            current_sr = self.audio.sample_rate if self.audio is not None else self.wav_rate
            self.spectrum.update_audio(spectrum_audio, current_sr)
            peak = "--" if self.spectrum.peak_hz is None else f"{self.spectrum.peak_hz:.0f} Hz"
            if self.auto_detect.isChecked() and self.autodetector.state == "RECEIVING":
                tuning = self._tr("image_active_tuning_disabled")
            else:
                tuning = self._spectrum_tuning_text()
            self.spectrum_info.setText(self._tr("spectrum_live", peak=peak, tuning=tuning))

        for chunk in chunks:
            decode_this_chunk = True
            locked_this_chunk = False

            if self.auto_detect.isChecked():
                events = self.autodetector.push_audio(chunk)
                for event in events:
                    if event.kind in ("START", "RESTART"):
                        self.spectrum.mark_event("START")
                        self.spectrum.mark_event("PHASING")

                        # A RESTART is a newly detected 300 Hz START while an
                        # image is already being received. This is deliberately
                        # handled even when the previous 450 Hz STOP was missed:
                        # finalize the old fax, clear it and wait for new phasing.
                        saved = None
                        if self.image_lines:
                            saved = self.auto_save_image(reason="new_start")

                        if self.auto_clear.isChecked():
                            self.clear_image()
                        else:
                            self.decoder.reset()

                        self._set_lock_quality(self.phasing_quality_label, self.phasing_quality, 0.0, "phasing_lock")
                        self._set_lock_quality(self.hsync_quality_label, self.hsync_quality, 0.0, "hsync_lock")
                        if event.kind == "RESTART":
                            if saved is not None:
                                self.update_status(self._tr("auto_restart_saved", name=saved.name))
                            else:
                                self.update_status(self._tr("auto_restart_detected"))
                        else:
                            self.update_status(self._tr("auto_start_detected"))

                    elif event.kind == "LOCK" and event.lpm is not None:
                        self.spectrum.mark_event("IMAGE")
                        self.auto_realign_done = False
                        self.lpm.setCurrentText(str(event.lpm))
                        self.configure_decoder(current_sr)
                        if self.auto_levels.isChecked() and event.black_hz is not None and event.white_hz is not None:
                            self.decoder.set_levels(event.black_hz, event.white_hz)
                            lc = 0.0 if event.level_confidence is None else event.level_confidence
                            self.level_cal_status.setText(self._tr(
                                "levels_measured",
                                black=event.black_hz,
                                white=event.white_hz,
                                conf=lc,
                            ))
                        else:
                            self.level_cal_status.setText(self._tr("levels_nominal"))
                        if self.auto_hsync.isChecked() and event.sync_skip is not None:
                            self.decoder.set_initial_sync(event.sync_skip)
                        conf = "" if event.confidence is None else f" ({event.confidence:.0%})"
                        self._set_lock_quality(
                            self.phasing_quality_label, self.phasing_quality,
                            event.confidence or 0.0, "phasing_lock"
                        )
                        self._set_lock_quality(
                            self.hsync_quality_label, self.hsync_quality,
                            event.hsync_confidence or 0.0, "hsync_lock"
                        )
                        sync_txt = ""
                        if self.auto_hsync.isChecked() and event.phase_percent is not None:
                            sync_txt = self._tr("hsync_suffix", phase=event.phase_percent)
                        self.update_status(self._tr(
                            "auto_locked",
                            lpm=event.lpm,
                            conf=conf,
                            sync=sync_txt,
                        ))
                        locked_this_chunk = True

                    elif event.kind == "STOP":
                        self.spectrum.mark_event("STOP")
                        saved = self.auto_save_image(reason="stop")
                        self.decoder.reset()
                        self._set_lock_quality(self.phasing_quality_label, self.phasing_quality, 0.0, "phasing_lock")
                        self._set_lock_quality(self.hsync_quality_label, self.hsync_quality, 0.0, "hsync_lock")
                        if saved is not None:
                            self.update_status(self._tr(
                                "auto_stop_saved",
                                hz=self.autodetector.stop_target_hz,
                                name=saved.name,
                            ))
                        else:
                            self.update_status(self._tr(
                                "auto_stop_waiting",
                                hz=self.autodetector.stop_target_hz,
                            ))

                self.auto_status.setText(self._auto_status_text())
                if self.autodetector.state in ("WAIT_START", "PHASING"):
                    self._set_lock_quality(
                        self.phasing_quality_label, self.phasing_quality,
                        self.autodetector.last_phasing_confidence, "phasing_lock"
                    )
                decode_this_chunk = (self.autodetector.state == "RECEIVING") and (not locked_this_chunk)

            if decode_this_chunk:
                new_lines = self.decoder.push_audio(chunk)
                if new_lines:
                    self.image_lines.extend(new_lines)
                    added += len(new_lines)
                    if self.auto_slant.isChecked():
                        ppm, slant_conf = self.slant_estimator.push_lines(new_lines)
                        self.auto_slant_status.setText(self._slant_status_text())
                        if ppm is not None and slant_conf >= 0.48:
                            # Limit each control-loop step to avoid visible raster jumps.
                            current = float(self.decoder.slant_ppm)
                            target = float(ppm)
                            step = float(np.clip(target - current, -120.0, 120.0))
                            applied = float(np.clip(current + step, -5000.0, 5000.0))
                            self.decoder.set_slant_ppm(applied)
                            self.slant.blockSignals(True)
                            self.slant.setValue(applied)
                            self.slant.blockSignals(False)

        if added:
            if self.auto_line_start.isChecked() and (not self.auto_realign_done) and len(self.image_lines) >= 60:
                shift, conf, seam = estimate_wrap_shift(self.image_lines)
                if shift is not None:
                    if conf >= 0.58:
                        self.image_shift_px = int(shift)
                        self.auto_realign_done = True
                        pct = 100.0 * self.image_shift_px / max(1, self.decoder.width)
                        self.line_start_status.setText(self._tr("line_auto_applied", pct=pct, conf=conf))
                    elif len(self.image_lines) >= 140:
                        self.auto_realign_done = True
                        self.line_start_status.setText(self._tr("line_no_seam", conf=conf))
            self.render_image()
            if self.auto_detect.isChecked():
                lpm = self.autodetector.detected_lpm or self.lpm.currentText()
                self.update_status(self._tr("receiving_lpm", lpm=lpm, lines=len(self.image_lines)))
            else:
                self.update_status(self._tr("receiving", lines=len(self.image_lines)))

    def nudge_image(self, percent: float):
        if not self.image_lines:
            return
        width = int(self.image_lines[0].size)
        delta = int(round(width * float(percent) / 100.0))
        self.image_shift_px = int((self.image_shift_px + delta) % width)
        if self.image_shift_px > width // 2:
            self.image_shift_px -= width
        self.auto_realign_done = True
        pct = 100.0 * self.image_shift_px / max(1, width)
        self.line_start_status.setText(self._tr("line_manual_shift", pct=pct))
        self.render_image()

    def realign_image(self):
        if len(self.image_lines) < 24:
            self.line_start_status.setText(self._tr("line_need_24"))
            return
        shift, conf, seam = estimate_wrap_shift(self.image_lines)
        if shift is None:
            self.line_start_status.setText(self._tr("line_could_not_estimate"))
            return
        self.image_shift_px = int(shift)
        self.auto_realign_done = True
        pct = 100.0 * self.image_shift_px / max(1, self.decoder.width)
        self.line_start_status.setText(self._tr("line_re_aligned", pct=pct, conf=conf))
        self.render_image()

    def current_image_array(self):
        if not self.image_lines:
            return None
        img = np.vstack(self.image_lines).astype(np.uint8, copy=False)
        if self.image_shift_px:
            img = np.roll(img, int(self.image_shift_px), axis=1)
        if self.invert:
            img = 255 - img
        return np.ascontiguousarray(img)

    def render_image(self):
        img = self.current_image_array()
        if img is None:
            return
        h, w = img.shape
        qimg = QImage(img.data, w, h, img.strides[0], QImage.Format_Grayscale8).copy()
        pix = QPixmap.fromImage(qimg)
        # Display at a manageable width while preserving the saved full raster.
        display_w = min(1200, max(500, self.scroll.viewport().width() - 30))
        if pix.width() > display_w:
            pix = pix.scaledToWidth(display_w, Qt.SmoothTransformation)
        self.image_label.setPixmap(pix)
        self.image_label.adjustSize()

    def _write_png(self, path: Path, img: np.ndarray) -> bool:
        h, w = img.shape
        qimg = QImage(img.data, w, h, img.strides[0], QImage.Format_Grayscale8).copy()
        return bool(qimg.save(str(path), "PNG"))

    def auto_save_image(self, reason: str = "auto") -> Path | None:
        if not self.auto_save.isChecked():
            return None
        img = self.current_image_array()
        if img is None or img.shape[0] < 40 or img.shape[1] < 100:
            return None
        signature = (
            self.image_generation, len(self.image_lines), img.shape[1], img.shape[0],
            int(self.image_shift_px), bool(self.invert),
        )
        if signature == self.last_autosave_signature:
            return None
        out_dir = self.autosave_dir()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        source = ''.join(c if c.isalnum() or c in ('-','_') else '_' for c in self.current_source_name)[:40] or 'weatherfax'
        lpm = self.autodetector.detected_lpm or self.lpm.currentText()
        name = f"{ts}_{source}_{reason}_{lpm}lpm_{len(self.image_lines):04d}lines.png"
        path = out_dir / name
        idx = 1
        while path.exists():
            path = out_dir / f"{path.stem}_{idx}{path.suffix}"
            idx += 1
        if self._write_png(path, img):
            self.last_autosave_signature = signature
            return path
        return None

    def save_png(self):
        img = self.current_image_array()
        if img is None:
            QMessageBox.information(self, self._tr("save_title"), self._tr("no_decoded_image"))
            return
        fn, _ = QFileDialog.getSaveFileName(self, self._tr("save_image_title"), "weatherfax.png", "PNG (*.png)")
        if not fn:
            return
        ok = self._write_png(Path(fn), img)
        if not ok:
            QMessageBox.warning(self, self._tr("save_title"), self._tr("save_failed"))
        else:
            self.update_status(self._tr("saved", path=fn))

    def _set_lock_quality(self, label: QLabel, bar: QProgressBar, confidence: float, name_key: str):
        confidence = float(np.clip(confidence, 0.0, 1.0))
        pct = int(round(confidence * 100.0))
        if confidence >= 0.70:
            state = self._tr("locked")
        elif confidence >= 0.45:
            state = self._tr("marginal")
        else:
            state = self._tr("no_lock")
        label.setText(f"{self._tr(name_key)}: {state}")
        bar.setValue(pct)
        bar.setFormat(f"{pct}%")

    def update_status(self, text: str):
        self.status.setText(text)

    def closeEvent(self, event):
        self._save_cat_settings()
        self.stop_all()
        self.disconnect_cat()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("OK5TVR")
    icon_path = resource_path("assets", "hfweatherfax_ok5tvr.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.{__version__}")
        except Exception:
            pass
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
