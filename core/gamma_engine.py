import atexit
import ctypes
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from typing import Dict, List, Optional

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

# Load Windows Magnification API for DWM-level 5x5 Color Shader, Reading Themes, Ultra-Dim & Smart Invert
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


def kelvin_to_rgb_extended(kelvin: float, calibration_mode: str = "melanopic"):
    """
    Maps 0K..6500K color temperature to normalized sRGB channel multipliers (r, g, b).
    Uses perceptual melanopic blue-light attenuation across 6500K->1000K so standard
    LCD displays (gamma ~2.2) achieve genuine physical blue-light reduction and warm
    ivory/amber tones while smoothly graduating from pure zero-blue/zero-green ruby red
    at 0K to glowing ember at 1000K.
    """
    k = max(0.0, min(6500.0, float(kelvin)))
    if abs(k - 6500.0) < 5.0:
        return 1.0, 1.0, 1.0

    r = 1.0

    if str(calibration_mode).lower() == "cie":
        if k <= 505.0:
            return 1.0, 0.0, 0.0
        temp = k / 100.0
        g_d65 = (99.4708025861 * math.log(65.0) - 161.1195681661) / 255.0
        if temp <= 19.0:
            g = ((99.4708025861 * math.log(temp) - 161.1195681661) / 255.0) / g_d65
            b = 0.0
        else:
            g = ((99.4708025861 * math.log(temp) - 161.1195681661) / 255.0) / g_d65
            b = (138.5177312231 * math.log(temp - 10.0) - 305.0447927307) / 255.0
        return 1.0, max(0.0, min(1.0, g)), max(0.0, min(1.0, b))

    # Default "melanopic" calibration:
    # - 1900K..6500K: monotonic amber curve with aggressive 450-480nm blue suppression & reduced 555nm glare
    # - 1000K..1900K: smooth convergence from warm amber (G=0.44, B=0.0) to glowing ember (G=0.266, B=0.0)
    # - 0K..1000K: smooth graduation from deep ruby red (G=0.0, B=0.0 at 0K) to glowing ember (G=0.266 at 1000K)
    if k <= 1000.0:
        t0 = max(0.0, min(1.0, k / 1000.0))
        g = 0.266 * (t0 ** 1.25)
        b = 0.0
    elif k <= 1900.0:
        t_sub = max(0.0, min(1.0, (k - 1000.0) / 900.0))
        g = 0.266 + (0.440 - 0.266) * (t_sub ** 1.15)
        b = 0.0
    else:
        u = max(0.0, min(1.0, (k - 1900.0) / 4600.0))
        g = 0.440 + 0.560 * (u ** 0.92)
        b = u ** 2.15

    return r, max(0.0, min(1.0, g)), max(0.0, min(1.0, b))


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

    # --- Xodo PDF Reader Authentic Preset Palettes ---
    "xodo_clean_paper": {"name": "Xodo Clean Paper", "bg": [241 / 255, 243 / 255, 244 / 255], "txt": [54 / 255, 59 / 255, 61 / 255], "s": 0.25},
    "xodo_sage_olive": {"name": "Xodo Sage Olive", "bg": [181 / 255, 197 / 255, 136 / 255], "txt": [49 / 255, 54 / 255, 58 / 255], "s": 0.25},
    "xodo_mint": {"name": "Xodo Mint Celadon", "bg": [191 / 255, 216 / 255, 194 / 255], "txt": [64 / 255, 75 / 255, 64 / 255], "s": 0.25},
    "xodo_ice_blue": {"name": "Xodo Ice Blue", "bg": [208 / 255, 217 / 255, 227 / 255], "txt": [41 / 255, 60 / 255, 88 / 255], "s": 0.25},
    "xodo_sepia": {"name": "Xodo Classic Sepia", "bg": [222 / 255, 206 / 255, 172 / 255], "txt": [91 / 255, 67 / 255, 46 / 255], "s": 0.28},
    "xodo_rose": {"name": "Xodo Rose Blush", "bg": [233 / 255, 205 / 255, 213 / 255], "txt": [170 / 255, 69 / 255, 92 / 255], "s": 0.25},
    "xodo_emerald": {"name": "Xodo Emerald Mint", "bg": [153 / 255, 255 / 255, 204 / 255], "txt": [0.0, 0.0, 0.0], "s": 0.25},
    "xodo_sky_blue": {"name": "Xodo Sky Blue", "bg": [153 / 255, 204 / 255, 255 / 255], "txt": [0.0, 0.0, 0.0], "s": 0.25},
    "xodo_ash": {"name": "Xodo Warm Ash", "bg": [52 / 255, 48 / 255, 46 / 255], "txt": [137 / 255, 135 / 255, 132 / 255], "s": 0.22},
    "xodo_burgundy": {"name": "Xodo Burgundy Wine", "bg": [108 / 255, 45 / 255, 44 / 255], "txt": [252 / 255, 220 / 255, 220 / 255], "s": 0.22},
    "xodo_khaki": {"name": "Xodo Forest Khaki", "bg": [90 / 255, 95 / 255, 55 / 255], "txt": [252 / 255, 220 / 255, 220 / 255], "s": 0.22},
    "xodo_espresso": {"name": "Xodo Espresso Umber", "bg": [64 / 255, 47 / 255, 33 / 255], "txt": [222 / 255, 206 / 255, 172 / 255], "s": 0.25},
    "xodo_midnight": {"name": "Xodo Midnight Oceanic", "bg": [8 / 255, 48 / 255, 69 / 255], "txt": [219 / 255, 225 / 255, 227 / 255], "s": 0.22},
    "xodo_dusk": {"name": "Xodo OLED Dusk", "bg": [53 / 255, 59 / 255, 61 / 255], "txt": [135 / 255, 150 / 255, 153 / 255], "s": 0.22},
    "xodo_console": {"name": "Xodo Terminal Green", "bg": [0.0, 51 / 255, 0.0], "txt": [0.0, 1.0, 0.4], "s": 0.20},
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


