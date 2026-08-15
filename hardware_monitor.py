"""
=====================================================================
 FckinMonitoring - System Monitoring Overlay (Lightweight)
=====================================================================
Struktur proyek:
    assets/icon.png     -> icon system tray & aplikasi
    versions/            -> hasil build .exe / installer
    hardware_monitor.py  -> file ini

Install dependency:
    pip install PyQt6 psutil pywin32 WMI mss numpy

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

CATATAN: FITUR SUHU CPU SUDAH DIHAPUS.
Sebelumnya suhu dibaca lewat LibreHardwareMonitorLib/WMI/ACPI, tapi
sumber-sumber itu butuh driver kernel (WinRing0) yang sering diblokir
oleh Memory Integrity/Secure Boot di laptop modern sehingga selalu
tampil N/A. Karena suhu dihapus, aplikasi ini JUGA TIDAK PERLU LAGI
berjalan sebagai Administrator - tidak ada auto-elevate/UAC lagi,
start-up jadi lebih cepat.

CATATAN FPS:
FPS adalah ESTIMASI ringan berbasis screen-diff sampling pada
area window yang sedang aktif (foreground), BUKAN hook/inject
seperti RTSS, sehingga aman dari deteksi anti-cheat. Sampling FPS
berjalan di THREAD TERPISAH dari CPU/RAM/GPU, supaya label CPU/RAM/GPU
tetap update cepat (tidak ikut menunggu jendela sampling FPS).
=====================================================================
"""

import sys
import os
import time
import logging

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QCheckBox, QFrame, QPushButton, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QFont, QIcon

# ---------------------------------------------------------------
# WMI (untuk GPU usage) - fitur khusus Windows
# ---------------------------------------------------------------
WMI_AVAILABLE = True
try:
    import wmi
    import pythoncom
except Exception:
    WMI_AVAILABLE = False

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

    def estimate(self, sample_time=0.4):
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


# ---------------------------------------------------------------
# Worker thread: polling CPU/RAM/GPU secara berkala (hemat resource).
# Sengaja TIDAK menyertakan FPS di sini, supaya loop ini bisa jalan
# cepat & konsisten tanpa terhambat jendela sampling FPS (lihat
# FPSWorker di bawah, jalan di thread terpisah).
# ---------------------------------------------------------------
class MonitorWorker(QThread):
    data_ready = pyqtSignal(dict)

    def __init__(self, interval=0.4):
        super().__init__()
        self.interval = interval
        self._running = True
        self.flags = {"cpu": True, "ram": True, "gpu": True}
        self._wmi_gpu = None

    def run(self):
        # WMI/COM wajib di-inisialisasi di thread yang memakainya
        if WMI_AVAILABLE:
            pythoncom.CoInitialize()
        self._init_gpu()
        # Panggilan pertama psutil.cpu_percent hanya untuk baseline
        psutil.cpu_percent(interval=None)
        try:
            while self._running:
                data = {}
                if self.flags.get("cpu"):
                    data["cpu"] = psutil.cpu_percent(interval=None)
                if self.flags.get("ram"):
                    data["ram"] = psutil.virtual_memory().percent
                if self.flags.get("gpu"):
                    data["gpu"] = self._get_gpu_usage()
                self.data_ready.emit(data)
                time.sleep(max(0.1, self.interval))
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

    def stop(self):
        self._running = False
        self.wait(1000)


# ---------------------------------------------------------------
# Worker thread TERPISAH khusus FPS. Sampling screen-diff butuh waktu
# (mis. ~0.4 detik per sampel) supaya hasilnya cukup stabil, jadi kalau
# digabung ke loop utama CPU/RAM/GPU jadi ikut lambat. Dengan thread
# sendiri, FPS tetap update rutin tanpa bikin CPU/RAM/GPU nunggu.
# ---------------------------------------------------------------
class FPSWorker(QThread):
    fps_ready = pyqtSignal(int)

    def __init__(self, sample_time=0.4):
        super().__init__()
        self.sample_time = sample_time
        self._running = True
        self._enabled = True
        self.fps_est = FPSEstimator()

    def run(self):
        while self._running:
            if self._enabled:
                fps = self.fps_est.estimate(sample_time=self.sample_time)
                if self._running:
                    self.fps_ready.emit(fps)
            else:
                time.sleep(0.2)

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def stop(self):
        self._running = False
        self.wait(1000)


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
        self.lbl_fps = QLabel("FPS --")
        for lbl in (self.lbl_cpu, self.lbl_ram, self.lbl_gpu, self.lbl_fps):
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
        """Update label CPU/RAM/GPU (dipanggil dari MonitorWorker)."""
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
        self.adjustSize()

    def update_fps(self, fps):
        """Update label FPS (dipanggil dari FPSWorker, thread terpisah)."""
        self.lbl_fps.setText(f"FPS {fps:.0f}" if fps is not None else "FPS N/A")
        self.lbl_fps.setVisible(True)
        self.adjustSize()

    def set_fps_visible(self, visible: bool):
        """Sembunyikan/tampilkan label FPS langsung saat di-toggle dari Settings,
        tanpa perlu menunggu FPSWorker emit lagi (worker bisa saja sedang di-pause)."""
        self.lbl_fps.setVisible(visible)
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
                            ("gpu", "GPU Usage"), ("fps", "FPS Estimate")]:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("color: white;")
            cb.stateChanged.connect(lambda state, k=key: self.on_toggle(k, state != 0))
            layout.addWidget(cb)
            self.checks[key] = cb

        hint = QLabel("Klik icon tray / area gelap untuk menutup overlay")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

        self.resize(260, 200)

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

        # CPU/RAM/GPU: loop cepat & ringan, tidak terikat sampling FPS.
        self.worker = MonitorWorker(interval=0.4)
        self.worker.data_ready.connect(self.monitor_widget.update_data)
        self.worker.start()

        # FPS: thread terpisah supaya tidak menahan update CPU/RAM/GPU.
        self.fps_worker = FPSWorker(sample_time=0.4)
        self.fps_worker.fps_ready.connect(self.monitor_widget.update_fps)
        self.fps_worker.start()

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
        if key == "fps":
            self.fps_worker.set_enabled(enabled)
            if not enabled:
                # Worker fps sedang di-pause, jadi sembunyikan label langsung
                # tanpa menunggu emit berikutnya (yang tidak akan datang).
                self.monitor_widget.set_fps_visible(False)
        else:
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
        self.fps_worker.stop()
        self.tray.hide()
        self.app.quit()

    def run(self):
        exit_code = self.app.exec()
        sys.exit(exit_code)


if __name__ == "__main__":
    logging.info("Aplikasi mulai (tanpa elevasi Administrator - tidak diperlukan lagi).")
    OverlayApp().run()