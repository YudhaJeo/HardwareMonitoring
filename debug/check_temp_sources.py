"""
check_temp_sources.py
=====================
Script diagnostik ringkas untuk menguji pembacaan suhu CPU via:
- ACPI ThermalZone WMI bawaan Windows (root\wmi)
"""

import sys
import os
import ctypes
import time

# IMPORT MODULE YANG DIPERLUKAN
try:
    import wmi
    import pythoncom
except ImportError:
    print("[ERROR] Modul 'wmi' atau 'pywin32' belum terinstall.")
    print("Silakan install dengan: pip install wmi pywin32")
    sys.exit(1)

# Check Administrator privileges
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False

print("=" * 65)
print(" CEK SUMBER SUHU CPU - FckinMonitoring Diagnostic")
print(" Status Hak Akses Admin:", "AKTIF (OK)" if is_admin() else "TIDAK (Jalankan as Admin untuk ctypes/DLL)")
print("=" * 65)

# -----------------------------------------------------------------
# ACPI Thermal Zone WMI
# -----------------------------------------------------------------
print("\n[1] Mengecek ACPI ThermalZone bawaan Windows (root\\wmi)...")
try:
    # Inisialisasi thread COM
    pythoncom.CoInitialize()
    
    therm = wmi.WMI(namespace="root\\wmi")
    zones = therm.MSAcpi_ThermalZoneTemperature()
    
    if zones:
        print(f"    [SUCCESS] DITEMUKAN {len(zones)} thermal zone:")
        for z in zones:
            celsius = (z.CurrentTemperature / 10.0) - 273.15
            print(f"      - {z.InstanceName}: {celsius:.1f} °C")
    else:
        print("    [FAILED] Namespace ada, tapi TIDAK ADA thermal zone terdaftar.")
except Exception as e:
    print(f"    [FAILED] TIDAK DITEMUKAN. Alasan: {e}")
finally:
    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass

print("\n" + "=" * 65)