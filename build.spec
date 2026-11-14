# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成单文件、无控制台窗口的 ModMigrator.exe。"""

from pathlib import Path

ROOT = Path(SPECPATH)
ASSETS = ROOT / "assets"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ASSETS / "icon.ico"), "assets"),
        (str(ASSETS / "icon.png"), "assets"),
        (str(ASSETS / "icon64.png"), "assets"),
    ],
    hiddenimports=["PIL._tkinter_finder"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "pandas", "matplotlib"],
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
    name="ModMigrator",
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
    icon=str(ASSETS / "icon.ico"),
)
