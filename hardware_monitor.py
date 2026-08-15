"""
=====================================================================
 FckinMonitoring - System Monitoring Overlay (Lightweight)
=====================================================================
Struktur proyek:
    assets/icon.png     -> icon system tray & aplikasi
    versions/            -> hasil build .exe / installer
    hardware_monitor.py  -> file ini

Install dependency:
    pip install PyQt6 psutil pywin32 WMI mss numpy pythonnet

Jalankan:
    python hardware_monitor.py

Perilaku:
- App langsung jalan di background (system tray), TIDAK ada window
  yang muncul saat pertama dibuka.
- Klik kiri icon tray -> overlay "terbuka": layar dim + widget Settings
  (tengah layar) + widget monitoring bisa digeser (drag).
- Klik kiri icon tray lagi / klik tombol X di Settings / klik area
  dim -> overlay "tertutup": hanya widget monitoring tersisa,
  klik-tembus, tidak bisa digeser.
- Klik kanan icon tray -> menu (Buka/Tutup Overlay, Keluar).

CATATAN GPU (non-Nvidia):
Karena tidak pakai GPU Nvidia, GPU usage diambil dari Windows
Performance Counters "GPU Engine" via WMI (root\\cimv2,
Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine).
Ini didukung native oleh Windows 10/11 untuk semua vendor GPU
(Nvidia/AMD/Intel) TANPA perlu driver/DLL vendor tambahan.

CATATAN SUHU CPU (urutan prioritas sumber):
  1. LibreHardwareMonitorLib.dll IN-PROCESS via pythonnet (UTAMA) - akurat
     & real-time, TANPA proses terpisah. Wajib taruh file DLL di:
         libs/LibreHardwareMonitorLib.dll
     Download dari rilis "portable" (bukan installer) di:
         https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases
  2. LibreHardwareMonitor.exe / OpenHardwareMonitor.exe via WMI (fallback,
     hanya kepakai kalau kamu jalankan aplikasi itu manual terpisah).
  3. MSAcpi_ThermalZoneTemperature / ACPI (LAST RESORT) - nilainya sering
     cache/statis, TIDAK real-time. Cuma dipakai kalau 1 & 2 gagal total.
Aplikasi ini WAJIB berjalan sebagai Administrator (auto-elevate lewat UAC
saat start) karena driver sensor (WinRing0) butuh privilege tinggi.

CATATAN FPS:
FPS adalah ESTIMASI ringan berbasis screen-diff sampling pada
area window yang sedang aktif (foreground), BUKAN hook/inject
seperti RTSS, sehingga aman dari deteksi anti-cheat.
=====================================================================
"""

import sys
import os
import time
import logging
import ctypes

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QCheckBox, QFrame, QPushButton, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QFont, QIcon

# ---------------------------------------------------------------
# WMI (untuk GPU usage & fallback CPU temperature) - fitur khusus Windows
# ---------------------------------------------------------------
WMI_AVAILABLE = True
try:
    import wmi
    import pythoncom
except Exception:
    WMI_AVAILABLE = False

# ---------------------------------------------------------------
# pythonnet (clr) - untuk baca LibreHardwareMonitorLib.dll IN-PROCESS.
# Ini sumber suhu CPU UTAMA & PALING AKURAT: tanpa perlu menjalankan
# LibreHardwareMonitor.exe terpisah, tanpa WMI/COM, tanpa proses
# background tambahan -> tetap ringan.
# Install: pip install pythonnet
# DLL: taruh LibreHardwareMonitorLib.dll di folder libs/ (lihat catatan
# di bawah _init_temp).
# ---------------------------------------------------------------
try:
    import clr  # noqa: F401  (import awal saja untuk cek ketersediaan pythonnet)
    _PYTHONNET_OK = True
except Exception:
    _PYTHONNET_OK = False

# ---------------------------------------------------------------
# FPS estimator ringan (screen-diff sampling, tanpa hook/inject)
# ---------------------------------------------------------------
try:
    import mss
    import numpy as np
    _MSS_OK = True
except Exception:
    _MSS_OK = False

try:
    import win32gui
    _WIN32GUI_OK = True
except Exception:
    _WIN32GUI_OK = False