def _is_unit_test_env() -> bool:
    return (
        (not getattr(sys, "frozen", False))
        and ("unittest" in sys.modules)
        and any("test" in str(a).lower() or "unittest" in str(a).lower() for a in sys.argv)
    )


class EyeGuardGammaEngine:
    """
    Hybrid Display Engine (Hardware GPU LUT + DWM Magnification 5x5 Shader):
    - Uses per-monitor Hardware GPU Gamma LUT (SetDeviceGammaRamp via CreateDCW) by default for
      zero-compositor-overhead color temperature & dimming down to 20%, natively tinting the
      hardware mouse cursor and preserving DirectFlip / fullscreen frame pacing.
    - Stacks Hardware LUT (20%) with DWM MagSetFullscreenColorEffect for ultra-dimming down to 1%,
      and seamlessly switches to DWM 5x5 shaders for saturation, reading themes, Smart Invert,
      E-Ink grayscale, or drivers that block/clamp hardware gamma ramps (verified via readback).
    - Sleeps at 0 Hz when idle and controls laptop WMI / external DDC-CI backlights in-process.
    """

    MIN_BRIGHTNESS = 0.01
    MIN_HARDWARE_FLOOR = 0.0025
    LUT_SAFE_FLOOR = 0.20
    LUT_READBACK_TOLERANCE = 4096
    LUT_LOW_READBACK_TOLERANCE = 1024
    PERCEPTUAL_DIM_EXPONENT = 1.65

    def __init__(self):
        self._current_kelvin = 6500.0
        self._current_brightness = 1.0
        self._applied_kelvin = 6500.0
        self._applied_brightness = 1.0
        self.saturation = 1.0  # 0.0 (grayscale) .. 1.0 (full color)
        self.color_mode = "normal"
        self.custom_bg_hex = "#FAF0E6"
        self.custom_txt_hex = "#2D3748"
        self.grayscale = False
        self.invert = False
        self.green_background = False
        self.deep_crimson: bool = False
        self.tint_cursor: bool = True
        self.smooth_transition = True
        self.software_filter_mode = False
        self.standard_calibration_mode = True
        self.software_mouse_cursor = True

        self._monitors_cache: List[Dict] = []
        self._last_monitor_check = 0.0

        self._mailbox_lock = threading.Lock()
        self._apply_lock = threading.RLock()
        self._event = threading.Event()
        self._cancel_smooth = threading.Event()
        self._epoch = 0
        self._pending_request: Optional[tuple] = None
        self._in_flight = False

        self._target_hw_brightness: Optional[int] = None
        self._hw_worker_lock = threading.Lock()
        self._hw_worker_running = False
        self._wmi_available: Optional[bool] = None
        self._wmi_mgmt_mod = None
        self._running = True

        self._mag_initialized = False
        self._active_backend = "identity"  # "identity" | "hardware_lut" | "hybrid_lut_dwm" | "software_dwm"
        self._lut_verified: Dict[str, Optional[bool]] = {}
        self._lut_low_verified: Dict[str, Optional[bool]] = {}
        self._monitor_dcs: Dict[str, int] = {}
        self._last_ramp_sig: Dict[str, bytes] = {}
        self._lut_active = False

        self._original_gamma_ramp: Optional[GAMMA_RAMP] = None
        self._original_monitor_ramps: Dict[str, GAMMA_RAMP] = {}
        self._capture_original_hardware_lut()

        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="EyeGuard-GammaWorker",
            daemon=True,
        )
        self._worker_thread.start()

        self._sentinel_path = os.path.join(tempfile.gettempdir(), f"eyeguard_clean_{os.getpid()}.flag")
        if not _is_unit_test_env():
            self._spawn_crash_guard()
        self._install_safety_handlers()
        atexit.register(self.shutdown)

    def get_active_backend(self) -> str:
        return self._active_backend

    def _close_monitor_dc(self, dev: str):
        hdc = self._monitor_dcs.pop(dev, None)
        if hdc:
            try:
                gdi32.DeleteDC(hdc)
            except Exception:
                pass

    def _close_monitor_dcs(self):
        for dev in list(self._monitor_dcs.keys()):
            self._close_monitor_dc(dev)

    def _get_monitor_dc(self, dev: str):
        hdc = self._monitor_dcs.get(dev)
        if hdc:
            return hdc
        hdc = gdi32.CreateDCW(dev, None, None, None)
        if hdc:
            self._monitor_dcs[dev] = hdc
        return hdc

    def reset_lut_capability_cache(self):
        """Clears cached LUT readback checks and monitor DCs (e.g. display mode or monitor change)."""
        self._lut_verified.clear()
        self._lut_low_verified.clear()
        self._last_ramp_sig.clear()
        self._close_monitor_dcs()

    def _build_crash_guard_cmd(self) -> Optional[List[str]]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--crash-guard", str(os.getpid()), self._sentinel_path]
        guard_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash_guard.py")
        if not os.path.exists(guard_script):
            return None
        py_exe = sys.executable
        if "python.exe" in py_exe.lower():
            pyw = py_exe.lower().replace("python.exe", "pythonw.exe")
            if os.path.exists(pyw):
                py_exe = pyw
        return [py_exe, guard_script, str(os.getpid()), self._sentinel_path]

    def _spawn_crash_guard(self):
        """Spawns zero-dependency supervisor to guarantee display recovery on SIGKILL / crash."""
        try:
            cmd = self._build_crash_guard_cmd()
            if not cmd:
                return
            creationflags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, creationflags=creationflags, close_fds=True)
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

    def _make_linear_ramp(self) -> GAMMA_RAMP:
        ramp = GAMMA_RAMP()
        for i in range(256):
            val = min(65535, (i << 8) | i)
            ramp.Red[i] = val
            ramp.Green[i] = val
            ramp.Blue[i] = val
        return ramp

    def _is_valid_baseline_ramp(self, ramp: GAMMA_RAMP) -> bool:
        """Rejects leftover tinted/dimmed filter ramps so software_dwm never double-tints."""
        peaks = (int(ramp.Red[255]), int(ramp.Green[255]), int(ramp.Blue[255]))
        return max(peaks) >= 60000 and min(peaks) >= 35000

    def _capture_original_hardware_lut(self):
        """Captures native monitor hardware LUTs cleanly per monitor (and primary DC fallback)."""
        try:
            for m in self.get_monitors():
                dev = m.get("device")
                if not dev or dev in self._original_monitor_ramps:
                    continue
                hdc = gdi32.CreateDCW(dev, None, None, None)
                if hdc:
                    try:
                        r = GAMMA_RAMP()
                        if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(r)):
                            self._original_monitor_ramps[dev] = (
                                r if self._is_valid_baseline_ramp(r) else self._make_linear_ramp()
                            )
                    finally:
                        gdi32.DeleteDC(hdc)

            hdc = user32.GetDC(None)
            if hdc:
                try:
                    ramp = GAMMA_RAMP()
                    if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                        self._original_gamma_ramp = (
                            ramp if self._is_valid_baseline_ramp(ramp) else self._make_linear_ramp()
                        )
                finally:
                    user32.ReleaseDC(None, hdc)
        except Exception:
            pass

    def _restore_original_hardware_lut(self, target_device: Optional[str] = None):
        """Restores authentic native monitor hardware LUTs or 100% linear calibration."""
        try:
            linear_fallback = self._original_gamma_ramp or self._make_linear_ramp()

            if target_device is not None:
                self._last_ramp_sig.pop(target_device, None)
                ramp = self._original_monitor_ramps.get(target_device, linear_fallback)
                hdc = self._get_monitor_dc(target_device)
                if hdc:
                    if not gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                        self._close_monitor_dc(target_device)
                        hdc = self._get_monitor_dc(target_device)
                        if hdc:
                            gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
                return

            self._last_ramp_sig.clear()
            monitors = self.get_monitors()
            restored_any = False
            if monitors:
                for m in monitors:
                    dev = m.get("device")
                    if not dev:
                        continue
                    ramp = self._original_monitor_ramps.get(dev, linear_fallback)
                    hdc = self._get_monitor_dc(dev)
                    if hdc:
                        if gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                            restored_any = True
                        else:
                            self._close_monitor_dc(dev)
                            hdc = self._get_monitor_dc(dev)
                            if hdc and gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                                restored_any = True

            if not restored_any or not monitors:
                hdc = user32.GetDC(None)
                if hdc:
                    try:
                        gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(linear_fallback))
                    finally:
                        user32.ReleaseDC(None, hdc)
            self._lut_active = False
        except Exception:
            pass

    def _pump_win32_messages(self):
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _ensure_mag_initialized(self) -> bool:
        if not _HAS_MAGNIFICATION or mag32 is None:
            return False
        if not self._mag_initialized:
            self._mag_initialized = bool(mag32.MagInitialize())
        return self._mag_initialized

    def _uninit_dwm(self):
        if not _HAS_MAGNIFICATION or mag32 is None or not self._mag_initialized:
            return
        try:
            identity = MAGCOLOREFFECT()
            for i in range(5):
                identity.transform[i][i] = 1.0
            mag32.MagSetFullscreenColorEffect(ctypes.byref(identity))
            mag32.MagUninitialize()
            self._mag_initialized = False
        except Exception:
            pass

    def _perceptual_brightness(self, brightness: float) -> float:
        """
        Maps UI slider fraction (0.01..1.0) to a perceptually uniform display channel
        multiplier so every step down to 1% produces smooth visual attenuation.
        """
        b = max(self.MIN_BRIGHTNESS, min(1.0, float(brightness)))
        if b >= 0.999:
            return 1.0
        return max(self.MIN_HARDWARE_FLOOR, min(1.0, b ** self.PERCEPTUAL_DIM_EXPONENT))

    def _is_lut_eligible(self, color_mode: str, is_gray: bool, is_invert: bool, sat: float = 1.0) -> bool:
        return (
            (not self.software_filter_mode)
            and (color_mode in ("normal", "day"))
            and (not is_gray)
            and (not is_invert)
        )

    def _normalize_color_mode(self, color_mode: Optional[str]) -> str:
        mode = str(color_mode if color_mode is not None else self.color_mode or "normal").lower().strip()
        if mode in ("day", "standard"):
            return "normal"
        if mode in ("invert", "night_contrast"):
            return "dark"
        if mode in ("sepia_contrast",):
            return "paper"
        if mode in ("grayscale", "gray"):
            return "e_ink"
        return mode

    def _apply_dwm_color_effect(
        self,
        kelvin: float,
        brightness: float,
        is_gray: bool,
        is_invert: bool,
        color_mode: Optional[str] = None,
        saturation_override: Optional[float] = None,
        green_bg_override: Optional[bool] = None,
        deep_crimson_override: Optional[bool] = None,
        lut_scale: Optional[Tuple[float, float, float]] = None,
    ):
        """
        Applies GPU-composited 5x5 DWM Magnification matrix (MagSetFullscreenColorEffect).
        Supports Reading Themes, saturation, E-Ink grayscale, Smart Invert, ultra-dimming down to 1%,
        and neutral stacked brightness scaling on top of hardware LUTs.
        """
        if not _HAS_MAGNIFICATION:
            return

        use_crimson = self.deep_crimson if deep_crimson_override is None else bool(deep_crimson_override)
        k_clamped = max(0.0, min(6500.0, float(kelvin)))
        b_clamped = max(self.MIN_HARDWARE_FLOOR, min(1.0, float(brightness)))
        mode = self._normalize_color_mode(color_mode)
        sat = max(0.0, min(1.0, float(self.saturation if saturation_override is None else saturation_override)))
        use_green_bg = self.green_background if green_bg_override is None else bool(green_bg_override)

        is_identity = (
            (mode in ("normal", "day"))
            and (abs(k_clamped - 6500.0) <= 25.0)
            and (b_clamped >= 0.99)
            and (sat >= 0.99)
            and (not is_gray)
            and (not is_invert)
            and (not use_green_bg)
            and (not use_crimson)
        )

        if is_identity:
            self._uninit_dwm()
            return

        if not self._ensure_mag_initialized():
            return

        mat = MAGCOLOREFFECT()
        lw = [0.299, 0.587, 0.114]
        rf, gf, bf = kelvin_to_rgb_extended(k_clamped)
        if use_green_bg:
            rf *= 0.78
            gf *= 0.93
            bf *= 0.80

        theme_info = AUTHENTIC_READING_THEMES.get(mode)
        if mode == "custom":
            bg = parse_color_hex(self.custom_bg_hex, [1.0, 1.0, 1.0])
            txt = parse_color_hex(self.custom_txt_hex, [0.0, 0.0, 0.0])
            s = 0.0 if is_gray else 0.25
            bg_k = [bg[0] * rf, bg[1] * gf, bg[2] * bf]
            txt_k = [txt[0] * rf, txt[1] * gf, txt[2] * bf]
            diff = [bg_k[i] - txt_k[i] for i in range(3)]
            bias = [txt_k[i] for i in range(3)]
            for r in range(3):
                for c in range(3):
                    mat.transform[r][c] = b_clamped * ((1.0 - s) * lw[r] * diff[c] + (s * diff[c] if r == c else 0.0))
            for c in range(3):
                mat.transform[4][c] = b_clamped * bias[c]

        elif theme_info and not theme_info.get("is_normal"):
            if theme_info.get("e_ink"):
                for r in range(3):
                    for c in range(3):
                        tint = [rf, gf, bf][c]
                        mat.transform[r][c] = lw[r] * b_clamped * tint
            else:
                bg = list(theme_info["bg"])
                txt = list(theme_info["txt"])
                s = 0.0 if is_gray else float(theme_info.get("s", 0.25))
                bg_k = [bg[0] * rf, bg[1] * gf, bg[2] * bf]
                txt_k = [txt[0] * rf, txt[1] * gf, txt[2] * bf]
                diff = [bg_k[i] - txt_k[i] for i in range(3)]
                bias = [txt_k[i] for i in range(3)]
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = b_clamped * ((1.0 - s) * lw[r] * diff[c] + (s * diff[c] if r == c else 0.0))
                for c in range(3):
                    mat.transform[4][c] = b_clamped * bias[c]

        else:
            rf *= b_clamped
            gf *= b_clamped
            bf *= b_clamped

            eff_sat = 0.0 if is_gray else sat
            if eff_sat >= 0.999:
                c_mat = [
                    [rf, 0.0, 0.0],
                    [0.0, gf, 0.0],
                    [0.0, 0.0, bf],
                ]
            else:
                c_mat = [
                    [(lw[0] * (1.0 - eff_sat) + eff_sat) * rf, (lw[0] * (1.0 - eff_sat)) * gf, (lw[0] * (1.0 - eff_sat)) * bf],
                    [(lw[1] * (1.0 - eff_sat)) * rf, (lw[1] * (1.0 - eff_sat) + eff_sat) * gf, (lw[1] * (1.0 - eff_sat)) * bf],
                    [(lw[2] * (1.0 - eff_sat)) * rf, (lw[2] * (1.0 - eff_sat)) * gf, (lw[2] * (1.0 - eff_sat) + eff_sat) * bf],
                ]

            if is_invert:
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = -c_mat[r][c]
                mat.transform[4][0] = rf
                mat.transform[4][1] = gf
                mat.transform[4][2] = bf
            else:
                for r in range(3):
                    for c in range(3):
                        mat.transform[r][c] = c_mat[r][c]

        if use_crimson:
            for r in range(5):
                mat.transform[r][0] *= 0.78
                mat.transform[r][1] = 0.0
                mat.transform[r][2] = 0.0

        if lut_scale is not None:
            scales = [lut_scale[0], lut_scale[1], lut_scale[2]]
            for c in range(3):
                sc = scales[c]
                if sc > 1e-6:
                    inv_sc = 1.0 / sc
                    for r in range(5):
                        mat.transform[r][c] *= inv_sc
                else:
                    for r in range(5):
                        mat.transform[r][c] = 0.0

        mat.transform[3][3] = 1.0
        mat.transform[4][4] = 1.0

        try:
            mag32.MagSetFullscreenColorEffect(ctypes.byref(mat))
        except Exception:
            pass

    def _should_clamp_lut_floor(self, target_monitor: Optional[str] = None) -> bool:
        """Returns True if any target display driver clamped sub-20% LUTs during readback."""
        if target_monitor is not None:
            return self._lut_low_verified.get(target_monitor) is False
        if not self.standard_calibration_mode:
            return self._lut_low_verified.get("__primary__") is False
        monitors = self.get_monitors()
        if monitors:
            for m in monitors:
                dev = m.get("device")
                if dev and self._lut_low_verified.get(dev) is False:
                    return True
            return False
        return self._lut_low_verified.get("__primary__") is False

    def _apply_display_state(
        self,
        kelvin: float,
        brightness: float,
        grayscale: bool,
        invert: bool,
        target_monitor: Optional[str],
        color_mode: str,
    ):
        with self._apply_lock:
            k_val = max(0.0, min(6500.0, float(kelvin)))
            b_val = max(self.MIN_BRIGHTNESS, min(1.0, float(brightness)))
            eff_b = self._perceptual_brightness(b_val)
            mode = self._normalize_color_mode(color_mode)
            sat = max(0.0, min(1.0, float(self.saturation)))

            is_clean_identity = (
                (mode in ("normal", "day"))
                and (abs(k_val - 6500.0) <= 25.0)
                and (b_val >= 0.99)
                and (sat >= 0.99)
                and (not grayscale)
                and (not invert)
                and (not self.green_background)
                and (not self.deep_crimson)
            )

            if is_clean_identity:
                self._uninit_dwm()
                self._restore_original_hardware_lut(target_monitor)
                self._active_backend = "identity"
                self._applied_kelvin = 6500.0
                self._applied_brightness = 1.0
                return

            clamp_floor = self._should_clamp_lut_floor(target_monitor)

            # Normal full-sat path (Normal mode, sat >= 0.99, not grayscale, not invert, not software_filter_mode)
            # When self.tint_cursor is True: runs on 100% pure hardware_lut (0% DWM overhead)
            if self.tint_cursor and (not self.software_filter_mode) and self._is_lut_eligible(mode, grayscale, invert, sat) and sat >= 0.99:
                lut_b = max(self.LUT_SAFE_FLOOR, eff_b) if clamp_floor else eff_b
                ramp = self._build_ramp(
                    k_val,
                    lut_b,
                    grayscale=False,
                    invert=False,
                    apply_green_bg=self.green_background,
                    deep_crimson=self.deep_crimson,
                )
                ramp_ok = self._apply_ramp(ramp, target_monitor)
                if (not ramp_ok) and eff_b < self.LUT_SAFE_FLOOR and self._should_clamp_lut_floor(target_monitor):
                    lut_b = self.LUT_SAFE_FLOOR
                    ramp = self._build_ramp(
                        k_val,
                        lut_b,
                        grayscale=False,
                        invert=False,
                        apply_green_bg=self.green_background,
                        deep_crimson=self.deep_crimson,
                    )
                    ramp_ok = self._apply_ramp(ramp, target_monitor)

                if ramp_ok:
                    self._lut_active = True
                    needs_dwm_dim = lut_b > (eff_b + 1e-6)
                    if not needs_dwm_dim:
                        self._uninit_dwm()
                        self._active_backend = "hardware_lut"
                        self._applied_kelvin = k_val
                        self._applied_brightness = b_val
                        return
                    else:
                        rem_b = max(self.MIN_HARDWARE_FLOOR, min(1.0, eff_b / max(self.MIN_HARDWARE_FLOOR, lut_b)))
                        self._apply_dwm_color_effect(
                            6500.0,
                            rem_b,
                            is_gray=False,
                            is_invert=False,
                            color_mode="normal",
                            saturation_override=sat,
                            green_bg_override=False,
                            deep_crimson_override=False,
                            lut_scale=None,
                        )
                        self._active_backend = "hybrid_lut_dwm"
                        self._applied_kelvin = k_val
                        self._applied_brightness = b_val
                        return

            # Generalized Hybrid LUT+DWM (Themes, Invert, Grayscale, sat < 1.0, or sub-20% dimming)
            # Tints hardware cursor across ALL modes by loading peak ambient tone into GPU LUT
            # and normalizing DWM color matrix by the LUT scale.
            if self.tint_cursor and (not self.software_filter_mode):
                rf, gf, bf = kelvin_to_rgb_extended(k_val)
                if self.green_background:
                    rf *= 0.78
                    gf *= 0.93
                    bf *= 0.80

                base_rgb = [0.78, 0.0, 0.0] if self.deep_crimson else [rf, gf, bf]

                theme_info = AUTHENTIC_READING_THEMES.get(mode)
                if mode == "custom":
                    bg = parse_color_hex(self.custom_bg_hex, [1.0, 1.0, 1.0])
                    txt = parse_color_hex(self.custom_txt_hex, [0.0, 0.0, 0.0])
                    peak_rgb = [max(bg[c], txt[c]) * base_rgb[c] * eff_b for c in range(3)]
                elif theme_info and not theme_info.get("is_normal"):
                    if theme_info.get("e_ink"):
                        peak_rgb = [base_rgb[c] * eff_b for c in range(3)]
                    else:
                        bg = list(theme_info.get("bg", [1.0, 1.0, 1.0]))
                        txt = list(theme_info.get("txt", [0.0, 0.0, 0.0]))
                        peak_rgb = [max(bg[c], txt[c]) * base_rgb[c] * eff_b for c in range(3)]
                else:
                    # e_ink, invert, grayscale, normal+sat<1
                    peak_rgb = [base_rgb[c] * eff_b for c in range(3)]

                floor_val = self.LUT_SAFE_FLOOR if clamp_floor else self.MIN_HARDWARE_FLOOR
                lut_rgb = [
                    0.0 if peak_rgb[c] <= 0.0 else max(floor_val, min(1.0, peak_rgb[c]))
                    for c in range(3)
                ]

                # Note: When deep_crimson=True with DWM modes (invert/themes), LUT is linear (peak_r, 0, 0)
                ramp = self._build_rgb_ramp(lut_rgb[0], lut_rgb[1], lut_rgb[2], deep_crimson=False)
                ramp_ok = self._apply_ramp(ramp, target_monitor)
                if (not ramp_ok) and self._should_clamp_lut_floor(target_monitor):
                    lut_rgb = [
                        0.0 if peak_rgb[c] <= 0.0 else max(self.LUT_SAFE_FLOOR, min(1.0, peak_rgb[c]))
                        for c in range(3)
                    ]
                    ramp = self._build_rgb_ramp(lut_rgb[0], lut_rgb[1], lut_rgb[2], deep_crimson=False)
                    ramp_ok = self._apply_ramp(ramp, target_monitor)

                if ramp_ok:
                    self._lut_active = True
                    self._apply_dwm_color_effect(
                        k_val,
                        eff_b,
                        grayscale,
                        invert,
                        color_mode=mode,
                        saturation_override=sat,
                        green_bg_override=self.green_background,
                        deep_crimson_override=self.deep_crimson,
                        lut_scale=(lut_rgb[0], lut_rgb[1], lut_rgb[2]),
                    )
                    self._active_backend = "hybrid_lut_dwm"
                    self._applied_kelvin = k_val
                    self._applied_brightness = b_val
                    return

            # Full Software DWM fallback (tint_cursor=False, software_filter_mode=True, or LUT blocked by driver)
            # Hardware LUT is restored to 100% linear, rendering Windows hardware cursor crisp un-tinted white.
            if self._lut_active:
                self._restore_original_hardware_lut(target_monitor)
            self._apply_dwm_color_effect(
                k_val,
                eff_b,
                grayscale,
                invert,
                color_mode=mode,
                deep_crimson_override=self.deep_crimson,
                lut_scale=None,
            )
            self._active_backend = "software_dwm"
            self._applied_kelvin = k_val
            self._applied_brightness = b_val

    def _worker_loop(self):
        while self._running:
            self._event.wait()
            if not self._running:
                break

            req = None
            with self._mailbox_lock:
                if self._pending_request is not None:
                    req = self._pending_request
                    self._pending_request = None
                    self._in_flight = True
                self._cancel_smooth.clear()
                self._event.clear()

            if req is None:
                continue

            req_epoch, k_val, b_val, grayscale, invert, target_monitor, smooth_flag, *rest_req = req
            color_mode = rest_req[0] if rest_req else ("grayscale" if grayscale else ("invert" if invert else "normal"))

            try:
                self._pump_win32_messages()
                do_smooth = bool(self.smooth_transition and smooth_flag)
                if do_smooth and (
                    abs(k_val - self._applied_kelvin) > 150.0
                    or abs(b_val - self._applied_brightness) > 0.05
                ):
                    start_k = self._applied_kelvin
                    start_b = self._applied_brightness
                    steps = 10
                    for step_i in range(1, steps + 1):
                        with self._mailbox_lock:
                            if self._pending_request is not None or req_epoch != self._epoch or not self._running:
                                break
                        t_step0 = time.perf_counter()
                        t_ratio = step_i / float(steps)
                        ease = 1.0 - (1.0 - t_ratio) ** 2
                        ik = start_k + (k_val - start_k) * ease
                        ib = start_b + (b_val - start_b) * ease
                        self._apply_display_state(ik, ib, grayscale, invert, target_monitor, color_mode)
                        self._pump_win32_messages()
                        if step_i < steps:
                            rem_wait = 0.016 - (time.perf_counter() - t_step0)
                            if rem_wait > 0.0005 and self._cancel_smooth.wait(timeout=rem_wait):
                                break
                else:
                    self._apply_display_state(k_val, b_val, grayscale, invert, target_monitor, color_mode)
                    self._pump_win32_messages()
            except Exception:
                pass
            finally:
                with self._mailbox_lock:
                    self._in_flight = False

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

    def _get_wmi_mgmt(self):
        if self._wmi_mgmt_mod is not None:
            return self._wmi_mgmt_mod
        import clr
        clr.AddReference("System.Management")
        import System.Management as mgmt
        self._wmi_mgmt_mod = mgmt
        return mgmt

    def get_hardware_brightness(self) -> Optional[int]:
        """Reads physical backlight level (0..100) via in-process .NET WMI or DDC/CI."""
        if self._wmi_available is not False:
            try:
                mgmt = self._get_wmi_mgmt()
                mc = mgmt.ManagementClass(r"\\.\root\wmi:WmiMonitorBrightness")
                instances = mc.GetInstances()
                found = False
                for obj in instances:
                    found = True
                    val = int(obj["CurrentBrightness"])
                    self._wmi_available = True
                    return max(0, min(100, val))
                if not found:
                    self._wmi_available = False
            except ImportError:
                pass
            except Exception:
                pass

        if _HAS_DXVA2 and dxva2 is not None:
            ddc_val: List[int] = []
            try:
                def _ddc_get_cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    count = wintypes.DWORD(0)
                    if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                        hMonitor, ctypes.byref(count)
                    ) and count.value > 0:
                        arr = (PHYSICAL_MONITOR * count.value)()
                        if dxva2.GetPhysicalMonitorsFromHMONITOR(hMonitor, count.value, arr):
                            try:
                                lo, cur, hi = wintypes.DWORD(0), wintypes.DWORD(0), wintypes.DWORD(0)
                                if dxva2.GetMonitorBrightness(
                                    arr[0].hPhysicalMonitor, ctypes.byref(lo), ctypes.byref(cur), ctypes.byref(hi)
                                ) and hi.value > lo.value:
                                    pct = round((cur.value - lo.value) * 100.0 / (hi.value - lo.value))
                                    ddc_val.append(max(0, min(100, int(pct))))
                            finally:
                                dxva2.DestroyPhysicalMonitors(count.value, arr)
                    return len(ddc_val) == 0

                user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_ddc_get_cb), 0)
            except Exception:
                pass
            if ddc_val:
                return ddc_val[0]
        return None

    def set_hardware_brightness(self, brightness_pct: int) -> bool:
        """
        Adjusts physical monitor backlight brightness via DDC/CI (dxva2.dll) for external
        monitors and in-process .NET System.Management WMI for laptop internal panels.
        Coalesced background worker eliminates process storming when rapidly adjusting slider.
        """
        lvl = max(0, min(100, int(brightness_pct)))
        with self._hw_worker_lock:
            self._target_hw_brightness = lvl
            if not self._hw_worker_running:
                self._hw_worker_running = True
                threading.Thread(
                    target=self._run_hw_worker,
                    name="EyeGuard-HWBacklight",
                    daemon=True,
                ).start()
        return True

    def _set_wmi_brightness_once(self, lvl: int) -> bool:
        if self._wmi_available is False:
            return False
        try:
            mgmt = self._get_wmi_mgmt()
            import System
            mc = mgmt.ManagementClass(r"\\.\root\wmi:WmiMonitorBrightnessMethods")
            instances = mc.GetInstances()
            applied = False
            for obj in instances:
                applied = True
                obj.InvokeMethod(
                    "WmiSetBrightness",
                    [System.UInt32(1), System.Byte(lvl)],
                )
            self._wmi_available = applied
            return applied
        except ImportError:
            return False
        except Exception:
            return False

    def _run_hw_worker(self):
        while True:
            with self._hw_worker_lock:
                lvl = self._target_hw_brightness
            if lvl is None:
                with self._hw_worker_lock:
                    self._hw_worker_running = False
                break

            # 1. External monitors via DDC/CI
            if _HAS_DXVA2 and dxva2 is not None:
                try:
                    def _ddc_cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
                        count = wintypes.DWORD(0)
                        if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(
                            hMonitor, ctypes.byref(count)
                        ) and count.value > 0:
                            arr = (PHYSICAL_MONITOR * count.value)()
                            if dxva2.GetPhysicalMonitorsFromHMONITOR(hMonitor, count.value, arr):
                                try:
                                    for idx in range(count.value):
                                        dxva2.SetMonitorBrightness(
                                            arr[idx].hPhysicalMonitor, wintypes.DWORD(lvl)
                                        )
                                finally:
                                    dxva2.DestroyPhysicalMonitors(count.value, arr)
                        return True

                    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_ddc_cb), 0)
                except Exception:
                    pass

            # 2. Internal laptop panels via in-process .NET WMI (~25ms, zero subprocesses)
            wmi_handled = self._set_wmi_brightness_once(lvl)

            # Emergency fallback to powershell.exe ONLY if pythonnet/clr cannot be imported at all
            if not wmi_handled and self._wmi_available is None:
                try:
                    creationflags = 0x08000000
                    cmd = (
                        f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                        f".WmiSetBrightness(1, {lvl})"
                    )
                    res = subprocess.run(
                        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", cmd],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2.5,
                        creationflags=creationflags,
                    )
                    if res.returncode != 0:
                        self._wmi_available = False
                except Exception:
                    self._wmi_available = False

            time.sleep(0.05)
            with self._hw_worker_lock:
                if self._target_hw_brightness == lvl:
                    self._hw_worker_running = False
                    break

    def _build_rgb_ramp(
        self,
        rf: float,
        gf: float,
        bf: float,
        deep_crimson: bool = False,
        invert: bool = False,
        grayscale: bool = False,
    ) -> GAMMA_RAMP:
        ramp = GAMMA_RAMP()
        for i in range(256):
            val = (255 - i) / 255.0 if invert else (i / 255.0)
            if deep_crimson:
                shaped = (val ** 1.55) * 0.78
                red_word = int(max(0, min(65535, round(shaped * 65535.0 * rf))))
                ramp.Red[i] = red_word
                ramp.Green[i] = 0
                ramp.Blue[i] = 0
            elif grayscale:
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

    def _build_ramp(
        self,
        kelvin: float,
        brightness: float,
        grayscale: bool = False,
        invert: bool = False,
        apply_green_bg: Optional[bool] = None,
        deep_crimson: Optional[bool] = None,
    ) -> GAMMA_RAMP:
        use_crimson = self.deep_crimson if deep_crimson is None else bool(deep_crimson)
        rf, gf, bf = kelvin_to_rgb_extended(kelvin)
        use_green = self.green_background if apply_green_bg is None else bool(apply_green_bg)
        if use_green:
            rf *= 0.78
            gf *= 0.93
            bf *= 0.80

        b = max(self.MIN_HARDWARE_FLOOR, min(1.0, float(brightness)))
        rf *= b
        gf *= b
        bf *= b

        if use_crimson:
            return self._build_rgb_ramp(b, 0.0, 0.0, deep_crimson=True, invert=invert, grayscale=grayscale)

        return self._build_rgb_ramp(rf, gf, bf, deep_crimson=False, invert=invert, grayscale=grayscale)

    def _apply_ramp_to_hdc(
        self,
        hdc: wintypes.HDC,
        key: str,
        ramp: GAMMA_RAMP,
        dev_for_reopen: Optional[str] = None,
    ) -> bool:
        if self._lut_verified.get(key) is False:
            return False

        sig = bytes(ramp)
        is_low_ramp = (
            max(int(ramp.Red[255]), int(ramp.Green[255]), int(ramp.Blue[255]))
            < (int(self.LUT_SAFE_FLOOR * 65535) - 256)
        )
        if (
            self._lut_verified.get(key) is True
            and (not is_low_ramp or self._lut_low_verified.get(key) is True)
            and self._last_ramp_sig.get(key) == sig
        ):
            return True

        ok = bool(gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)))
        if (not ok) and dev_for_reopen is not None:
            self._close_monitor_dc(dev_for_reopen)
            fresh_hdc = self._get_monitor_dc(dev_for_reopen)
            if fresh_hdc:
                hdc = fresh_hdc
                ok = bool(gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)))

        if not ok:
            self._lut_verified[key] = False
            self._last_ramp_sig.pop(key, None)
            if dev_for_reopen is not None:
                self._close_monitor_dc(dev_for_reopen)
            return False

        is_non_identity = (
            ramp.Red[255] < 61000 or ramp.Green[255] < 61000 or ramp.Blue[255] < 61000
        )
        needs_low_verify = is_low_ramp and self._lut_low_verified.get(key) is None
        needs_base_verify = is_non_identity and self._lut_verified.get(key) is None
        if needs_low_verify or needs_base_verify:
            rb = GAMMA_RAMP()
            if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(rb)):
                err = max(
                    abs(int(rb.Red[255]) - int(ramp.Red[255])),
                    abs(int(rb.Green[255]) - int(ramp.Green[255])),
                    abs(int(rb.Blue[255]) - int(ramp.Blue[255])),
                )
                if needs_low_verify:
                    if err > self.LUT_LOW_READBACK_TOLERANCE:
                        self._lut_low_verified[key] = False
                        self._last_ramp_sig.pop(key, None)
                        return False
                    self._lut_low_verified[key] = True
                    if needs_base_verify:
                        self._lut_verified[key] = True
                elif needs_base_verify:
                    if err > self.LUT_READBACK_TOLERANCE:
                        self._lut_verified[key] = False
                        self._last_ramp_sig.pop(key, None)
                        return False
                    self._lut_verified[key] = True
            else:
                if needs_low_verify:
                    self._lut_low_verified[key] = True
                if needs_base_verify:
                    self._lut_verified[key] = True

        self._last_ramp_sig[key] = sig
        return True

    def _apply_for_monitor(self, dev: str, ramp: GAMMA_RAMP) -> bool:
        hdc = self._get_monitor_dc(dev)
        if not hdc:
            return False
        if dev not in self._original_monitor_ramps:
            orig = GAMMA_RAMP()
            if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(orig)):
                self._original_monitor_ramps[dev] = orig
        return self._apply_ramp_to_hdc(hdc, dev, ramp, dev_for_reopen=dev)

    def _apply_ramp(self, ramp: GAMMA_RAMP, target_device: Optional[str] = None) -> bool:
        with self._apply_lock:
            # standard_calibration_mode=False tries primary GetDC(None) directly when user toggles compatibility
            if target_device is None and not self.standard_calibration_mode:
                hdc = user32.GetDC(None)
                if not hdc:
                    return False
                try:
                    return self._apply_ramp_to_hdc(hdc, "__primary__", ramp)
                finally:
                    user32.ReleaseDC(None, hdc)

            monitors = self.get_monitors()
            if target_device is not None:
                for m in monitors:
                    if m["device"] == target_device:
                        return self._apply_for_monitor(target_device, ramp)
                return False

            if monitors and self.standard_calibration_mode:
                all_ok = True
                applied_any = False
                for m in monitors:
                    dev = m.get("device")
                    if not dev:
                        continue
                    applied_any = True
                    if not self._apply_for_monitor(dev, ramp):
                        all_ok = False
                if applied_any:
                    if not all_ok:
                        self._restore_original_hardware_lut(None)
                    return all_ok

            hdc = user32.GetDC(None)
            if hdc:
                try:
                    return self._apply_ramp_to_hdc(hdc, "__primary__", ramp)
                finally:
                    user32.ReleaseDC(None, hdc)
            return False

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
        if 0.0 < raw_b < 1.0 and not isinstance(brightness_pct, int):
            norm_b = raw_b
        else:
            norm_b = raw_b / 100.0
        b_val = max(self.MIN_BRIGHTNESS, min(1.0, norm_b))
        k_val = max(0.0, min(6500.0, float(kelvin)))

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
            self._cancel_smooth.set()
            self._event.set()

    def set_saturation(self, saturation_pct: float):
        raw_s = float(saturation_pct)
        if 0.0 < raw_s < 1.0 and not isinstance(saturation_pct, int):
            norm_s = raw_s
        else:
            norm_s = raw_s / 100.0
        self.saturation = max(0.0, min(1.0, norm_s))
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
                busy = (self._pending_request is not None) or self._in_flight
            if not busy:
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
            self._current_kelvin,
            self._current_brightness * 100.0,
            grayscale=False,
            invert=False,
            smooth=False,
            color_mode=self.color_mode,
        )

    def set_deep_crimson(self, enabled: bool):
        """Toggles pure-hardware-LUT Deep Crimson mode (G=0/B=0, non-linear glare taming)."""
        self.deep_crimson = bool(enabled)
        return self.set_display(
            self._current_kelvin,
            self._current_brightness * 100.0,
            grayscale=self.grayscale,
            invert=self.invert,
            smooth=False,
            color_mode=self.color_mode,
        )

    def set_tint_cursor(self, enabled: bool):
        """Sets whether the hardware mouse cursor is tinted to match the active screen filter/theme."""
        self.tint_cursor = bool(enabled)
        self.software_mouse_cursor = self.tint_cursor
        return self.set_display(
            self._current_kelvin,
            self._current_brightness * 100.0,
            grayscale=self.grayscale,
            invert=self.invert,
            smooth=False,
            color_mode=self.color_mode,
        )

    def reset(self):
        self.saturation = 1.0
        self.green_background = False
        self.deep_crimson = False
        self.color_mode = "normal"
        self.grayscale = False
        self.invert = False
        self._current_kelvin = 6500.0
        self._current_brightness = 1.0
        self._applied_kelvin = 6500.0
        self._applied_brightness = 1.0

        with self._mailbox_lock:
            self._epoch += 1
            self._pending_request = (self._epoch, 6500.0, 1.0, False, False, None, False, "normal")
            self._cancel_smooth.set()
            self._event.set()

        with self._apply_lock:
            self._restore_original_hardware_lut()

    def reset_all_monitors(self):
        return self.reset()

    def shutdown(self):
        if not self._running:
            return

        self._running = False
        with self._mailbox_lock:
            self._epoch += 1
            self._pending_request = None
            self._cancel_smooth.set()
        self._event.set()

        if self._worker_thread.is_alive() and threading.current_thread() != self._worker_thread:
            self._worker_thread.join(timeout=1.0)

        self._uninit_dwm()
        self._restore_original_hardware_lut()
        self._close_monitor_dcs()
        self._active_backend = "identity"

        try:
            with open(self._sentinel_path, "w") as f:
                f.write("clean")
        except Exception:
            pass


GammaEngine = EyeGuardGammaEngine
kelvin_to_rgb = kelvin_to_rgb_extended
