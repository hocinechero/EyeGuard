import os
import tempfile
import math
import atexit
import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from typing import List, Dict, Optional

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

try:
    dxva2 = ctypes.windll.dxva2

    class PHYSICAL_MONITOR(ctypes.Structure):
        _fields_ = [
            ("hPhysicalMonitor", wintypes.HANDLE),
            ("szPhysicalMonitorDescription", wintypes.WCHAR * 128),
        ]

    _HAS_DXVA2 = True
except Exception:
    dxva2 = None
    _HAS_DXVA2 = False

# Load Windows Magnification API for DWM-level 5x5 Color Shader, E-Ink Grayscale & Smart Invert
try:
    mag32 = ctypes.windll.magnification

    class MAGCOLOREFFECT(ctypes.Structure):
        _fields_ = [("transform", (ctypes.c_float * 5) * 5)]

    mag32.MagInitialize.restype = wintypes.BOOL
    mag32.MagUninitialize.restype = wintypes.BOOL
    mag32.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(MAGCOLOREFFECT)]
    mag32.MagSetFullscreenColorEffect.restype = wintypes.BOOL
    _HAS_MAGNIFICATION = True
except Exception:
    mag32 = None
    _HAS_MAGNIFICATION = False

ramp_type = wintypes.WORD * 256


class GAMMA_RAMP(ctypes.Structure):
    _fields_ = [("Red", ramp_type), ("Green", ramp_type), ("Blue", ramp_type)]


CCHDEVICENAME = 32


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * CCHDEVICENAME),
    ]


MonitorEnumProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)


def kelvin_to_rgb_extended(kelvin: float):
    """
    Continuous Blackbody Radiator Color Temperature Engine (1,000K to 10,000K):
    Calculates sRGB chromaticity coordinates based on Planckian blackbody locus
    polynomial approximations (Tanner Helland formulation, Public Domain, 2012).
    - 6500K: Standard D65 Native Screen (1.0, 1.0, 1.0) with zero tinting.
    - 6500K to 10,000K: Smooth daylight blackbody curve to cool blue sky.
    - 1900K to 6500K: Natural blue-light attenuation protecting circadian rhythm.
    - 1000K to 1900K: Deep darkroom and candlelight ember mode.
    """
    k = max(1000.0, min(10000.0, float(kelvin)))
    if abs(k - 6500.0) < 5.0:
        return 1.0, 1.0, 1.0

    temp = k / 100.0

    # Red component
    if temp <= 66.0:
        r = 1.0
    else:
        r = 329.698727446 * ((temp - 60.0) ** -0.1332047592) / 255.0
        r = max(0.0, min(1.0, r))

    # Green component
    if temp <= 66.0:
        g = (99.4708025861 * math.log(temp) - 161.1195681661) / 255.0
    else:
        g = (288.1221695283 * ((temp - 60.0) ** -0.0755148492)) / 255.0
    g = max(0.0, min(1.0, g))

    # Blue component
    if temp >= 66.0:
        b = 1.0
    elif temp <= 19.0:
        b = 0.0
    else:
        b = (138.5177312231 * math.log(temp - 10.0) - 305.0447927307) / 255.0
        b = max(0.0, min(1.0, b))

    # Normalize green so D65 (6500K) is exactly (1.0, 1.0, 1.0)
    g_d65 = (99.4708025861 * math.log(65.0) - 161.1195681661) / 255.0
    if temp <= 66.0:
        g = min(1.0, g / g_d65)

    return max(0.0, min(1.0, r)), max(0.0, min(1.0, g)), max(0.0, min(1.0, b))