def _is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_admin():
    """
    WMI query untuk suhu CPU (root\\wmi ACPI, dan seringnya juga
    root\\LibreHardwareMonitor / root\\OpenHardwareMonitor) butuh
    proses yang menjalankannya berjalan dengan privilege Administrator.
    Tanpa ini, query akan gagal DIAM-DIAM (exception ditangkap) dan
    TEMP selalu tampil N/A meski sumbernya sebenarnya tersedia.

    Untuk exe hasil build PyInstaller, UAC sudah otomatis diminta lewat
    manifest (lihat hardware_monitor.spec: uac_admin=True), jadi fungsi
    ini di-skip. Untuk mode development (python hardware_monitor.py),
    fungsi ini me-relaunch proses dengan hak admin lalu keluar dari
    proses lama yang non-admin.
    """
    if getattr(sys, "frozen", False):
        return  # exe: elevation ditangani oleh manifest PyInstaller

    if _is_admin():
        return

    logging.info("Belum berjalan sebagai Administrator, mencoba auto-elevate (UAC)...")
    try:
        script = os.path.abspath(sys.argv[0])
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
        cmd = f'"{script}" {params}'.strip()
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, cmd, None, 1)
    except Exception as e:
        logging.error(f"Auto-elevate gagal: {e}. Suhu CPU kemungkinan besar akan N/A.")
    sys.exit(0)


def _log_path():
    base = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "FckinMonitoring")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "fckinmonitoring.log")


