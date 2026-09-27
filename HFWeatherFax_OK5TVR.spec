# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project = Path(SPECPATH)

datas = [
    (str(project / "hfweatherfax" / "station_catalog.json"), "hfweatherfax"),
    (str(project / "assets"), "assets"),
]
binaries = []
hiddenimports = []

# Preserve package metadata/binaries that are easy to miss in audio packages.
for package in ("sounddevice", "soundfile"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Hamlib is optional for source runs but is bundled into official Windows builds.
hamlib_bin = project / "hamlib" / "bin"
if hamlib_bin.exists():
    for dll in sorted(hamlib_bin.glob("*.dll")):
        binaries.append((str(dll), "hamlib"))


a = Analysis(
    [str(project / "main.py")],
    pathex=[str(project)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HFWeatherFax_OK5TVR",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project / "assets" / "hfweatherfax_ok5tvr.ico"),
    version=str(project / "version_info.txt"),
)