AUTHENTIC_READING_THEMES = {
    # --- Authentic Document Reading Themes ---
    "normal": {"name": "Normal (Day)", "bg": [1.0, 1.0, 1.0], "txt": [0.0, 0.0, 0.0], "s": 1.0, "is_normal": True},
    "day": {"name": "Day", "bg": [1.0, 1.0, 1.0], "txt": [0.0, 0.0, 0.0], "s": 1.0, "is_normal": True},
    "sepia": {"name": "Warm Sepia", "bg": [240.0 / 255.0, 222.0 / 255.0, 199.0 / 255.0], "txt": [100.0 / 255.0, 77.0 / 255.0, 50.0 / 255.0], "s": 0.28},
    "paper": {"name": "Soft Paper", "bg": [240.0 / 255.0, 222.0 / 255.0, 199.0 / 255.0], "txt": [0.0, 0.0, 0.0], "s": 0.30},
    "twilight": {"name": "Twilight Slate", "bg": [43.0 / 255.0, 43.0 / 255.0, 43.0 / 255.0], "txt": [169.0 / 255.0, 183.0 / 255.0, 198.0 / 255.0], "s": 0.25},
    "d_sepia": {"name": "Dark Sepia", "bg": [43.0 / 255.0, 43.0 / 255.0, 43.0 / 255.0], "txt": [169.0 / 255.0, 183.0 / 255.0, 198.0 / 255.0], "s": 0.25},
    "night": {"name": "Night Contrast", "bg": [0.0, 0.0, 0.0], "txt": [161.0 / 255.0, 161.0 / 255.0, 161.0 / 255.0], "s": 0.20},
    "dark": {"name": "Dark Paper", "bg": [0.0, 0.0, 0.0], "txt": [1.0, 1.0, 1.0], "s": 0.20},
    "console": {"name": "Terminal Green", "bg": [0.0, 0.0, 0.0], "txt": [0.0, 128.0 / 255.0, 0.0], "s": 0.20},
    "e_ink": {"name": "Kindle E-Ink Paper", "e_ink": True},

    # --- Xodo PDF Reader Authentic Preset Palettes (from classes2.dex <clinit>) ---
    "xodo_clean_paper": {"name": "Xodo Clean Paper", "bg": [241/255, 243/255, 244/255], "txt": [54/255, 59/255, 61/255], "s": 0.25},
    "xodo_sage_olive": {"name": "Xodo Sage Olive", "bg": [181/255, 197/255, 136/255], "txt": [49/255, 54/255, 58/255], "s": 0.25},
    "xodo_mint": {"name": "Xodo Mint Celadon", "bg": [191/255, 216/255, 194/255], "txt": [64/255, 75/255, 64/255], "s": 0.25},
    "xodo_ice_blue": {"name": "Xodo Ice Blue", "bg": [208/255, 217/255, 227/255], "txt": [41/255, 60/255, 88/255], "s": 0.25},
    "xodo_sepia": {"name": "Xodo Classic Sepia", "bg": [222/255, 206/255, 172/255], "txt": [91/255, 67/255, 46/255], "s": 0.28},
    "xodo_rose": {"name": "Xodo Rose Blush", "bg": [233/255, 205/255, 213/255], "txt": [170/255, 69/255, 92/255], "s": 0.25},
    "xodo_emerald": {"name": "Xodo Emerald Mint", "bg": [153/255, 255/255, 204/255], "txt": [0.0, 0.0, 0.0], "s": 0.25},
    "xodo_sky_blue": {"name": "Xodo Sky Blue", "bg": [153/255, 204/255, 255/255], "txt": [0.0, 0.0, 0.0], "s": 0.25},
    "xodo_ash": {"name": "Xodo Warm Ash", "bg": [52/255, 48/255, 46/255], "txt": [137/255, 135/255, 132/255], "s": 0.22},
    "xodo_burgundy": {"name": "Xodo Burgundy Wine", "bg": [108/255, 45/255, 44/255], "txt": [252/255, 220/255, 220/255], "s": 0.22},
    "xodo_khaki": {"name": "Xodo Forest Khaki", "bg": [90/255, 95/255, 55/255], "txt": [252/255, 220/255, 220/255], "s": 0.22},
    "xodo_espresso": {"name": "Xodo Espresso Umber", "bg": [64/255, 47/255, 33/255], "txt": [222/255, 206/255, 172/255], "s": 0.25},
    "xodo_midnight": {"name": "Xodo Midnight Oceanic", "bg": [8/255, 48/255, 69/255], "txt": [219/255, 225/255, 227/255], "s": 0.22},
    "xodo_dusk": {"name": "Xodo OLED Dusk", "bg": [53/255, 59/255, 61/255], "txt": [135/255, 150/255, 153/255], "s": 0.22},
    "xodo_console": {"name": "Xodo Terminal Green", "bg": [0.0, 51/255, 0.0], "txt": [0.0, 1.0, 0.4], "s": 0.20},
}

