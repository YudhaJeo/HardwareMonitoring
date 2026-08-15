"""
=====================================================================
 SYSTEM MONITORING OVERLAY - Xbox Game Bar Style (Lightweight)
=====================================================================
Fitur:
- Overlay dark-dimming + widget Settings (tengah) + widget Monitoring (draggable)
- Toggle buka/tutup dengan hotkey global: Ctrl+Alt+G
- Saat "minimize" (tertutup): hanya widget monitoring tampil,
  tidak bisa digeser, tidak bisa diklik (click-through), selalu on-top.
- Monitoring: CPU, RAM, GPU (Nvidia via pynvml, fallback GPUtil), FPS (estimasi)

CATATAN FPS:
Hooking FPS game secara native (seperti RTSS) butuh inject DLL/driver
level yang berisiko kena anti-cheat & sangat berat dikembangkan.
Solusi ringan di sini: estimasi FPS berbasis frekuensi perubahan
frame layar (screen-diff sampling) pada area kecil di layar aktif.
Ini BUKAN pengganti RTSS, hanya estimasi ringan tanpa hook/inject.
=====================================================================
"""

import sys
import time
import threading

import psutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QCheckBox, QFrame
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint
from PyQt6.QtGui import QColor, QPainter, QFont

# ---------------------------------------------------------------
# GPU (opsional, Nvidia via pynvml, fallback GPUtil, fallback N/A)
# ---------------------------------------------------------------
GPU_MODE = None
try:
    import pynvml
    pynvml.nvmlInit()
    _gpu_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
    GPU_MODE = "nvml"
except Exception:
    try:
        import GPUtil
        GPU_MODE = "gputil"
    except Exception:
        GPU_MODE = None

# ---------------------------------------------------------------
# FPS estimator ringan (screen-diff sampling, tanpa hook/inject)
# ---------------------------------------------------------------
try:
    import mss
    import numpy as np
    _MSS_OK = True
except Exception:
    _MSS_OK = False


class FPSEstimator:
    """Estimasi FPS via sampling kecil area layar (bukan hasil presisi)."""

    def __init__(self, region_size=120):
        self.region_size = region_size
        self.enabled = _MSS_OK
        self._sct = mss.mss() if _MSS_OK else None

    def estimate(self, sample_time=0.5):
        if not self.enabled:
            return 0
        try:
            monitor = self._sct.monitors[1]
            w, h = self.region_size, self.region_size
            box = {
                "left": monitor["left"] + monitor["width"] // 2 - w // 2,
                "top": monitor["top"] + monitor["height"] // 2 - h // 2,
                "width": w,
                "height": h,
            }
            changes = 0
            prev = np.array(self._sct.grab(box))
            t_end = time.time() + sample_time
            while time.time() < t_end:
                time.sleep(1 / 60)  # cek maksimal setara 60Hz
                cur = np.array(self._sct.grab(box))
                if not np.array_equal(prev, cur):
                    changes += 1
                    prev = cur
            fps = int(changes / sample_time)
            return fps
        except Exception:
            return 0


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
        self.flags = {"cpu": True, "ram": True, "gpu": True, "fps": True}

    def run(self):
        while self._running:
            data = {}
            if self.flags.get("cpu"):
                data["cpu"] = psutil.cpu_percent(interval=None)
            if self.flags.get("ram"):
                data["ram"] = psutil.virtual_memory().percent
            if self.flags.get("gpu"):
                data["gpu"] = self._get_gpu_usage()
            if self.flags.get("fps"):
                # sampling FPS pakai porsi waktu interval agar tetap ringan
                data["fps"] = self.fps_est.estimate(sample_time=min(0.5, self.interval))
            self.data_ready.emit(data)
            time.sleep(max(0.1, self.interval - (0.5 if self.flags.get("fps") else 0)))

    def _get_gpu_usage(self):
        try:
            if GPU_MODE == "nvml":
                util = pynvml.nvmlDeviceGetUtilizationRates(_gpu_handle)
                return util.gpu
            elif GPU_MODE == "gputil":
                gpus = GPUtil.getGPUs()
                if gpus:
                    return int(gpus[0].load * 100)
            return None
        except Exception:
            return None

    def stop(self):
        self._running = False
        self.wait(1000)