logging.basicConfig(
    filename=_log_path(),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def resource_path(relative_path):
    """Resolusi path asset yang aman untuk mode script maupun exe PyInstaller."""
    try:
        base_path = sys._MEIPASS  # dibuat PyInstaller saat runtime (onefile)
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


class FPSEstimator:
    """Estimasi FPS via sampling kecil area window aktif (bukan hasil presisi)."""

    def __init__(self, region_size=150):
        self.region_size = region_size
        self._sct = None  # PENTING: mss harus dibuat di thread yang memakainya

    def _ensure_sct(self):
        if self._sct is None:
            self._sct = mss.mss()

    def _get_region(self):
        # Coba ambil area window yang sedang aktif (foreground) agar relevan saat gaming
        if _WIN32GUI_OK:
            try:
                hwnd = win32gui.GetForegroundWindow()
                l, t, r, b = win32gui.GetWindowRect(hwnd)
                if r > l and b > t:
                    w = min(self.region_size, r - l)
                    h = min(self.region_size, b - t)
                    cx, cy = l + (r - l) // 2, t + (b - t) // 2
                    return {"left": cx - w // 2, "top": cy - h // 2, "width": w, "height": h}
            except Exception:
                pass
        # Fallback: tengah monitor utama
        self._ensure_sct()
        mon = self._sct.monitors[1]
        w = h = self.region_size
        return {
            "left": mon["left"] + mon["width"] // 2 - w // 2,
            "top": mon["top"] + mon["height"] // 2 - h // 2,
            "width": w, "height": h,
        }

    def estimate(self, sample_time=0.5):
        if not _MSS_OK:
            return 0
        self._ensure_sct()
        try:
            box = self._get_region()
            changes = 0
            prev = np.array(self._sct.grab(box))
            t_end = time.time() + sample_time
            while time.time() < t_end:
                time.sleep(1 / 60)
                cur = np.array(self._sct.grab(box))
                if not np.array_equal(prev, cur):
                    changes += 1
                    prev = cur
            return int(changes / sample_time)
        except Exception:
            return 0


class LibreTempReader:
    """
    Baca suhu CPU langsung dari LibreHardwareMonitorLib.dll SECARA IN-PROCESS,
    lewat pythonnet (clr). TIDAK menjalankan LibreHardwareMonitor.exe sebagai
    proses terpisah, TIDAK pakai WMI/COM sama sekali.

    Kenapa ini lebih ringan & lebih akurat daripada jalur WMI:
    - Tidak ada proses ke-2 yang harus selalu running (hemat RAM/CPU)
    - Tidak ada overhead marshalling COM/WMI
    - Data sensor diambil real-time langsung dari driver di proses kita sendiri

    Setup:
    1. pip install pythonnet
    2. Download LibreHardwareMonitor (versi "portable", BUKAN installer)
       dari: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases
    3. Dari isi ZIP-nya, ambil file "LibreHardwareMonitorLib.dll" saja
       (dan "HidSharp.dll" jika ada di folder yang sama) -> taruh di:
           libs/LibreHardwareMonitorLib.dll
           libs/HidSharp.dll   (jika tersedia)
       Ini HANYA menyalin 1-2 file DLL, bukan instalasi aplikasi.

    Penyebab error paling umum (pythonnet + LibreHardwareMonitorLib):
    - Python HARUS versi 64-bit (DLL-nya x64). Cek: python -c "import struct;print(struct.calcsize('P')*8)"
    - HARUS jalan sebagai Administrator (driver kernel WinRing0 butuh privilege
      tinggi) -> sudah otomatis ditangani oleh _ensure_admin() di file ini.
    - "HidSharp.dll" hilang -> beberapa versi LHM butuh file ini di folder yang sama.
    - Bentrok dengan MSI Center / software vendor lain yang pegang driver sensor
      yang sama -> tutup dulu software sejenis (MSI Center, HWiNFO, dll).
    - Versi pythonnet lama tidak cocok dengan DLL target .NET terbaru ->
      pastikan pythonnet versi terbaru: pip install -U pythonnet
    """

    def __init__(self, dll_dir):
        self.available = False
        self.computer = None
        self._Hardware = None

        if not _PYTHONNET_OK:
            logging.warning("pythonnet ('clr') tidak terinstall -> pip install pythonnet")
            return

        dll_path = os.path.join(dll_dir, "LibreHardwareMonitorLib.dll")
        if not os.path.exists(dll_path):
            logging.warning(f"LibreHardwareMonitorLib.dll tidak ditemukan di: {dll_path}")
            return

        try:
            import clr
            clr.AddReference(dll_path)
            from LibreHardwareMonitor import Hardware  # noqa: N811 (nama namespace .NET)

            self._Hardware = Hardware
            self.computer = Hardware.Computer()
            self.computer.IsCpuEnabled = True
            self.computer.Open()
            self.available = True
            logging.info("LibreHardwareMonitorLib berhasil di-load IN-PROCESS (tanpa proses terpisah).")
        except Exception as e:
            logging.error(f"Gagal load LibreHardwareMonitorLib.dll: {e}")
            self.available = False

    def read_cpu_temp(self):
        if not self.available:
            return None
        try:
            best = None
            for hw in self.computer.Hardware:
                # Logging untuk memantau semua hardware yang terdeteksi
                logging.info(f"HW: {hw.Name} type={hw.HardwareType}")
                
                if hw.HardwareType != self._Hardware.HardwareType.Cpu:
                    continue
                
                hw.Update()
                for sensor in hw.Sensors:
                    # Logging untuk memantau semua sensor yang ada di dalam CPU
                    logging.info(f"  Sensor: {sensor.Name} type={sensor.SensorType} value={sensor.Value}")
                    
                    if sensor.SensorType != self._Hardware.SensorType.Temperature:
                        continue
                    if sensor.Value is None:
                        continue
                    
                    name = (sensor.Name or "").lower()
                    val = float(sensor.Value)
                    
                    if "package" in name:
                        return val  # paling representatif, langsung dipakai
                    if best is None or val > best:
                        best = val  # fallback: suhu core tertinggi
            return best
        except Exception as e:
            logging.warning(f"Gagal baca sensor LibreHardwareMonitorLib in-process, reset: {e}")
            self.available = False
            return None

    def close(self):
        try:
            if self.computer:
                self.computer.Close()
        except Exception:
            pass

# ---------------------------------------------------------------
# Worker thread: polling data sistem secara berkala (hemat resource)
# ---------------------------------------------------------------
class MonitorWorker(QThread):
    data_ready = pyqtSignal(dict)

    def __init__(self, interval=1.0):
        super().__init__()
        self.interval = interval
        self._running = True
        self.fps_est = FPSEstimator()
        self.flags = {"cpu": True, "ram": True, "gpu": True, "temp": True, "fps": True}
        self._wmi_gpu = None
        self._wmi_lhm = None
        self._wmi_therm = None
        self.libre_temp = None  # LibreTempReader in-process (sumber suhu utama)
        self.temp_mode = None
        # --- Auto-retry state untuk koneksi suhu CPU ---
        # Jika LibreHardwareMonitor baru dibuka setelah overlay jalan (atau
        # ditutup di tengah jalan), worker akan otomatis mencoba re-connect
        # secara periodik tanpa membebani loop utama (bukan retry tiap frame).
        self._last_temp_retry = 0.0
        self._temp_retry_interval = 5.0  # detik antar percobaan re-connect

    def run(self):
        # WMI/COM wajib di-inisialisasi di thread yang memakainya
        if WMI_AVAILABLE:
            pythoncom.CoInitialize()
        self._init_gpu()
        self._init_temp()
        self._last_temp_retry = time.time()  # tandai percobaan awal, retry berikutnya menunggu interval
        try:
            while self._running:
                data = {}
                if self.flags.get("cpu"):
                    data["cpu"] = psutil.cpu_percent(interval=None)
                if self.flags.get("ram"):
                    data["ram"] = psutil.virtual_memory().percent
                if self.flags.get("gpu"):
                    data["gpu"] = self._get_gpu_usage()
                if self.flags.get("temp"):
                    # Auto-retry: kalau belum ada sumber suhu (mis. LibreHardwareMonitor
                    # baru dibuka belakangan), coba re-init tiap _temp_retry_interval detik.
                    # Tidak dilakukan tiap frame supaya tidak membebani UI/CPU.
                    if self.temp_mode is None and (time.time() - self._last_temp_retry) >= self._temp_retry_interval:
                        logging.info("Auto-retry: mencoba re-connect ke sumber suhu CPU...")
                        self._init_temp()
                        self._last_temp_retry = time.time()
                    data["temp"] = self._get_cpu_temp()
                if self.flags.get("fps"):
                    data["fps"] = self.fps_est.estimate(sample_time=min(0.5, self.interval))
                self.data_ready.emit(data)
                fps_cost = 0.5 if self.flags.get("fps") else 0
                time.sleep(max(0.1, self.interval - fps_cost))
        finally:
            if WMI_AVAILABLE:
                pythoncom.CoUninitialize()

    # ---- GPU (Nvidia/AMD/Intel via Windows Performance Counters) ----
    def _init_gpu(self):
        if not WMI_AVAILABLE:
            return
        try:
            self._wmi_gpu = wmi.WMI(namespace="root\\cimv2")
        except Exception:
            self._wmi_gpu = None

    def _get_gpu_usage(self):
        if not self._wmi_gpu:
            return None
        try:
            items = self._wmi_gpu.query(
                "SELECT Name, UtilizationPercentage FROM "
                "Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
                "WHERE Name LIKE '%engtype_3D%'"
            )
            total = sum(float(i.UtilizationPercentage) for i in items)
            return min(100.0, total)
        except Exception:
            return None

    # ---- CPU Temperature: in-process (utama) -> WMI LHM/OHM -> ACPI (last resort) ----
    def _init_temp(self):
        # 1) In-process via pythonnet - PALING RINGAN & AKURAT, tanpa proses terpisah
        if self.libre_temp is None:
            dll_dir = resource_path("libs")
            self.libre_temp = LibreTempReader(dll_dir)
        if self.libre_temp.available:
            self.temp_mode = "libre_inproc"
            return

        if not WMI_AVAILABLE:
            logging.warning("Modul 'wmi'/'pythoncom' tidak tersedia, temp dinonaktifkan.")
            self.temp_mode = None
            return

        # 2) Fallback: LibreHardwareMonitor.exe / OpenHardwareMonitor.exe terpisah via WMI
        #    (dipakai HANYA jika kamu memang menjalankan aplikasi itu secara manual)
        try:
            lhm = wmi.WMI(namespace="root\\LibreHardwareMonitor")
            sensors = lhm.Sensor()
            temp_sensors = [s.Name for s in sensors if s.SensorType == "Temperature"]
            if temp_sensors:
                self._wmi_lhm = lhm
                self.temp_mode = "lhm"
                logging.info(f"Temp source: LibreHardwareMonitor (WMI). Sensor: {temp_sensors}")
                return
        except Exception as e:
            logging.info(f"LibreHardwareMonitor WMI tidak tersedia: {e}")

        try:
            ohm = wmi.WMI(namespace="root\\OpenHardwareMonitor")
            sensors = ohm.Sensor()
            temp_sensors = [s.Name for s in sensors if s.SensorType == "Temperature"]
            if temp_sensors:
                self._wmi_lhm = ohm
                self.temp_mode = "lhm"
                logging.info(f"Temp source: OpenHardwareMonitor (WMI). Sensor: {temp_sensors}")
                return
        except Exception as e:
            logging.info(f"OpenHardwareMonitor WMI tidak tersedia: {e}")

        # 3) LAST RESORT: ACPI ThermalZone bawaan Windows.
        #    PERINGATAN: nilainya sering cache/statis, TIDAK real-time.
        #    Hanya dipakai kalau semua sumber di atas gagal, lebih baik ada
        #    angka kasar daripada N/A terus.
        try:
            therm = wmi.WMI(namespace="root\\wmi")
            zones = therm.MSAcpi_ThermalZoneTemperature()
            if zones:
                self._wmi_therm = therm
                self.temp_mode = "acpi"
                logging.info("Temp source: ACPI ThermalZone (root\\wmi) - CATATAN: nilai mungkin tidak real-time.")
                return
        except Exception as e:
            logging.info(f"ACPI ThermalZone tidak tersedia: {e}")

        logging.error(
            "Tidak ada sumber suhu CPU yang terdeteksi. Pastikan libs/LibreHardwareMonitorLib.dll "
            "ada dan aplikasi berjalan sebagai Administrator."
        )
        self.temp_mode = None

    def _get_cpu_temp(self):
        """
        Baca suhu CPU dari sumber aktif (LibreHardwareMonitor/OpenHardwareMonitor
        atau ACPI). Pencarian nama sensor dibuat fleksibel karena LibreHardwareMonitor
        menamai sensor berbeda-beda tergantung platform CPU. Untuk Intel Gen 13
        (Raptor Lake, mis. i5-13500H di MSI Prestige 14 Evo) sensor "package" yang
        eksplisit kadang tidak ada / namanya cuma "Package" atau "CPU Core #1",
        jadi dicari bertingkat: package -> keyword umum -> max dari core individual
        -> sensor apa pun yang menyebut "cpu" -> sensor temperature pertama.

        Jika koneksi ke provider WMI mati (mis. LibreHardwareMonitor ditutup user),
        exception ditangkap, self.temp_mode di-reset ke None supaya loop run()
        otomatis mencoba re-connect di siklus retry berikutnya.
        """
        try:
            if self.temp_mode == "libre_inproc" and self.libre_temp:
                val = self.libre_temp.read_cpu_temp()
                if val is None and not self.libre_temp.available:
                    # DLL/driver bermasalah di tengah jalan -> reset supaya di-retry
                    raise RuntimeError("LibreTempReader in-process tidak lagi available")
                return val

            elif self.temp_mode == "lhm" and self._wmi_lhm:
                sensors = self._wmi_lhm.Sensor()
                temp_sensors = [s for s in sensors if s.SensorType == "Temperature"]
                if not temp_sensors:
                    raise RuntimeError("Sensor Temperature kosong (provider mungkin baru ditutup)")

                # Prioritas 1: sensor yang eksplisit menyebut "package"
                for s in temp_sensors:
                    if "package" in (s.Name or "").lower():
                        return float(s.Value)

                # Prioritas 2: keyword umum untuk suhu CPU keseluruhan
                # (beda vendor/skema penamaan LHM beda-beda)
                keywords = ("cpu average", "core average", "core max", "tctl", "tdie")
                for keyword in keywords:
                    for s in temp_sensors:
                        if keyword in (s.Name or "").lower():
                            return float(s.Value)

                # Prioritas 3: ambil nilai tertinggi dari core individual
                # (umum di Intel Gen 13/Raptor Lake: "CPU Core #1", "CPU Core #2", dst.
                # Suhu core tertinggi paling merepresentasikan beban CPU saat ini.)
                core_values = [
                    float(s.Value) for s in temp_sensors
                    if "core" in (s.Name or "").lower() and "cpu" in (s.Name or "").lower()
                ]
                if core_values:
                    return max(core_values)

                # Prioritas 4: sensor apa pun yang menyebut "cpu"
                cpu_values = [float(s.Value) for s in temp_sensors if "cpu" in (s.Name or "").lower()]
                if cpu_values:
                    return max(cpu_values)

                # Prioritas 5 (last resort): sensor temperature pertama yang tersedia,
                # daripada tetap tampil N/A padahal datanya sebenarnya ada.
                return float(temp_sensors[0].Value)

            elif self.temp_mode == "acpi" and self._wmi_therm:
                zones = self._wmi_therm.MSAcpi_ThermalZoneTemperature()
                if not zones:
                    raise RuntimeError("ACPI ThermalZone kosong")
                return (zones[0].CurrentTemperature / 10.0) - 273.15

        except Exception as e:
            # Koneksi ke provider (LibreHardwareMonitor/OpenHardwareMonitor/ACPI)
            # terputus atau providernya ditutup -> reset state supaya di-retry lagi
            # secara otomatis oleh loop run() tanpa perlu restart aplikasi.
            logging.warning(f"Sumber suhu CPU terputus, reset untuk auto-retry. Alasan: {e}")
            self.temp_mode = None
            self._wmi_lhm = None
            self._wmi_therm = None
            self._last_temp_retry = time.time()  # cegah retry langsung di siklus berikutnya

        return None

    def stop(self):
        self._running = False
        self.wait(1000)
        if self.libre_temp:
            self.libre_temp.close()


# ---------------------------------------------------------------
# Widget Monitoring (draggable saat overlay dibuka)
# ---------------------------------------------------------------
class MonitorWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self.draggable = True

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(14)

        font = QFont("Segoe UI", 10)
        self.lbl_cpu = QLabel("CPU --%")
        self.lbl_ram = QLabel("RAM --%")
        self.lbl_gpu = QLabel("GPU --%")
        self.lbl_temp = QLabel("TEMP --\u00b0C")
        self.lbl_fps = QLabel("FPS --")
        for lbl in (self.lbl_cpu, self.lbl_ram, self.lbl_gpu, self.lbl_temp, self.lbl_fps):
            lbl.setFont(font)
            lbl.setStyleSheet("color: white;")
            layout.addWidget(lbl)

        self.resize(400, 40)
        self.move(40, 40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Opacity bar dikurangi 30% dari sebelumnya (170 -> 119)
        painter.setBrush(QColor(20, 20, 20, 119))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 10, 10)

    def update_data(self, data):
        def set_label(lbl, key, fmt, suffix_na):
            if key in data:
                val = data[key]
                lbl.setText(fmt.format(val) if val is not None else suffix_na)
                lbl.setVisible(True)
            else:
                lbl.setVisible(False)

        set_label(self.lbl_cpu, "cpu", "CPU {:.0f}%", "CPU N/A")
        set_label(self.lbl_ram, "ram", "RAM {:.0f}%", "RAM N/A")
        set_label(self.lbl_gpu, "gpu", "GPU {:.0f}%", "GPU N/A")
        set_label(self.lbl_temp, "temp", "TEMP {:.0f}\u00b0C", "TEMP N/A")
        set_label(self.lbl_fps, "fps", "FPS {:.0f}", "FPS N/A")
        self.adjustSize()

    def set_clickthrough(self, enabled: bool):
        """Aktifkan/nonaktifkan mode klik-tembus (mode tertutup)."""
        self.draggable = not enabled
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.show()  # perlu show ulang agar flag baru diterapkan

    def mousePressEvent(self, event):
        if self.draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.draggable and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------
# Widget Settings (tengah layar, hanya muncul saat overlay dibuka)
# ---------------------------------------------------------------
class SettingsWidget(QWidget):
    def __init__(self, on_toggle, on_close):
        super().__init__()
        self.on_toggle = on_toggle
        self.on_close = on_close
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("FckinMonitoring")
        title.setStyleSheet("color: white; font-weight: bold; font-size: 15px;")
        header.addWidget(title)
        header.addStretch()
        btn_close = QPushButton("\u2715")
        btn_close.setFixedSize(22, 22)
        btn_close.setStyleSheet(
            "QPushButton { color: #ccc; background: transparent; border: none; font-size: 13px; }"
            "QPushButton:hover { color: white; }"
        )
        btn_close.clicked.connect(lambda: self.on_close())
        header.addWidget(btn_close)
        layout.addLayout(header)

        subtitle = QLabel("Overlay Settings")
        subtitle.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(subtitle)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #555;")
        layout.addWidget(line)

        self.checks = {}
        for key, label in [("cpu", "CPU Usage"), ("ram", "RAM Usage"),
                            ("gpu", "GPU Usage"), ("temp", "CPU Temperature"),
                            ("fps", "FPS Estimate")]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("color: white;")
            cb.stateChanged.connect(lambda state, k=key: self.on_toggle(k, state != 0))
            layout.addWidget(cb)
            self.checks[key] = cb

        hint = QLabel("Klik icon tray / area gelap untuk menutup overlay")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

        self.resize(260, 230)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(30, 30, 30, 235))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 14, 14)


