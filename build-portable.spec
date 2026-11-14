# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成文件夹版，Python 运行时随应用一起分发。

文件夹结构：
  ModMigrator-Portable/
    ModMigrator.exe         启动器
    _internal/              Python 3.12 运行时、Tcl/Tk、Pillow 与标准库
    使用说明.md
"""

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
    [],
    exclude_binaries=True,
    name="ModMigrator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ModMigrator-Portable",
)
