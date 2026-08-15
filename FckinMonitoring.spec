# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['hardware_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],   # <-- bundel folder assets/icon.png ke exe
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FckinMonitoring',
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
    icon='compile\\icon.ico',       # <-- icon exe (Explorer, taskbar, shortcut)
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='FckinMonitoring',
)