# ---------------------------------------------------------------
# Dark dimming background (fullscreen). Klik di area ini -> tutup overlay.
# ---------------------------------------------------------------
class DimOverlay(QWidget):
    def __init__(self, on_click):
        super().__init__()
        self.on_click = on_click
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen_geo = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geo)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

    def mousePressEvent(self, event):
        self.on_click()


# ---------------------------------------------------------------
# Controller utama: state open/closed dikendalikan dari system tray
# ---------------------------------------------------------------
class OverlayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # tetap hidup walau semua widget hidden

        icon_path = resource_path(os.path.join("assets", "icon.png"))
        self.app_icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.app.setWindowIcon(self.app_icon)

        self.dim = DimOverlay(on_click=self.set_closed_state)
        self.monitor_widget = MonitorWidget()
        self.settings_widget = SettingsWidget(self.on_toggle_metric, self.set_closed_state)

        self.worker = MonitorWorker(interval=1.0)
        self.worker.data_ready.connect(self.monitor_widget.update_data)
        self.worker.start()

        self.is_open = False
        self.set_closed_state()
        self.monitor_widget.show()

        self._init_tray()

    # ---- System tray ----
    def _init_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self.app)
        self.tray.setToolTip("FckinMonitoring")

        menu = QMenu()
        self.action_toggle = menu.addAction("Buka Overlay")
        self.action_toggle.triggered.connect(self.toggle)
        menu.addSeparator()
        action_quit = menu.addAction("Keluar")
        action_quit.triggered.connect(self.quit_app)
        self.tray.setContextMenu(menu)

        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # klik kiri
            self.toggle()

    def on_toggle_metric(self, key, enabled):
        self.worker.flags[key] = enabled

    def _center_settings(self):
        screen_geo = QApplication.primaryScreen().geometry()
        x = screen_geo.center().x() - self.settings_widget.width() // 2
        y = screen_geo.center().y() - self.settings_widget.height() // 2
        self.settings_widget.move(x, y)

    def set_open_state(self):
        """Overlay terbuka: dim background + settings + monitor draggable."""
        self.is_open = True
        self._center_settings()
        self.dim.show()
        self.settings_widget.show()
        self.monitor_widget.set_clickthrough(False)
        if hasattr(self, "action_toggle"):
            self.action_toggle.setText("Tutup Overlay")

    def set_closed_state(self):
        """Overlay tertutup: hanya monitor tampil, klik-tembus, tak bisa digeser."""
        self.is_open = False
        self.dim.hide()
        self.settings_widget.hide()
        self.monitor_widget.set_clickthrough(True)
        if hasattr(self, "action_toggle"):
            self.action_toggle.setText("Buka Overlay")

    def toggle(self):
        self.set_closed_state() if self.is_open else self.set_open_state()

    def quit_app(self):
        self.worker.stop()
        self.tray.hide()
        self.app.quit()

    def run(self):
        exit_code = self.app.exec()
        sys.exit(exit_code)


if __name__ == "__main__":
    _ensure_admin()
    logging.info(f"Aplikasi mulai. Berjalan sebagai Administrator: {_is_admin()}")
    OverlayApp().run()