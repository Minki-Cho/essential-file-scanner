# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(SPECPATH)
asset_files = []
assets_dir = project_root / "assets"
if assets_dir.is_dir():
    asset_files.append((str(assets_dir), "assets"))

a = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=asset_files,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Qt for Windows uses the ICU implementation supplied by the operating system.
# A build shell can nevertheless have an unrelated ``icuuc.dll`` on PATH (for
# example, from Poppler), and PyInstaller may collect that foreign DLL while
# resolving Qt6Core. Bundling it makes QtCore fail with ERROR_PROC_NOT_FOUND on
# otherwise healthy Windows installations. Keep ICU only when it actually came
# from the PySide6 distribution itself.
foreign_icu_names = {"icuuc.dll", "icuin.dll", "icudt.dll"}


def is_foreign_icu(entry):
    destination, source, _type_code = entry
    name = Path(destination).name.lower()
    is_icu = name in foreign_icu_names or name.startswith("icudt")
    return is_icu and "pyside6" not in str(source).lower()


a.binaries = [entry for entry in a.binaries if not is_foreign_icu(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="RequiredFileScanner",
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
)