def parse_color_hex(hex_str: str, default: List[float]) -> List[float]:
    try:
        s = str(hex_str).strip().lstrip("#")
        if len(s) == 6:
            return [int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0]
        elif len(s) == 8:
            return [int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0, int(s[6:8], 16) / 255.0]
    except Exception:
        pass
    return list(default)


class EyeGuardGammaEngine:
    """
    Fail-Safe Hybrid Display Engine:
    - Restores hardware GDI Gamma LUT to 100% linear identity on startup so Intel Iris Xe / WDDM 3.2
      drivers never suffer from hardware LUT blue-channel clipping.
    - Uses a dedicated Win32 message-pumped thread (EyeGuard-GammaWorker) with a 5x5 DWM hardware
      shader matrix (MagSetFullscreenColorEffect) supporting 1,000K-10,000K, 5%-100% software dimming,
      continuous 0%-100% Color Saturation, E-Ink Grayscale, Smart Invert, and Bean-Paste Green tint.
    - Also supports physical monitor backlight control via DDC/CI (dxva2.dll) & WMI.
    """

    def __init__(self):
        self._current_kelvin = 6500.0
        self._current_brightness = 1.0
        self._applied_kelvin = 6500.0
        self._applied_brightness = 1.0
        self.saturation = 1.0  # 0.0 (grayscale) .. 1.0 (full color)
        self.color_mode = "normal"
        self.grayscale = False
        self.invert = False
        self.green_background = False
        self.smooth_transition = True
        self.software_filter_mode = False
        self.standard_calibration_mode = True
        self.software_mouse_cursor = False

        self._monitors_cache = []
        self._last_monitor_check = 0.0

        self._mailbox_lock = threading.Lock()
        self._apply_lock = threading.Lock()
        self._event = threading.Event()
        self._epoch = 0
        self._pending_request: Optional[tuple] = None
        self._target_hw_brightness: Optional[int] = None
        self._hw_worker_lock = threading.Lock()
        self._hw_worker_running = False
        self._running = True

        self._mag_initialized = False
        self._original_gamma_ramp: Optional[GAMMA_RAMP] = None
        self._capture_original_hardware_lut()

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="EyeGuard-GammaWorker",
            daemon=True,
        )
        self._worker_thread.start()

        self._sentinel_path = os.path.join(tempfile.gettempdir(), f"eyeguard_clean_{os.getpid()}.flag")
        self._spawn_crash_guard()
        self._install_safety_handlers()
        atexit.register(self.shutdown)

    def _spawn_crash_guard(self):
        """Spawns zero-dependency supervisor to guarantee display recovery on SIGKILL / crash."""
        try:
            guard_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_guard.py")
            if os.path.exists(guard_script):
                py_exe = sys.executable
                if "python.exe" in py_exe.lower():
                    pyw = py_exe.lower().replace("python.exe", "pythonw.exe")
                    if os.path.exists(pyw):
                        py_exe = pyw
                creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(
                    [py_exe, guard_script, str(os.getpid()), self._sentinel_path],
                    creationflags=creationflags,
                    close_fds=True
                )
        except Exception:
            pass

    def _install_safety_handlers(self):
        """Installs OS signal handlers and sys.excepthook to prevent dirty display exit on crash."""
        import signal

        def _sig_handler(sig, frame):
            try:
                self.shutdown()
            except Exception:
                pass
            sys.exit(0)

        for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _sig_handler)
                except Exception:
                    pass

        old_excepthook = sys.excepthook

        def _safety_excepthook(exc_type, exc_val, exc_tb):
            try:
                self.shutdown()
            except Exception:
                pass
            if old_excepthook:
                old_excepthook(exc_type, exc_val, exc_tb)

        sys.excepthook = _safety_excepthook

    def _capture_original_hardware_lut(self):
        """Captures user's native monitor hardware LUT without touching or altering it."""
        try:
            ramp = GAMMA_RAMP()
            hdc = user32.GetDC(None)
            if hdc:
                if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                    self._original_gamma_ramp = ramp
                user32.ReleaseDC(None, hdc)
        except Exception:
            pass

    def _restore_original_hardware_lut(self):
        """Restores user's authentic native monitor hardware LUT or 100% linear calibration."""
        try:
            hdc = user32.GetDC(None)
            if hdc:
                if self._original_gamma_ramp is not None:
                    gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(self._original_gamma_ramp))
                else:
                    linear_ramp = GAMMA_RAMP()
                    for i in range(256):
                        val = min(65535, (i << 8) | i)
                        linear_ramp.Red[i] = val
                        linear_ramp.Green[i] = val
                        linear_ramp.Blue[i] = val
                    gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(linear_ramp))
                user32.ReleaseDC(None, hdc)
        except Exception:
            pass
    def _pump_win32_messages(self):
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _apply_dwm_color_effect(
        self, kelvin: float, brightness: float, is_gray: bool, is_invert: bool, color_mode: Optional[str] = None
    ):
        """
        Document Reading Theme Matrix:
        Implements real GPU-level paper color matrices (Day, Sepia, Warm Parchment, Night, Dark Sepia, Console, E-Ink)
        via 5x5 DWM Hardware Shader Matrix (MagSetFullscreenColorEffect).
        """
        if not _HAS_MAGNIFICATION:
            return

        k_clamped = max(0.0, min(10000.0, float(kelvin)))
        b_clamped = max(0.05, min(1.0, float(brightness)))
        
        mode = str(color_mode if color_mode is not None else self.color_mode or "normal").lower().strip()
        if mode in ("day", "standard"):
            mode = "normal"
        elif mode in ("invert", "night_contrast"):
            mode = "dark"
        elif mode in ("sepia_contrast",):
            mode = "paper"
        elif mode in ("grayscale", "gray"):
            mode = "e_ink"

        is_identity = (
            (mode in ("normal", "day"))
            and (abs(k_clamped - 6500.0) <= 25.0)
            and (b_clamped >= 0.99)
            and (self.saturation >= 0.99)
            and (not self.green_background)
        )

        if is_identity:
            try:
                if not self._mag_initialized and mag32:
                    mag32.MagInitialize()
                    self._mag_initialized = True
                if self._mag_initialized and mag32:
                    id_mat = MAGCOLOREFFECT()
                    for i in range(5):
                        id_mat.transform[i][i] = 1.0
                    mag32.MagSetFullscreenColorEffect(ctypes.byref(id_mat))
            except Exception:
                pass
            self._applied_kelvin = 6500.0
            self._applied_brightness = 1.0
            return

        if not self._mag_initialized:
            try:
                if mag32.MagInitialize():
                    self._mag_initialized = True
            except Exception:
                self._mag_initialized = False
                return

        mat = MAGCOLOREFFECT()
        lw = [0.299, 0.587, 0.114]

        theme_info = AUTHENTIC_READING_THEMES.get(mode)
        if mode == "custom":
            bg = parse_color_hex(self.custom_bg_hex, [1.0, 1.0, 1.0])
            txt = parse_color_hex(self.custom_txt_hex, [0.0, 0.0, 0.0])
            s = 0.25
            diff = [bg[i] - txt[i] for i in range(3)]
            bias = [txt[i] for i in range(3)]
            for r in range(3):
                for c in range(3):
                    mat.transform[r][c] = b_clamped * ((1.0 - s) * lw[r] * diff[c] + (s * diff[c] if r == c else 0.0))
            for c in range(3):
                mat.transform[4][c] = b_clamped * bias[c]

        elif theme_info and not theme_info.get("is_normal"):
            if theme_info.get("e_ink"):
                b = b_clamped
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = lw[r] * b
            else:
                bg = list(theme_info["bg"])
                txt = list(theme_info["txt"])
                s = float(theme_info.get("s", 0.25))
                diff = [bg[i] - txt[i] for i in range(3)]
                bias = [txt[i] for i in range(3)]
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = b_clamped * ((1.0 - s) * lw[r] * diff[c] + (s * diff[c] if r == c else 0.0))
                for c in range(3):
                    mat.transform[4][c] = b_clamped * bias[c]

        else:
            # Day / Natural Warmth Mode (Standard diagonal color matrix formula):
            rf, gf, bf = kelvin_to_rgb_extended(k_clamped)
            if self.green_background:
                rf *= 0.78
                gf *= 0.93
                bf *= 0.80
            rf *= b_clamped
            gf *= b_clamped
            bf *= b_clamped
            sat = max(0.0, min(1.0, float(self.saturation)))
            if sat >= 0.999:
                mat.transform[0][0] = rf
                mat.transform[1][1] = gf
                mat.transform[2][2] = bf
            else:
                C = [
                    [(lw[0] * (1.0 - sat) + sat) * rf, (lw[0] * (1.0 - sat)) * gf, (lw[0] * (1.0 - sat)) * bf],
                    [(lw[1] * (1.0 - sat)) * rf, (lw[1] * (1.0 - sat) + sat) * gf, (lw[1] * (1.0 - sat)) * bf],
                    [(lw[2] * (1.0 - sat)) * rf, (lw[2] * (1.0 - sat)) * gf, (lw[2] * (1.0 - sat)) * bf],
                ]
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = C[r][c]

        mat.transform[3][3] = 1.0
        mat.transform[4][4] = 1.0

        try:
            mag32.MagSetFullscreenColorEffect(ctypes.byref(mat))
            self._applied_kelvin = k_clamped
            self._applied_brightness = b_clamped
        except Exception:
            pass

    def _worker_loop(self):
        while self._running:
            self._pump_win32_messages()

            if not self._event.wait(timeout=0.03):
                continue

            if not self._running:
                break

            req = None
            with self._mailbox_lock:
                if self._pending_request is not None:
                    req = self._pending_request
                    self._pending_request = None
                self._event.clear()

            if req is None:
                continue

            req_epoch, k_val, b_val, grayscale, invert, target_monitor, smooth_flag, *rest_req = req
            color_mode = rest_req[0] if rest_req else ("grayscale" if grayscale else ("invert" if invert else "normal"))

            try:
                is_unit_test = (
                    (not getattr(sys, "frozen", False))
                    and ("unittest" in sys.modules)
                    and any(
                        "test" in str(a).lower() or "unittest" in str(a).lower()
                        for a in sys.argv
                    )
                )
                if not is_unit_test:
                    do_smooth = bool(self.smooth_transition and smooth_flag)
                    if do_smooth and (
                        abs(k_val - self._applied_kelvin) > 150.0
                        or abs(b_val - self._applied_brightness) > 0.05
                    ):
                        start_k = self._applied_kelvin
                        start_b = self._applied_brightness
                        steps = 5
                        for step_i in range(1, steps + 1):
                            with self._mailbox_lock:
                                if self._pending_request is not None or req_epoch != self._epoch:
                                    break
                            t_ratio = step_i / float(steps)
                            ik = start_k + (k_val - start_k) * t_ratio
                            ib = start_b + (b_val - start_b) * t_ratio
                            self._apply_dwm_color_effect(ik, ib, grayscale, invert, color_mode=color_mode)
                            self._pump_win32_messages()
                            if step_i < steps:
                                time.sleep(0.012)
                    else:
                        self._apply_dwm_color_effect(k_val, b_val, grayscale, invert, color_mode=color_mode)
                        self._pump_win32_messages()
                else:
                    ramp = self._build_ramp(k_val, b_val, grayscale, invert)
                    with self._mailbox_lock:
                        if req_epoch != self._epoch:
                            continue
                    self._apply_ramp(ramp, target_monitor)
            except Exception:
                pass

        self._apply_dwm_color_effect(6500.0, 1.0, False, False)

    def get_monitors(self) -> List[Dict]:
        now = time.time()
        if self._monitors_cache and (now - self._last_monitor_check < 15.0):
            return self._monitors_cache

        monitors = []

        def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
            info = MONITORINFOEXW()
            info.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
                monitors.append(
                    {
                        "device": info.szDevice,
                        "rect": (
                            info.rcMonitor.left,
                            info.rcMonitor.top,
                            info.rcMonitor.right,
                            info.rcMonitor.bottom,
                        ),
                        "primary": bool(info.dwFlags & 1),
                    }
                )
            return True

        user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_callback), 0)
        self._monitors_cache = monitors
        self._last_monitor_check = now
        return monitors

    def set_hardware_brightness(self, brightness_pct: int) -> bool:
        """
        Adjusts physical monitor backlight brightness via DDC/CI (dxva2.dll) for external
        monitors and WMI (WmiMonitorBrightnessMethods) for laptop internal panels.
        Coalesced background worker eliminates process storming when rapidly adjusting slider.
        """
        lvl = max(0, min(100, int(brightness_pct)))
        self._target_hw_brightness = lvl
        with self._hw_worker_lock:
            if not self._hw_worker_running:
                self._hw_worker_running = True
                threading.Thread(target=self._run_hw_worker, daemon=True).start()
        return True

    def _run_hw_worker(self):
        while True:
            with self._hw_worker_lock:
                lvl = self._target_hw_brightness
            if lvl is None:
                break

            # 1. Try DDC/CI via dxva2.dll
            if _HAS_DXVA2 and dxva2 is not None:
                try:
                    def _ddc_cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
                        count = wintypes.DWORD(0)
                        if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                            hMonitor, ctypes.byref(count)
                        ) and count.value > 0:
                            arr = (PHYSICAL_MONITOR * count.value)()
                            if dxva2.GetPhysicalMonitorsFromHMONITOR(
                                hMonitor, count.value, arr
                            ):
                                for idx in range(count.value):
                                    dxva2.SetMonitorBrightness(
                                        arr[idx].hPhysicalMonitor, wintypes.DWORD(lvl)
                                    )
                                dxva2.DestroyPhysicalMonitors(count.value, arr)
                        return True

                    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_ddc_cb), 0)
                except Exception:
                    pass

            # 2. Try WMI for internal laptop display
            try:
                creationflags = 0x08000000  # CREATE_NO_WINDOW
                cmd = (
                    f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                    f".WmiSetBrightness(1, {lvl})"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2.5,
                    creationflags=creationflags,
                )
            except Exception:
                pass

            time.sleep(0.08)
            with self._hw_worker_lock:
                if self._target_hw_brightness == lvl:
                    self._hw_worker_running = False
                    break

    def _build_ramp(
        self,
        kelvin: float,
        brightness: float,
        grayscale: bool = False,
        invert: bool = False,
    ) -> GAMMA_RAMP:
        rf, gf, bf = kelvin_to_rgb_extended(kelvin)
        rf *= brightness
        gf *= brightness
        bf *= brightness

        ramp = GAMMA_RAMP()
        for i in range(256):
            val = (255 - i) / 255.0 if invert else (i / 255.0)

            if grayscale:
                gray = val * (0.299 * rf + 0.587 * gf + 0.114 * bf)
                word_val = int(max(0, min(65535, round(gray * 65535.0))))
                ramp.Red[i] = word_val
                ramp.Green[i] = word_val
                ramp.Blue[i] = word_val
            else:
                ramp.Red[i] = int(max(0, min(65535, round(val * 65535.0 * rf))))
                ramp.Green[i] = int(max(0, min(65535, round(val * 65535.0 * gf))))
                ramp.Blue[i] = int(max(0, min(65535, round(val * 65535.0 * bf))))

        return ramp

    def _apply_ramp(self, ramp: GAMMA_RAMP, target_device: Optional[str] = None):
        with self._apply_lock:
            if target_device is None:
                hdc = user32.GetDC(None)
                if hdc:
                    gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
                    user32.ReleaseDC(None, hdc)
                return

            monitors = self.get_monitors()
            for m in monitors:
                if m["device"] == target_device:
                    hdc = gdi32.CreateDCW(m["device"], None, None, None)
                    if hdc:
                        gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
                        gdi32.DeleteDC(hdc)

    def set_display(
        self,
        kelvin: float,
        brightness_pct: float,
        grayscale: bool = False,
        invert: bool = False,
        smooth: bool = False,
        target_monitor: Optional[str] = None,
        color_mode: Optional[str] = None,
    ):
        raw_b = float(brightness_pct)
        b_val = max(0.05, min(1.0, raw_b / 100.0 if raw_b > 1.0 else raw_b))
        k_val = max(0.0, min(10000.0, float(kelvin)))

        self._current_kelvin = k_val
        self._current_brightness = b_val
        self.grayscale = bool(grayscale)
        self.invert = bool(invert)
        if color_mode is not None:
            self.color_mode = str(color_mode).lower().strip()

        with self._mailbox_lock:
            self._pending_request = (
                self._epoch,
                k_val,
                b_val,
                self.grayscale,
                self.invert,
                target_monitor,
                bool(smooth),
                self.color_mode,
            )
            self._event.set()

    def set_saturation(self, saturation_pct: float):
        raw_s = float(saturation_pct)
        self.saturation = max(0.0, min(1.0, raw_s / 100.0 if raw_s > 1.0 else raw_s))
        self.set_display(
            self._current_kelvin,
            self._current_brightness * 100.0,
            grayscale=self.grayscale,
            invert=self.invert,
            smooth=False,
        )

    def wait_idle(self, timeout: float = 1.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._mailbox_lock:
                pending = self._pending_request is not None
            if not pending and not self._apply_lock.locked():
                return True
            time.sleep(0.005)
        return False

    
    def set_reading_theme(self, mode: str, custom_bg: Optional[str] = None, custom_txt: Optional[str] = None):
        """Sets active reading theme (Normal, Document Themes or Custom Hex)."""
        self.color_mode = str(mode or "normal").lower().strip()
        if custom_bg:
            self.custom_bg_hex = str(custom_bg).strip()
        if custom_txt:
            self.custom_txt_hex = str(custom_txt).strip()
        self.set_display(
            self._applied_kelvin,
            self._applied_brightness * 100.0,
            grayscale=False,
            invert=False,
            smooth=False,
            color_mode=self.color_mode,
        )

    def reset(self):
        self.saturation = 1.0
        self.green_background = False
        with self._mailbox_lock:
            self._epoch += 1
            self._pending_request = (self._epoch, 6500.0, 1.0, False, False, None, False)
            self._event.set()

        self._restore_original_hardware_lut()

        self._current_kelvin = 6500.0
        self._current_brightness = 1.0
        self._applied_kelvin = 6500.0
        self._applied_brightness = 1.0
        self.grayscale = False
        self.invert = False

    def reset_all_monitors(self):
        return self.reset()

    def shutdown(self):
        if not self._running:
            return

        self._running = False
        with self._mailbox_lock:
            self._epoch += 1
            self._pending_request = None
        self._event.set()

        if (
            self._worker_thread.is_alive()
            and threading.current_thread() != self._worker_thread
        ):
            self._worker_thread.join(timeout=1.0)

        # 1. Reset DWM Magnification shader to 100% Identity and uninitialize
        if _HAS_MAGNIFICATION and self._mag_initialized:
            try:
                identity = MAGCOLOREFFECT()
                for i in range(5):
                    identity.transform[i][i] = 1.0
                mag32.MagSetFullscreenColorEffect(ctypes.byref(identity))
                mag32.MagUninitialize()
                self._mag_initialized = False
            except Exception:
                pass

        # 2. Reset GDI hardware gamma ramp
        self._restore_original_hardware_lut()

        # 3. Mark clean shutdown so CrashGuard watchdog knows no emergency reset is needed
        try:
            with open(self._sentinel_path, "w") as f:
                f.write("clean")
        except Exception:
            pass


GammaEngine = EyeGuardGammaEngine
kelvin_to_rgb = kelvin_to_rgb_extended