# ---------------------------------------------------------------
# Widget Monitoring (draggable saat overlay aktif)
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

        self.resize(340, 40)
        self.move(40, 40)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(20, 20, 20, 170))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 10, 10)

    def update_data(self, data):
        if "cpu" in data:
            self.lbl_cpu.setText(f"CPU {data['cpu']:.0f}%")
            self.lbl_cpu.setVisible(True)
        else:
            self.lbl_cpu.setVisible(False)
        if "ram" in data:
            self.lbl_ram.setText(f"RAM {data['ram']:.0f}%")
            self.lbl_ram.setVisible(True)
        else:
            self.lbl_ram.setVisible(False)
        if "gpu" in data:
            val = data["gpu"]
            self.lbl_gpu.setText(f"GPU {val:.0f}%" if val is not None else "GPU N/A")
            self.lbl_gpu.setVisible(True)
        else:
            self.lbl_gpu.setVisible(False)
        if "fps" in data:
            self.lbl_fps.setText(f"FPS {data['fps']}")
            self.lbl_fps.setVisible(True)
        else:
            self.lbl_fps.setVisible(False)
        self.adjustSize()

    def set_clickthrough(self, enabled: bool):
        """Aktifkan/nonaktifkan mode klik-tembus (minimize mode)."""
        self.draggable = not enabled
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        self.show()  # perlu show ulang agar flag baru diterapkan

    # --- Drag handling, hanya aktif saat overlay dibuka ---
    def mousePressEvent(self, event):
        if self.draggable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event):
        if self.draggable and self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None


# ---------------------------------------------------------------
# Widget Settings (di tengah layar, hanya muncul saat overlay dibuka)
# ---------------------------------------------------------------
class SettingsWidget(QWidget):
    def __init__(self, on_toggle):
        super().__init__()
        self.on_toggle = on_toggle
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        title = QLabel("Overlay Settings")
        title.setStyleSheet("color: white; font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

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

        hint = QLabel("Ctrl+Alt+G untuk buka/tutup overlay")
        hint.setStyleSheet("color: #aaa; font-size: 10px;")
        layout.addWidget(hint)

        self.resize(240, 200)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(30, 30, 30, 235))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 14, 14)


# ---------------------------------------------------------------
# Dark dimming background (fullscreen, klik-tembus dimatikan saat aktif)
# ---------------------------------------------------------------
class DimOverlay(QWidget):
    def __init__(self):
        super().__init__()
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
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))  # dim transparan


# ---------------------------------------------------------------
# Controller utama: mengatur state open / minimized + hotkey global
# ---------------------------------------------------------------
class OverlayApp:
    def __init__(self):
        self.app = QApplication(sys.argv)

        self.dim = DimOverlay()
        self.monitor_widget = MonitorWidget()
        self.settings_widget = SettingsWidget(self.on_toggle_metric)

        self.worker = MonitorWorker(interval=1.0)
        self.worker.data_ready.connect(self.monitor_widget.update_data)
        self.worker.start()

        self.is_open = False
        self.set_minimized_state()  # default: mulai dalam mode minimized

        self.monitor_widget.show()

        # Hotkey global Ctrl+Alt+G untuk toggle (butuh library 'keyboard')
        self._start_hotkey_listener()

    def on_toggle_metric(self, key, enabled):
        self.worker.flags[key] = enabled

    def _center_settings(self):
        screen_geo = QApplication.primaryScreen().geometry()
        x = screen_geo.center().x() - self.settings_widget.width() // 2
        y = screen_geo.center().y() - self.settings_widget.height() // 2
        self.settings_widget.move(x, y)

    def set_open_state(self):
        """Overlay aktif: dim background + settings + monitor draggable."""
        self.is_open = True
        self._center_settings()
        self.dim.show()
        self.settings_widget.show()
        self.monitor_widget.set_clickthrough(False)

    def set_minimized_state(self):
        """Overlay non-aktif: hanya monitor tampil, klik-tembus, tak bisa digeser."""
        self.is_open = False
        self.dim.hide()
        self.settings_widget.hide()
        self.monitor_widget.set_clickthrough(True)

    def toggle(self):
        if self.is_open:
            self.set_minimized_state()
        else:
            self.set_open_state()

    def _start_hotkey_listener(self):
        try:
            import keyboard  # pip install keyboard (mungkin butuh admin di Windows)

            def listener():
                keyboard.add_hotkey("ctrl+alt+g", self.toggle)
                keyboard.wait()

            t = threading.Thread(target=listener, daemon=True)
            t.start()
        except Exception:
            print("[!] Modul 'keyboard' tidak tersedia. "
                  "Jalankan sebagai admin atau install: pip install keyboard")

    def run(self):
        exit_code = self.app.exec()
        self.worker.stop()
        sys.exit(exit_code)


if __name__ == "__main__":
    OverlayApp().run()
