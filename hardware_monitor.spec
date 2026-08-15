# -*- mode: python ; coding: utf-8 -*-
# =====================================================================
# Build:
#   pyinstaller hardware_monitor.spec --distpath compile --workpath build --noconfirm
# =====================================================================
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# --- Auto-generate compile/icon.ico dari assets/icon.png (butuh exe .ico) ---
ICON_PNG = os.path.join('assets', 'icon.png')
ICON_ICO = os.path.join('compile', 'icon.ico')
if not os.path.exists(ICON_ICO) and os.path.exists(ICON_PNG):
    try:
        from PIL import Image
        os.makedirs('compile', exist_ok=True)
        img = Image.open(ICON_PNG).convert("RGBA")
        img.save(ICON_ICO, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    except Exception as e:
        print(f"[!] Gagal membuat icon.ico ({e}). Icon exe akan pakai default.")

a = Analysis(
    ['hardware_monitor.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/icon.png', 'assets')],
    hiddenimports=collect_submodules('wmi') + ['win32timezone', 'pythoncom'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FckinMonitoring',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,               # tidak ada console (background app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_ICO if os.path.exists(ICON_ICO) else None,
)
