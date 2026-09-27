from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from pathlib import Path


class _RigCapsHead(ctypes.Structure):
    _fields_ = [
        ("rig_model", ctypes.c_int),
        ("model_name", ctypes.c_char_p),
        ("mfg_name", ctypes.c_char_p),
    ]

class _HamlibValue(ctypes.Union):
    """ctypes equivalent of Hamlib value_t."""

    _fields_ = [
        ("i", ctypes.c_int),
        ("f", ctypes.c_float),
        ("s", ctypes.c_char_p),
        ("cs", ctypes.c_char_p),
    ]

class HamlibError(RuntimeError):
    pass


class HamlibRig:
    """Direct Hamlib C-API wrapper. No rigctld process is used."""

    RIG_VFO_NONE = 0
    RIG_VFO_A = 1 << 0
    RIG_VFO_B = 1 << 1
    RIG_VFO_CURR = 1 << 29

    # Hamlib rig_level_e: calibrated receive signal strength in dB relative to S9.
    RIG_LEVEL_STRENGTH = 1 << 30

    MODE_TO_VALUE = {
        "AM": 1 << 0,
        "CW": 1 << 1,
        "USB": 1 << 2,
        "LSB": 1 << 3,
        "RTTY": 1 << 4,
        "FM": 1 << 5,
        "CWR": 1 << 7,
        "RTTYR": 1 << 8,
        "PKTLSB": 1 << 10,
        "PKTUSB": 1 << 11,
        "PKTFM": 1 << 12,
        "FAX": 1 << 15,
    }
    VALUE_TO_MODE = {v: k for k, v in MODE_TO_VALUE.items()}

    DLL_NAMES = ("libhamlib-4.dll", "hamlib-4.dll", "libhamlib.dll", "Hamlib-4.dll")
    PRELOAD_NAMES = (
        "libwinpthread-1.dll",
        "libusb-1.0.dll",
        "libgcc_s_seh-1.dll",
        "libgcc_s_sjlj-1.dll",
        "libgcc_s_dw2-1.dll",
        "libstdc++-6.dll",
        "zlib1.dll",
        "libintl-8.dll",
        "libiconv-2.dll",
    )

    def __init__(self, dll_path: str | None = None):
        self.dll_path = dll_path or ""
        self.lib = None
        self.rig = None
        self.connected = False
        self._dll_dir_handles = []
        self.loaded_path = ""
        self.loaded_dir = ""
        self._load_library()
        self._bind_api()

    @staticmethod
    def _app_roots() -> list[Path]:
        roots: list[Path] = []
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except Exception:
            pass
        try:
            roots.append(Path.cwd())
        except Exception:
            pass
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            roots.append(Path(getattr(sys, "_MEIPASS")))
        return roots

    @classmethod
    def _common_windows_roots(cls) -> list[Path]:
        roots: list[Path] = []
        if os.name != "nt":
            return roots
        for envname in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            value = os.environ.get(envname)
            if not value:
                continue
            base = Path(value)
            roots.extend([
                base / "Hamlib",
                base / "hamlib",
                base / "hamlib-w64-4.7.2",
                base / "hamlib-w64-4.7.1",
                base / "hamlib-w64-4.7.0",
            ])
        return roots

    @classmethod
    def candidate_paths(cls, explicit: str | None = None) -> list[str]:
        out: list[str] = []

        def add_dir(d: Path):
            for name in cls.DLL_NAMES:
                out.append(str(d / name))
                out.append(str(d / "bin" / name))

        if explicit:
            p = Path(explicit)
            if p.is_dir():
                add_dir(p)
            else:
                out.append(str(p))
                if p.parent:
                    for name in cls.DLL_NAMES:
                        out.append(str(p.parent / name))

        env = os.environ.get("HAMLIB_DLL")
        if env:
            p = Path(env)
            if p.is_dir():
                add_dir(p)
            else:
                out.append(str(p))

        for root in cls._app_roots():
            add_dir(root)
            add_dir(root / "hamlib")

        for root in cls._common_windows_roots():
            add_dir(root)

        found = ctypes.util.find_library("hamlib")
        if found:
            out.append(found)
        out.extend(cls.DLL_NAMES)

        unique: list[str] = []
        seen = set()
        for x in out:
            if x and x not in seen:
                seen.add(x)
                unique.append(x)
        return unique

    def _prepare_dll_directory(self, directory: Path):
        if os.name != "nt" or not directory.exists():
            return
        d = str(directory.resolve())
        # Python 3.8+ uses a restricted DLL search path. Add the full Hamlib bin
        # directory so transitive MinGW/libusb dependencies are resolvable.
        if hasattr(os, "add_dll_directory"):
            try:
                self._dll_dir_handles.append(os.add_dll_directory(d))
            except Exception:
                pass
        old_path = os.environ.get("PATH", "")
        if d.lower() not in [x.lower() for x in old_path.split(os.pathsep) if x]:
            os.environ["PATH"] = d + os.pathsep + old_path

        # Preload known dependencies when present. This gives Windows another
        # chance to resolve them before loading libhamlib itself.
        for name in self.PRELOAD_NAMES:
            dep = directory / name
            if dep.exists():
                try:
                    ctypes.CDLL(str(dep))
                except Exception:
                    pass

    def _load_library(self):
        errors: list[str] = []
        existing_candidates: list[str] = []
        for candidate in self.candidate_paths(self.dll_path):
            try:
                p = Path(candidate)
                if p.is_absolute() and p.exists():
                    existing_candidates.append(str(p))
                    self._prepare_dll_directory(p.parent)
                    load_target = str(p)
                else:
                    load_target = candidate
                self.lib = ctypes.CDLL(load_target)
                self.loaded_path = load_target
                try:
                    self.loaded_dir = str(Path(load_target).resolve().parent)
                except Exception:
                    self.loaded_dir = ""
                return
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")

        if existing_candidates:
            hint = (
                "Hamlib DLL was found, but Windows could not load it. This usually means a dependent DLL is missing "
                "or the Hamlib package architecture does not match HFWeatherFax.\n\n"
                "Select the full 64-bit Hamlib BIN folder, not a copied libhamlib-4.dll by itself. Keep all DLL files "
                "from the official Hamlib bin directory together.\n"
            )
        else:
            hint = (
                "Hamlib was not found. Direct CAT does not require rigctld, but it still requires the Hamlib shared library.\n\n"
                "Install/extract the 64-bit Hamlib Windows package, then select its BIN folder or libhamlib-4.dll.\n"
            )
        raise HamlibError(hint + "\nLast load attempts:\n" + "\n".join(errors[-5:]))

    def _bind_api(self):
        L = self.lib
        if L is None:
            raise HamlibError("Hamlib DLL is not loaded")

        L.rig_init.argtypes = [ctypes.c_int]
        L.rig_init.restype = ctypes.c_void_p
        L.rig_cleanup.argtypes = [ctypes.c_void_p]
        L.rig_cleanup.restype = ctypes.c_int
        L.rig_open.argtypes = [ctypes.c_void_p]
        L.rig_open.restype = ctypes.c_int
        L.rig_close.argtypes = [ctypes.c_void_p]
        L.rig_close.restype = ctypes.c_int
        L.rig_token_lookup.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.rig_token_lookup.restype = ctypes.c_long
        L.rig_set_conf.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_char_p]
        L.rig_set_conf.restype = ctypes.c_int
        L.rig_set_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_double]
        L.rig_set_freq.restype = ctypes.c_int
        L.rig_get_freq.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_double)]
        L.rig_get_freq.restype = ctypes.c_int
        L.rig_set_mode.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_long]
        L.rig_set_mode.restype = ctypes.c_int
        L.rig_get_mode.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_long)]
        L.rig_get_mode.restype = ctypes.c_int
        if hasattr(L, "rig_get_level"):
            L.rig_get_level.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_ulong, ctypes.POINTER(_HamlibValue)
            ]
            L.rig_get_level.restype = ctypes.c_int
        L.rigerror.argtypes = [ctypes.c_int]
        L.rigerror.restype = ctypes.c_char_p

        if hasattr(L, "rig_load_all_backends"):
            L.rig_load_all_backends.argtypes = []
            L.rig_load_all_backends.restype = ctypes.c_int
            try:
                L.rig_load_all_backends()
            except Exception:
                pass

        if hasattr(L, "rig_list_foreach"):
            self._rig_list_cb_type = ctypes.CFUNCTYPE(
                ctypes.c_int, ctypes.POINTER(_RigCapsHead), ctypes.c_void_p
            )
            L.rig_list_foreach.argtypes = [self._rig_list_cb_type, ctypes.c_void_p]
            L.rig_list_foreach.restype = ctypes.c_int

    def list_models(self) -> list[tuple[int, str, str]]:
        """Return [(model_id, manufacturer, model_name), ...] from loaded Hamlib."""
        if self.lib is None or not hasattr(self.lib, "rig_list_foreach"):
            raise HamlibError("This Hamlib DLL does not export rig_list_foreach")

        models: list[tuple[int, str, str]] = []

        def _decode(value) -> str:
            if not value:
                return ""
            try:
                return value.decode("utf-8", "replace")
            except Exception:
                return str(value)

        @self._rig_list_cb_type
        def callback(caps_ptr, _data):
            if not caps_ptr:
                return 1
            try:
                caps = caps_ptr.contents
                model_id = int(caps.rig_model)
                model = _decode(caps.model_name).strip()
                mfg = _decode(caps.mfg_name).strip()
                if model_id > 0 and model:
                    models.append((model_id, mfg or "Unknown", model))
            except Exception:
                pass
            return 1

        rc = self.lib.rig_list_foreach(callback, None)
        if int(rc) not in (0,):
            # Some Hamlib versions return a callback-related nonzero value after
            # traversal; if we collected models, the list is still usable.
            if not models:
                self._check(int(rc), "list Hamlib models")

        # De-duplicate while preserving the current Hamlib backend's names.
        unique: dict[int, tuple[int, str, str]] = {}
        for item in models:
            unique[item[0]] = item
        return sorted(unique.values(), key=lambda x: (x[1].casefold(), x[2].casefold(), x[0]))

    def model_name(self, model_id: int) -> str:
        for mid, mfg, model in self.list_models():
            if mid == int(model_id):
                return f"{mfg} {model}".strip()
        return f"Hamlib model #{int(model_id)}"

    def error_text(self, rc: int) -> str:
        if rc == 0:
            return "OK"
        try:
            raw = self.lib.rigerror(int(rc))
            return raw.decode("utf-8", "replace") if raw else f"Hamlib error {rc}"
        except Exception:
            return f"Hamlib error {rc}"

    def _check(self, rc: int, action: str):
        if int(rc) != 0:
            raise HamlibError(f"{action}: {self.error_text(int(rc))} ({rc})")

    def _set_conf(self, key: str, value: str, required: bool = False) -> bool:
        token = int(self.lib.rig_token_lookup(self.rig, key.encode("ascii")))
        if token == 0:
            if required:
                raise HamlibError(f"Hamlib configuration token '{key}' is not supported")
            return False
        rc = self.lib.rig_set_conf(self.rig, token, str(value).encode("utf-8"))
        self._check(rc, f"set {key}")
        return True

    def connect(self, model_id: int, rig_pathname: str, serial_speed: int | None = None, extra_conf: dict[str, str] | None = None):
        self.disconnect()
        self.rig = self.lib.rig_init(int(model_id))
        if not self.rig:
            raise HamlibError(f"Unknown or unavailable Hamlib model ID {model_id}")
        try:
            self._set_conf("rig_pathname", rig_pathname, required=True)
            if serial_speed:
                self._set_conf("serial_speed", str(int(serial_speed)), required=False)
            for key, value in (extra_conf or {}).items():
                if key.strip():
                    self._set_conf(key.strip(), value, required=False)
            rc = self.lib.rig_open(self.rig)
            self._check(rc, "rig_open")
            self.connected = True
        except Exception:
            try:
                self.lib.rig_cleanup(self.rig)
            except Exception:
                pass
            self.rig = None
            self.connected = False
            raise

    def disconnect(self):
        if self.rig:
            try:
                if self.connected:
                    self.lib.rig_close(self.rig)
            finally:
                try:
                    self.lib.rig_cleanup(self.rig)
                finally:
                    self.rig = None
                    self.connected = False

    def _require(self):
        if not self.rig or not self.connected:
            raise HamlibError("CAT is not connected")

    def get_frequency(self, vfo: int | None = None) -> float:
        self._require()
        value = ctypes.c_double()
        rc = self.lib.rig_get_freq(self.rig, int(vfo or self.RIG_VFO_CURR), ctypes.byref(value))
        self._check(rc, "get frequency")
        return float(value.value)

    def set_frequency(self, hz: float, vfo: int | None = None):
        self._require()
        rc = self.lib.rig_set_freq(self.rig, int(vfo or self.RIG_VFO_CURR), float(hz))
        self._check(rc, "set frequency")

    def get_mode(self, vfo: int | None = None) -> tuple[str, int]:
        self._require()
        mode = ctypes.c_int()
        width = ctypes.c_long()
        rc = self.lib.rig_get_mode(self.rig, int(vfo or self.RIG_VFO_CURR), ctypes.byref(mode), ctypes.byref(width))
        self._check(rc, "get mode")
        return self.VALUE_TO_MODE.get(int(mode.value), f"0x{int(mode.value):x}"), int(width.value)

    def set_mode(self, mode_name: str, width_hz: int = -1, vfo: int | None = None):
        self._require()
        key = mode_name.upper().strip()
        if key not in self.MODE_TO_VALUE:
            raise HamlibError(f"Unsupported mode name: {mode_name}")
        rc = self.lib.rig_set_mode(
            self.rig,
            int(vfo or self.RIG_VFO_CURR),
            int(self.MODE_TO_VALUE[key]),
            int(width_hz),
        )
        self._check(rc, "set mode")

    def get_signal_strength_db(self, vfo: int | None = None) -> int | None:
        """Return calibrated RX strength in dB relative to S9, or None if unsupported.

        Hamlib defines RIG_LEVEL_STRENGTH as an integer dB value relative to S9
        (S9 == 0 dB). Some backends do not expose an S-meter; that is not a
        fatal CAT error for HFWeatherFax, so unsupported/error returns None.
        """
        self._require()
        if not hasattr(self.lib, "rig_get_level"):
            return None
        value = _HamlibValue()
        rc = self.lib.rig_get_level(
            self.rig,
            int(vfo or self.RIG_VFO_CURR),
            ctypes.c_ulong(self.RIG_LEVEL_STRENGTH),
            ctypes.byref(value),
        )
        if int(rc) != 0:
            return None
        return int(value.i)

    def __del__(self):
        try:
            self.disconnect()
        except Exception:
            pass
