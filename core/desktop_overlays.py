"""
EyeGuard — Native Win32 Desktop Overlays & Audio Engine (core/desktop_overlays.py)
Provides true standalone Win32 Topmost Desktop Windows that operate independently
even when the main PyWebView Control Panel is minimized to the System Tray:
  1. DesktopMiniWindow: Draggable floating timer HUD ('bar' | 'window') with auto-translucency.
  2. BlinkAndPromptOverlay: Click-through topmost notification banner ('top_center' | 'bottom_right')
     for Blink Reminders (every 60s) and Pre-Break Countdown Prompts (10s/20s/30s/60s).
  3. MagicTitlebarToolbar: Floating quick-action bar attached to the foreground window's titlebar
     with 4 interactive buttons: [Dark/Invert], [Grayscale], [Dim 80%], [Pin Always-on-Top].
  4. BreakAudioPlayer: Native Windows MCI (winmm.dll) & WAV generator for alert sounds and break music.
"""

import ctypes
from ctypes import wintypes
import math
import os
import queue
import struct
import sys
import threading
import time
import wave
from typing import Callable, Dict, Optional

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi
winmm = ctypes.windll.winmm

# Win32 Constants
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

LWA_ALPHA = 0x00000002
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
HWND_TOPMOST = ctypes.c_void_p(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001

WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
HTCLIENT = 1

PM_REMOVE = 0x0001
SPI_GETWORKAREA = 0x0030
DT_LEFT = 0x0000
DT_CENTER = 0x0001
DT_RIGHT = 0x0002
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
TRANSPARENT = 1
NULL_BRUSH = 5
NULL_PEN = 8
DWMWA_EXTENDED_FRAME_BOUNDS = 9

if ctypes.sizeof(ctypes.c_void_p) == 8:
    LRESULT = ctypes.c_int64
else:
    LRESULT = ctypes.c_long

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", wintypes.RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", wintypes.BYTE * 32),
    ]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.RegisterClassExW.restype = wintypes.ATOM

gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int

gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = wintypes.HPEN
gdi32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.RoundRect.restype = wintypes.BOOL
gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Ellipse.restype = wintypes.BOOL
gdi32.Arc.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Arc.restype = wintypes.BOOL
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HGDIOBJ

gdiplus = ctypes.windll.gdiplus


class GdiplusStartupInput(ctypes.Structure):
    _fields_ = [
        ("GdiplusVersion", ctypes.c_uint32),
        ("DebugEventCallback", ctypes.c_void_p),
        ("SuppressBackgroundThread", ctypes.c_bool),
        ("SuppressExternalCodecs", ctypes.c_bool),
    ]


class RectF(ctypes.Structure):
    _fields_ = [
        ("X", ctypes.c_float),
        ("Y", ctypes.c_float),
        ("Width", ctypes.c_float),
        ("Height", ctypes.c_float),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdiplus.GdipCreateFromHDC.argtypes = [wintypes.HDC, ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipCreateFromHDC.restype = ctypes.c_int
gdiplus.GdipNewPrivateFontCollection.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipNewPrivateFontCollection.restype = ctypes.c_int
gdiplus.GdipDeletePrivateFontCollection.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
gdiplus.GdipDeletePrivateFontCollection.restype = ctypes.c_int
gdiplus.GdipPrivateAddFontFile.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
gdiplus.GdipPrivateAddFontFile.restype = ctypes.c_int
gdiplus.GdipCreateFontFamilyFromName.argtypes = [
    ctypes.c_wchar_p,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p),
]
gdiplus.GdipCreateFontFamilyFromName.restype = ctypes.c_int
gdiplus.GdipCreateFont.argtypes = [
    ctypes.c_void_p,
    ctypes.c_float,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
]
gdiplus.GdipCreateFont.restype = ctypes.c_int

gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC


def hex_to_colorref(hex_str: str) -> int:
    s = str(hex_str or "#0d9488").strip().lstrip("#")
    if len(s) != 6:
        s = "0d9488"
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (b << 16) | (g << 8) | r
    except ValueError:
        return 0x0088940D


class BreakAudioPlayer:
    """
    Generates default soothing WAV audio files if none exist and plays
    both .wav and .mp3 files via native Windows MCI (winmm.dll).
    """

    def __init__(self, appdata_dir: str) -> None:
        self.appdata_dir = appdata_dir
        os.makedirs(self.appdata_dir, exist_ok=True)
        self.default_alert_wav = os.path.join(self.appdata_dir, "vg_alert_chime.wav")
        self.default_ambient_wav = os.path.join(self.appdata_dir, "vg_ambient_break.wav")
        self._ensure_default_audio_files()
        self._playing_bgm = False

    def _ensure_default_audio_files(self) -> None:
        try:
            if not os.path.isfile(self.default_alert_wav):
                self._synthesize_chime_wav(self.default_alert_wav)
            if not os.path.isfile(self.default_ambient_wav):
                self._synthesize_ambient_wav(self.default_ambient_wav)
        except Exception:
            pass

    @staticmethod
    def _synthesize_chime_wav(filepath: str) -> None:
        sample_rate = 22050
        duration = 0.9
        n_samples = int(sample_rate * duration)
        with wave.open(filepath, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_samples):
                t = i / sample_rate
                env = math.exp(-3.5 * t) * min(1.0, t / 0.02)
                # Pleasant harmonic C5 + E5 + G5 chime
                val = (
                    0.50 * math.sin(2.0 * math.pi * 523.25 * t)
                    + 0.30 * math.sin(2.0 * math.pi * 659.25 * t)
                    + 0.20 * math.sin(2.0 * math.pi * 783.99 * t)
                )
                sample = int(max(-32767, min(32767, val * env * 14000)))
                frames.extend(struct.pack("<h", sample))
            wf.writeframes(bytes(frames))

    @staticmethod
    def _synthesize_ambient_wav(filepath: str) -> None:
        sample_rate = 22050
        duration = 4.0
        n_samples = int(sample_rate * duration)
        with wave.open(filepath, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            frames = bytearray()
            for i in range(n_samples):
                t = i / sample_rate
                # Smooth loopable envelope
                env = 0.75 + 0.25 * math.sin(2.0 * math.pi * (t / duration))
                # Warm 432Hz + 216Hz + 324Hz meditation pad
                val = (
                    0.45 * math.sin(2.0 * math.pi * 216.0 * t)
                    + 0.35 * math.sin(2.0 * math.pi * 324.0 * t)
                    + 0.20 * math.sin(2.0 * math.pi * 432.0 * t)
                )
                sample = int(max(-32767, min(32767, val * env * 8500)))
                frames.extend(struct.pack("<h", sample))
            wf.writeframes(bytes(frames))

    def play_alert(self, custom_path: str = "") -> None:
        path = custom_path.strip() if custom_path else ""
        if not path or not os.path.isfile(path):
            path = self.default_alert_wav
        try:
            import winsound
            if path.lower().endswith(".wav") and os.path.isfile(path):
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            else:
                winmm.mciSendStringW("close vg_alert", None, 0, 0)
                winmm.mciSendStringW(f'open "{path}" type mpegvideo alias vg_alert', None, 0, 0)
                winmm.mciSendStringW("play vg_alert", None, 0, 0)
        except Exception:
            pass

    def start_break_music(self, custom_path: str = "") -> None:
        self.stop_break_music()
        path = custom_path.strip() if custom_path else ""
        if not path or not os.path.isfile(path):
            path = self.default_ambient_wav
        try:
            if path.lower().endswith(".wav") and path == self.default_ambient_wav:
                import winsound
                winsound.PlaySound(
                    path,
                    winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP,
                )
                self._playing_bgm = True
            else:
                winmm.mciSendStringW("close vg_bgm", None, 0, 0)
                winmm.mciSendStringW(f'open "{path}" type mpegvideo alias vg_bgm', None, 0, 0)
                winmm.mciSendStringW("play vg_bgm repeat", None, 0, 0)
                self._playing_bgm = True
        except Exception:
            pass

    def stop_break_music(self) -> None:
        if not self._playing_bgm:
            return
        self._playing_bgm = False
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        try:
            winmm.mciSendStringW("stop vg_bgm", None, 0, 0)
            winmm.mciSendStringW("close vg_bgm", None, 0, 0)
        except Exception:
            pass


# Global reference for WndProc callbacks inside DesktopOverlayManager
_OVERLAY_MGR_REF: Optional["DesktopOverlayManager"] = None


def _desktop_overlay_wnd_proc(
    hwnd: int, msg: int, wparam: int, lparam: int
) -> int:
    mgr = _OVERLAY_MGR_REF
    if mgr is not None:
        try:
            res = mgr.handle_wnd_proc(hwnd, msg, wparam, lparam)
            if res is not None:
                return res
        except Exception:
            pass
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_OVERLAY_WND_PROC_CB = WNDPROC(_desktop_overlay_wnd_proc)


class DesktopOverlayManager:
    """
    Dedicated Win32 GUI thread managing:
      1. Native Desktop Mini Floating Window (hwnd_mini)
      2. Native Blink / Pre-Break Toast Window (hwnd_toast)
      3. Native Floating Magic Toolbar Window (hwnd_toolbar)
    """

    CLASS_NAME = "EyeGuardDesktopOverlayClass"

    def __init__(
        self,
        on_toolbar_action: Optional[Callable[[str, int], None]] = None,
        on_open_main_window: Optional[Callable[[], None]] = None,
        on_toast_postpone: Optional[Callable[[int], None]] = None,
        on_toast_skip: Optional[Callable[[], None]] = None,
    ) -> None:
        global _OVERLAY_MGR_REF
        _OVERLAY_MGR_REF = self

        self.on_toolbar_action = on_toolbar_action
        self.on_open_main_window = on_open_main_window
        self.on_toast_postpone = on_toast_postpone
        self.on_toast_skip = on_toast_skip

        # Mini window state
        self.mini_enabled = False
        self.mini_style = "bar"  # 'bar' | 'window'
        self.mini_auto_translucent = True
        self.mini_opacity_pct = 85
        self.mini_time_text = "20:00"
        self.mini_phase_text = "WORK"
        self.mini_progress = 1.0  # 0.0 -> 1.0
        self.mini_x = -1
        self.mini_y = -1

        # Toast (Blink / Pre-break prompt) state
        self.toast_visible = False
        self.toast_expire_ts = 0.0
        self.toast_position = "bottom_right"  # 'top_center' | 'bottom_right'
        self.toast_title = "TEAR FILM HYDRATION"
        self.toast_subtitle = "Blink gently twice"
        self.toast_start_ts = 0.0
        self.toast_duration = 4.0
        self.toast_is_prompt = False
        self.toast_postpone_mins = 5
        self.toast_can_postpone = True
        self.toast_hover_btn: Optional[str] = None
        self.toast_x = 0
        self.toast_y = 0
        self.toast_w = 430
        self.toast_h = 60
        self._toast_hdc_mem = None
        self._toast_hbm = None
        self._toast_old_hbm = None
        self._toast_p_bits = None
        self._toast_last_w = 0
        self._toast_last_h = 0
        self._gdiplus_token = ctypes.c_ulong()
        self._private_font_collection = ctypes.c_void_p()
        startup_in = GdiplusStartupInput(1, None, False, False)
        try:
            gdiplus.GdiplusStartup(ctypes.byref(self._gdiplus_token), ctypes.byref(startup_in), None)
            self._init_private_fonts()
        except Exception:
            pass

        # Magic Toolbar state
        self.toolbar_enabled = False
        self.toolbar_color = "#7c3aed"
        self.toolbar_placement = "center"  # 'center' | 'left' | 'right'
        self.toolbar_offset_dp = 0
        self.toolbar_delay_sec = 0.30
        self.toolbar_target_hwnd = 0
        self.toolbar_hover_start_ts = 0.0
        self.toolbar_visible = False

        # HWNDs
        self.hwnd_mini: Optional[int] = None
        self.hwnd_toast: Optional[int] = None
        self.hwnd_toolbar: Optional[int] = None

        self._cmd_queue: "queue.Queue[tuple]" = queue.Queue()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self._thread_loop,
            name="VG-DesktopOverlays-Thread",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        if hasattr(self, "_private_font_collection") and self._private_font_collection.value:
            try:
                gdiplus.GdipDeletePrivateFontCollection(ctypes.byref(self._private_font_collection))
                self._private_font_collection = ctypes.c_void_p(0)
            except Exception:
                pass
        if hasattr(self, "_gdiplus_token") and self._gdiplus_token.value:
            try:
                gdiplus.GdiplusShutdown(self._gdiplus_token)
                self._gdiplus_token = ctypes.c_ulong(0)
            except Exception:
                pass

    def configure_mini_window(
        self,
        enabled: bool,
        style: str = "bar",
        auto_translucent: bool = True,
        opacity_pct: int = 85,
    ) -> None:
        self.mini_enabled = bool(enabled)
        self.mini_style = "window" if style == "window" else "bar"
        self.mini_auto_translucent = bool(auto_translucent)
        self.mini_opacity_pct = max(30, min(100, int(opacity_pct)))
        self._cmd_queue.put(("sync_mini",))
        self._wake_event.set()

    def update_mini_timer(
        self, remaining_sec: int, total_sec: int, phase_label: str = "WORK"
    ) -> None:
        rem = max(0, int(remaining_sec))
        tot = max(1, int(total_sec))
        mins = rem // 60
        secs = rem % 60
        self.mini_time_text = f"{mins:02d}:{secs:02d}"
        self.mini_phase_text = phase_label
        self.mini_progress = max(0.0, min(1.0, float(rem) / float(tot)))
        if self.mini_enabled:
            self._cmd_queue.put(("repaint_mini",))
            self._wake_event.set()

    def show_toast(
        self,
        title: str,
        subtitle: str,
        position: str = "bottom_right",
        duration_sec: float = 4.0,
        is_prompt: bool = False,
        postpone_mins: int = 5,
        can_postpone: bool = True,
    ) -> None:
        self.toast_title = title
        self.toast_subtitle = subtitle
        self.toast_position = "top_center" if position == "top_center" else "bottom_right"
        self.toast_start_ts = time.monotonic()
        self.toast_duration = max(1.0, float(duration_sec))
        self.toast_expire_ts = self.toast_start_ts + self.toast_duration
        self.toast_is_prompt = bool(is_prompt)
        self.toast_postpone_mins = max(1, int(postpone_mins))
        self.toast_can_postpone = bool(can_postpone)
        self.toast_hover_btn = None
        self.toast_visible = True
        self._cmd_queue.put(("show_toast",))
        self._wake_event.set()

    def hide_toast(self) -> None:
        self.toast_visible = False
        self.toast_hover_btn = None
        self._cmd_queue.put(("hide_toast",))
        self._wake_event.set()

    def configure_magic_toolbar(
        self,
        enabled: bool,
        color: str = "#7c3aed",
        placement: str = "center",
        offset_dp: int = 0,
        delay_sec: float = 0.30,
    ) -> None:
        self.toolbar_enabled = bool(enabled)
        self.toolbar_color = str(color or "#7c3aed")
        self.toolbar_placement = placement if placement in ("center", "left", "right") else "center"
        self.toolbar_offset_dp = int(offset_dp)
        self.toolbar_delay_sec = max(0.05, min(2.0, float(delay_sec)))
        if not self.toolbar_enabled:
            self._cmd_queue.put(("hide_toolbar",))
        self._wake_event.set()

    # ------------------------------------------------------------------
    # Win32 Window Procedure
    # ------------------------------------------------------------------
    def handle_wnd_proc(
        self, hwnd: int, msg: int, wparam: int, lparam: int
    ) -> Optional[int]:
        if msg == WM_ERASEBKGND:
            return 1

        if hwnd == self.hwnd_mini:
            if msg == WM_LBUTTONDOWN:
                user32.ReleaseCapture()
                user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
                # Save new position after drag completes
                rc = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rc)):
                    self.mini_x = rc.left
                    self.mini_y = rc.top
                return 0
            if msg == WM_PAINT:
                self._paint_mini_window(hwnd)
                return 0

        elif hwnd == self.hwnd_toast:
            if msg == WM_LBUTTONDOWN and self.toast_is_prompt:
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                self._on_toast_click(x, y)
                return 0
            elif msg == WM_MOUSEMOVE and self.toast_is_prompt:
                x = ctypes.c_short(lparam & 0xFFFF).value
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value
                self._on_toast_mousemove(x, y)
                return 0
            elif msg == WM_PAINT:
                ps = PAINTSTRUCT()
                user32.BeginPaint(hwnd, ctypes.byref(ps))
                user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0

        elif hwnd == self.hwnd_toolbar:
            if msg == WM_LBUTTONDOWN:
                x = ctypes.c_short(lparam & 0xFFFF).value
                self._on_toolbar_click(x)
                return 0
            if msg == WM_PAINT:
                self._paint_toolbar_window(hwnd)
                return 0

        return None

    def _on_toolbar_click(self, client_x: int) -> None:
        # 4 equal buttons across 240px width (60px each):
        # [0..59]: dark | [60..119]: gray | [120..179]: dim | [180..240]: pin
        target = self.toolbar_target_hwnd
        if not target or not self.on_toolbar_action:
            return
        if client_x < 60:
            action = "dark"
        elif client_x < 120:
            action = "grayscale"
        elif client_x < 180:
            action = "dim"
        else:
            action = "pin"
        threading.Thread(
            target=self.on_toolbar_action,
            args=(action, target),
            daemon=True,
        ).start()

    @staticmethod
    def _create_pill_path(x: float, y: float, w: float, h: float, r: float):
        path = ctypes.c_void_p()
        gdiplus.GdipCreatePath(0, ctypes.byref(path))
        d = r * 2.0
        gdiplus.GdipAddPathArc(path, ctypes.c_float(x), ctypes.c_float(y), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(180), ctypes.c_float(90))
        gdiplus.GdipAddPathLine(path, ctypes.c_float(x + r), ctypes.c_float(y), ctypes.c_float(x + w - r), ctypes.c_float(y))
        gdiplus.GdipAddPathArc(path, ctypes.c_float(x + w - d), ctypes.c_float(y), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(270), ctypes.c_float(90))
        gdiplus.GdipAddPathLine(path, ctypes.c_float(x + w), ctypes.c_float(y + r), ctypes.c_float(x + w), ctypes.c_float(y + h - r))
        gdiplus.GdipAddPathArc(path, ctypes.c_float(x + w - d), ctypes.c_float(y + h - d), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(0), ctypes.c_float(90))
        gdiplus.GdipAddPathLine(path, ctypes.c_float(x + w - r), ctypes.c_float(y + h), ctypes.c_float(x + r), ctypes.c_float(y + h))
        gdiplus.GdipAddPathArc(path, ctypes.c_float(x), ctypes.c_float(y + h - d), ctypes.c_float(d), ctypes.c_float(d), ctypes.c_float(90), ctypes.c_float(90))
        gdiplus.GdipAddPathLine(path, ctypes.c_float(x), ctypes.c_float(y + h - r), ctypes.c_float(x), ctypes.c_float(y + r))
        gdiplus.GdipClosePathFigure(path)
        return path

    @staticmethod
    def _resolve_bundled_font_paths():
        """Locate bundled custom .ttf font files across frozen builds and source checkouts."""
        font_names = ("LinBiolinum_RB_G.ttf", "thmanyahseriftext-Black.otf")
        candidate_dirs = []
        if hasattr(sys, "_MEIPASS"):
            candidate_dirs.append(os.path.join(sys._MEIPASS, "assets", "fonts"))
        exe_dir = os.path.dirname(sys.executable)
        candidate_dirs.append(os.path.join(exe_dir, "assets", "fonts"))
        candidate_dirs.append(os.path.join(exe_dir, "_internal", "assets", "fonts"))
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidate_dirs.append(os.path.join(repo_root, "assets", "fonts"))

        resolved = []
        seen = set()
        for font_name in font_names:
            for directory in candidate_dirs:
                path = os.path.join(directory, font_name)
                if path not in seen and os.path.isfile(path):
                    seen.add(path)
                    resolved.append(path)
                    break
        return resolved

    def _init_private_fonts(self) -> None:
        """Load bundled toast typography into an isolated GDI+ PrivateFontCollection."""
        paths = self._resolve_bundled_font_paths()
        if not paths:
            return
        pfc = ctypes.c_void_p()
        if gdiplus.GdipNewPrivateFontCollection(ctypes.byref(pfc)) != 0 or not pfc.value:
            return
        loaded = 0
        for path in paths:
            if gdiplus.GdipPrivateAddFontFile(pfc, path) == 0:
                loaded += 1
        if loaded > 0:
            self._private_font_collection = pfc
        else:
            gdiplus.GdipDeletePrivateFontCollection(ctypes.byref(pfc))

    @staticmethod
    def _toast_font_candidates(role: str = "sub"):
        """Return ordered font-family candidates tuned by optical role."""
        if role == "tag":
            return ("Linux Biolinum G", "Segoe UI Variable Text Bold", "Segoe UI Bold", "Segoe UI")
        if role == "ui":
            return ("Linux Biolinum G", "Segoe UI Variable Text Bold", "Segoe UI Bold", "Segoe UI")
        return ("thmanyah serif text Black", "Elephant", "Segoe UI Variable Display Semibold", "Segoe UI Semibold", "Segoe UI")

    def _create_font_family(self, candidates) -> ctypes.c_void_p:
        """Instantiate the first available GDI+ font family from private or system collections."""
        pfc = getattr(self, "_private_font_collection", None)
        collections = (pfc, None) if (pfc and pfc.value) else (None,)
        for family_name in candidates:
            for collection in collections:
                ff = ctypes.c_void_p()
                status = gdiplus.GdipCreateFontFamilyFromName(
                    family_name, collection, ctypes.byref(ff)
                )
                if status == 0 and ff.value:
                    return ff
        ff = ctypes.c_void_p()
        gdiplus.GdipGetGenericFontFamilySansSerif(ctypes.byref(ff))
        return ff

    @staticmethod
    def _create_typographic_format(align: int = 0, line_align: int = 0) -> ctypes.c_void_p:
        """Clone GenericTypographic StringFormat to strip GDI+ fake leading and sidebearings."""
        generic_sf = ctypes.c_void_p()
        gdiplus.GdipStringFormatGetGenericTypographic(ctypes.byref(generic_sf))
        sf = ctypes.c_void_p()
        if generic_sf.value:
            gdiplus.GdipCloneStringFormat(generic_sf, ctypes.byref(sf))
        else:
            gdiplus.GdipCreateStringFormat(0, 0, ctypes.byref(sf))
        flags = 0x00001000 | 0x00004000  # NoWrap | MeasureTrailingSpaces
        gdiplus.GdipSetStringFormatFlags(sf, flags)
        gdiplus.GdipSetStringFormatTrimming(sf, 3)  # EllipsisCharacter
        gdiplus.GdipSetStringFormatAlign(sf, align)
        gdiplus.GdipSetStringFormatLineAlign(sf, line_align)
        return sf

    def _measure_text_ink_bounds(
        self, candidates, px_size: float, font_style: int, text: str
    ):
        """Measure pixel-exact ink offsets (left_pad, top_pad, right_pad, ink_w, ink_h), cached."""
        clean = (text or "").strip()
        if not clean:
            return (0.0, 0.0, 0.0, 0.0, float(px_size) * 0.72)

        cache = getattr(self, "_ink_metrics_cache", None)
        if cache is None:
            cache = {}
            self._ink_metrics_cache = cache

        key = (tuple(candidates), round(float(px_size), 2), int(font_style), clean)
        cached = cache.get(key)
        if cached is not None:
            return cached

        mw, mh = 480, 64
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = mw
        bmi.bmiHeader.biHeight = -mh
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        p_bits = ctypes.c_void_p()
        hdc_screen = user32.GetDC(None)
        hdc_tmp = gdi32.CreateCompatibleDC(hdc_screen)
        hbm_tmp = gdi32.CreateDIBSection(hdc_tmp, ctypes.byref(bmi), 0, ctypes.byref(p_bits), None, 0)
        old_tmp = gdi32.SelectObject(hdc_tmp, hbm_tmp)
        user32.ReleaseDC(None, hdc_screen)

        if not p_bits.value:
            gdi32.SelectObject(hdc_tmp, old_tmp)
            gdi32.DeleteObject(hbm_tmp)
            gdi32.DeleteDC(hdc_tmp)
            return (0.0, 0.0, 0.0, float(len(clean)) * px_size * 0.5, float(px_size) * 0.72)

        ctypes.memset(p_bits, 0, mw * mh * 4)
        gfx = ctypes.c_void_p()
        gdiplus.GdipCreateFromHDC(hdc_tmp, ctypes.byref(gfx))
        gdiplus.GdipSetTextRenderingHint(gfx, 4)  # AntiAlias

        ff = self._create_font_family(candidates)
        font = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff, ctypes.c_float(px_size), font_style, 2, ctypes.byref(font))  # UnitPixel
        sf = self._create_typographic_format()
        brush = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xFFFFFFFF), ctypes.byref(brush))

        box_x, box_y, box_w, box_h = 24.0, 12.0, float(mw - 48), float(mh - 20)
        rect = RectF(box_x, box_y, box_w, box_h)
        gdiplus.GdipDrawString(gfx, clean, -1, font, ctypes.byref(rect), sf, brush)

        alpha_plane = bytes((ctypes.c_ubyte * (mw * mh * 4)).from_address(p_bits.value))[3::4]
        min_x, min_y, max_x, max_y = mw, mh, -1, -1
        for y in range(mh):
            row_start = y * mw
            row_alpha = alpha_plane[row_start : row_start + mw]
            if max(row_alpha) > 16:
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
                for x, val in enumerate(row_alpha):
                    if val > 16:
                        if x < min_x:
                            min_x = x
                        if x > max_x:
                            max_x = x

        gdiplus.GdipDeleteBrush(brush)
        gdiplus.GdipDeleteStringFormat(sf)
        gdiplus.GdipDeleteFont(font)
        gdiplus.GdipDeleteFontFamily(ff)
        gdiplus.GdipDeleteGraphics(gfx)
        gdi32.SelectObject(hdc_tmp, old_tmp)
        gdi32.DeleteObject(hbm_tmp)
        gdi32.DeleteDC(hdc_tmp)

        if max_x < min_x or max_y < min_y:
            result = (0.0, 0.0, 0.0, float(len(clean)) * px_size * 0.5, float(px_size) * 0.72)
        else:
            left_pad = float(min_x) - box_x
            top_pad = float(min_y) - box_y
            right_pad = (box_x + box_w) - float(max_x + 1)
            ink_w = float(max_x - min_x + 1)
            ink_h = float(max_y - min_y + 1)
            result = (left_pad, top_pad, right_pad, ink_w, ink_h)

        if len(cache) > 64:
            cache.clear()
        cache[key] = result
        return result

    def _hit_test_toast(self, x: int, y: int) -> Optional[str]:
        if not self.toast_is_prompt:
            return None
        w, h = self.toast_w, self.toast_h
        btn_postpone = (296.0, 18.0, 114.0, 30.0)
        btn_skip = (418.0, 18.0, 64.0, 30.0)
        close_cx, close_cy = float(w - 22.0), float(h / 2.0)

        # Check close (circle r=14)
        if (x - close_cx) ** 2 + (y - close_cy) ** 2 <= 196:
            return "close"

        # Check postpone (if allowed)
        px, py, pw, ph = btn_postpone
        if self.toast_can_postpone and (px <= x <= px + pw and py <= y <= py + ph):
            return "postpone"

        # Check skip
        sx, sy, sw, sh = btn_skip
        if sx <= x <= sx + sw and sy <= y <= sy + sh:
            return "skip"

        return None

    def _on_toast_mousemove(self, x: int, y: int) -> None:
        hit = self._hit_test_toast(x, y)
        if hit != self.toast_hover_btn:
            self.toast_hover_btn = hit
            now = time.monotonic()
            elapsed = now - self.toast_start_ts
            progress = max(0.0, min(1.0, 1.0 - (elapsed / self.toast_duration)))
            self._render_and_update_toast(progress=progress)

        if hit:
            hcursor = user32.LoadCursorW(None, 32649)  # IDC_HAND
            user32.SetCursor(hcursor)
        else:
            hcursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
            user32.SetCursor(hcursor)

    def _on_toast_click(self, x: int, y: int) -> None:
        hit = self._hit_test_toast(x, y)
        if not hit:
            return
        self.hide_toast()
        if hit == "postpone":
            if self.on_toast_postpone:
                threading.Thread(
                    target=self.on_toast_postpone,
                    args=(self.toast_postpone_mins,),
                    daemon=True,
                ).start()
        elif hit == "skip":
            if self.on_toast_skip:
                threading.Thread(
                    target=self.on_toast_skip,
                    daemon=True,
                ).start()

    # ------------------------------------------------------------------
    # GDI Painting Routines
    # ------------------------------------------------------------------
    def _paint_mini_window(self, hwnd: int) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            w = max(1, rc.right - rc.left)
            h = max(1, rc.bottom - rc.top)

            bg_brush = gdi32.CreateSolidBrush(0x001E1915)  # Dark slate BGR
            user32.FillRect(hdc, ctypes.byref(rc), bg_brush)
            gdi32.DeleteObject(bg_brush)

            # Border
            border_brush = gdi32.CreateSolidBrush(0x0058524A)
            user32.FrameRect(hdc, ctypes.byref(rc), border_brush)
            gdi32.DeleteObject(border_brush)

            # Progress bar at bottom
            bar_h = 5 if self.mini_style == "bar" else 7
            prog_w = int(w * self.mini_progress)
            if prog_w > 0:
                prog_rc = wintypes.RECT(1, h - bar_h - 1, max(2, prog_w - 1), h - 1)
                teal_brush = gdi32.CreateSolidBrush(0x0088940D)  # #0d9488 in BGR
                user32.FillRect(hdc, ctypes.byref(prog_rc), teal_brush)
                gdi32.DeleteObject(teal_brush)

            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, 0x00F8FAFC)
            font_h = 16 if self.mini_style == "bar" else 19
            hfont = gdi32.CreateFontW(
                -font_h, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Linux Biolinum G"
            )
            old_font = gdi32.SelectObject(hdc, hfont)

            text_rc = wintypes.RECT(4, 2, w - 4, h - bar_h - 2)
            label = (
                f"👁 {self.mini_time_text}"
                if self.mini_style == "bar"
                else f"👁 {self.mini_phase_text}  •  {self.mini_time_text}"
            )
            user32.DrawTextW(
                hdc,
                label,
                -1,
                ctypes.byref(text_rc),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            gdi32.SelectObject(hdc, old_font)
            gdi32.DeleteObject(hfont)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _render_and_update_toast(self, progress: float = 1.0) -> None:
        if not self.hwnd_toast:
            return
        w = self.toast_w
        h = self.toast_h

        # Lazily allocate 32-bit DIB and memory DC (reallocate if dimension changes)
        if (
            not self._toast_hdc_mem
            or not self._toast_hbm
            or not self._toast_p_bits
            or getattr(self, "_toast_last_w", 0) != w
            or getattr(self, "_toast_last_h", 0) != h
        ):
            if self._toast_hbm:
                if self._toast_hdc_mem and getattr(self, "_toast_old_hbm", None):
                    gdi32.SelectObject(self._toast_hdc_mem, self._toast_old_hbm)
                    self._toast_old_hbm = None
                gdi32.DeleteObject(self._toast_hbm)
                self._toast_hbm = None
            if self._toast_hdc_mem:
                gdi32.DeleteDC(self._toast_hdc_mem)
                self._toast_hdc_mem = None
            self._toast_p_bits = None
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h  # top-down DIB
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            self._toast_p_bits = ctypes.c_void_p()
            hdc_screen = user32.GetDC(None)
            self._toast_hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            self._toast_hbm = gdi32.CreateDIBSection(
                self._toast_hdc_mem,
                ctypes.byref(bmi),
                0,
                ctypes.byref(self._toast_p_bits),
                None,
                0,
            )
            self._toast_old_hbm = gdi32.SelectObject(self._toast_hdc_mem, self._toast_hbm)
            user32.ReleaseDC(None, hdc_screen)
            self._toast_last_w = w
            self._toast_last_h = h

        ctypes.memset(self._toast_p_bits, 0, w * h * 4)

        graphics = ctypes.c_void_p()
        gdiplus.GdipCreateFromHDC(self._toast_hdc_mem, ctypes.byref(graphics))
        gdiplus.GdipSetSmoothingMode(graphics, 2)  # AntiAlias
        gdiplus.GdipSetTextRenderingHint(graphics, 4)  # AntiAlias

        try:
            if self.toast_is_prompt:
                self._render_interactive_toast(graphics, w, h, progress)
            else:
                self._render_blink_toast(graphics, w, h, progress)
        finally:
            gdiplus.GdipDeleteGraphics(graphics)

        master_alpha = 255
        if not self.toast_is_prompt and self.toast_visible:
            now_mono = time.monotonic()
            elapsed = max(0.0, now_mono - self.toast_start_ts)
            remaining = max(0.0, self.toast_expire_ts - now_mono)
            fade_in = min(1.0, elapsed / 0.35)
            fade_out = min(1.0, remaining / 0.45)
            t_ease = max(0.0, min(fade_in, fade_out))
            t_ease = t_ease * t_ease * (3.0 - 2.0 * t_ease)
            master_alpha = max(0, min(255, int(246 * t_ease)))

        blend = BLENDFUNCTION(0, 0, master_alpha, 1)
        pt_src = wintypes.POINT(0, 0)
        pt_dest = wintypes.POINT(self.toast_x, self.toast_y)
        size_dest = wintypes.SIZE(w, h)
        hdc_screen = user32.GetDC(None)

        user32.UpdateLayeredWindow(
            self.hwnd_toast,
            hdc_screen,
            ctypes.byref(pt_dest),
            ctypes.byref(size_dest),
            self._toast_hdc_mem,
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            2,  # ULW_ALPHA
        )
        user32.ReleaseDC(None, hdc_screen)
        user32.ShowWindow(self.hwnd_toast, 4)  # SW_SHOWNOACTIVATE

    @staticmethod
    def _blink_openness(elapsed_sec: float) -> float:
        """Returns eyelid openness in [0.0, 1.0] with two soft natural blinks."""
        for start in (0.75, 1.85):
            end = start + 0.48
            if start <= elapsed_sec <= end:
                phase = (elapsed_sec - start) / 0.48
                return max(0.0, 0.5 * (1.0 + math.cos(2.0 * math.pi * phase)))
        return 1.0

    def _render_blink_toast(
        self, graphics: ctypes.c_void_p, w: int, h: int, progress: float
    ) -> None:
        elapsed = max(0.0, time.monotonic() - self.toast_start_ts)
        openness = self._blink_openness(elapsed)

        # Gentle rhythmic breathing pulse + expanding halo ripple across the entire pill
        pulse = 0.5 - 0.5 * math.cos(2.0 * math.pi * (elapsed / 1.40))
        ripple = (elapsed * 0.78) % 1.0
        pad = 5.0
        pill_w = float(w) - pad * 2.0
        pill_h = float(h) - pad * 2.0
        pill_r = pill_h / 2.0

        # 0. Expanding Attention Ripple Halo around the entire capsule
        ripple_expand = 4.4 * ripple
        ripple_alpha = int(170 * ((1.0 - ripple) ** 1.45))
        if ripple_alpha > 4:
            r_path = self._create_pill_path(
                pad - ripple_expand,
                pad - ripple_expand,
                pill_w + ripple_expand * 2.0,
                pill_h + ripple_expand * 2.0,
                pill_r + ripple_expand,
            )
            r_pen = ctypes.c_void_p()
            gdiplus.GdipCreatePen1(
                ctypes.c_uint32((ripple_alpha << 24) | 0x00FF4557),
                ctypes.c_float(2.0),
                2,
                ctypes.byref(r_pen),
            )
            gdiplus.GdipDrawPath(graphics, r_pen, r_path)
            gdiplus.GdipDeletePen(r_pen)
            gdiplus.GdipDeletePath(r_path)

        # 1. Spacious Capsule Pill with Soft Breathing Red Frame & Sweeping Satin Sheen
        path = self._create_pill_path(pad, pad, pill_w, pill_h, pill_r)

        # Deep obsidian background (#0E1520 @ 95%)
        bg_brush = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xF20E1520), ctypes.byref(bg_brush))
        gdiplus.GdipFillPath(graphics, bg_brush, path)
        gdiplus.GdipDeleteBrush(bg_brush)

        # Clipped whole-pill interior effects: breathing crimson wash + velvety smooth sheen sweep
        gdiplus.GdipSetClipPath(graphics, path, 0)

        wash_alpha = int(12 + 22 * pulse)
        wash_brush = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(
            ctypes.c_uint32((wash_alpha << 24) | 0x00FF3B4E), ctypes.byref(wash_brush)
        )
        gdiplus.GdipFillPath(graphics, wash_brush, path)
        gdiplus.GdipDeleteBrush(wash_brush)

        sweep_cycle = (elapsed % 1.45) / 1.45
        sx = -75.0 + sweep_cycle * (float(w) + 150.0)
        for step in range(-12, 13):
            offset = step * 3.0
            norm = step / 6.0
            gauss = math.exp(-(norm * norm))
            s_alpha = int(18.0 * gauss)
            if s_alpha < 1:
                continue
            sheen_pen = ctypes.c_void_p()
            gdiplus.GdipCreatePen1(
                ctypes.c_uint32((s_alpha << 24) | 0x00FFE4E6),
                ctypes.c_float(4.5),
                2,
                ctypes.byref(sheen_pen),
            )
            gdiplus.GdipDrawLine(
                graphics,
                sheen_pen,
                ctypes.c_float(sx + offset - 18.0),
                ctypes.c_float(float(h) + 8.0),
                ctypes.c_float(sx + offset + 18.0),
                ctypes.c_float(-8.0),
            )
            gdiplus.GdipDeletePen(sheen_pen)

        gdiplus.GdipResetClip(graphics)

        # Breathing feathered red halo + bright luminous crimson frame
        bloom_alpha = int(42 + 46 * pulse)
        bloom_pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(
            ctypes.c_uint32((bloom_alpha << 24) | 0x00FF3B4E),
            ctypes.c_float(4.4),
            2,
            ctypes.byref(bloom_pen),
        )
        gdiplus.GdipDrawPath(graphics, bloom_pen, path)
        gdiplus.GdipDeletePen(bloom_pen)

        rim_alpha = int(218 + 37 * pulse)
        rim_pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(
            ctypes.c_uint32((rim_alpha << 24) | 0x00FF4557),
            ctypes.c_float(1.6),
            2,
            ctypes.byref(rim_pen),
        )
        gdiplus.GdipDrawPath(graphics, rim_pen, path)
        gdiplus.GdipDeletePen(rim_pen)
        gdiplus.GdipDeletePath(path)

        # 2. Calm Orb with Gentle Blinking Eye + Integrated Progress Ring
        cx = 35.0
        cy = float(h) / 2.0
        orb_r = 16.5

        halo = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0x1CF5C26B), ctypes.byref(halo))
        gdiplus.GdipFillEllipse(graphics, halo, ctypes.c_float(cx - orb_r - 2.5), ctypes.c_float(cy - orb_r - 2.5), ctypes.c_float((orb_r + 2.5) * 2), ctypes.c_float((orb_r + 2.5) * 2))
        gdiplus.GdipDeleteBrush(halo)

        orb_body = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0x28F5C26B), ctypes.byref(orb_body))
        gdiplus.GdipFillEllipse(graphics, orb_body, ctypes.c_float(cx - orb_r), ctypes.c_float(cy - orb_r), ctypes.c_float(orb_r * 2), ctypes.c_float(orb_r * 2))
        gdiplus.GdipDeleteBrush(orb_body)

        orb_track = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(ctypes.c_uint32(0x30FAF8F5), ctypes.c_float(1.3), 2, ctypes.byref(orb_track))
        gdiplus.GdipDrawEllipse(graphics, orb_track, ctypes.c_float(cx - orb_r), ctypes.c_float(cy - orb_r), ctypes.c_float(orb_r * 2), ctypes.c_float(orb_r * 2))
        gdiplus.GdipDeletePen(orb_track)

        if progress > 0.01:
            orb_prog = ctypes.c_void_p()
            gdiplus.GdipCreatePen1(ctypes.c_uint32(0xD8F5C26B), ctypes.c_float(1.5), 2, ctypes.byref(orb_prog))
            sweep = ctypes.c_float(360.0 * progress)
            gdiplus.GdipDrawArc(graphics, orb_prog, ctypes.c_float(cx - orb_r), ctypes.c_float(cy - orb_r), ctypes.c_float(orb_r * 2), ctypes.c_float(orb_r * 2), ctypes.c_float(-90.0), sweep)
            gdiplus.GdipDeletePen(orb_prog)

        # Animated Eyelid Vector (closes gently down to a peaceful crescent)
        closed_dip = 1.8 * (1.0 - openness)
        top_ctrl_y = cy - 6.4 * openness + closed_dip
        bot_ctrl_y = cy + 6.0 * openness + closed_dip
        corner_y = cy + 0.8 * (1.0 - openness)

        eye_path = ctypes.c_void_p()
        gdiplus.GdipCreatePath(0, ctypes.byref(eye_path))
        gdiplus.GdipAddPathBezier(
            eye_path,
            ctypes.c_float(cx - 8.5), ctypes.c_float(corner_y),
            ctypes.c_float(cx - 4.0), ctypes.c_float(top_ctrl_y),
            ctypes.c_float(cx + 4.0), ctypes.c_float(top_ctrl_y),
            ctypes.c_float(cx + 8.5), ctypes.c_float(corner_y),
        )
        if openness > 0.14:
            gdiplus.GdipAddPathBezier(
                eye_path,
                ctypes.c_float(cx + 8.5), ctypes.c_float(corner_y),
                ctypes.c_float(cx + 4.0), ctypes.c_float(bot_ctrl_y),
                ctypes.c_float(cx - 4.0), ctypes.c_float(bot_ctrl_y),
                ctypes.c_float(cx - 8.5), ctypes.c_float(corner_y),
            )
            gdiplus.GdipClosePathFigure(eye_path)

        eye_pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(ctypes.c_uint32(0xFFFAF8F5), ctypes.c_float(1.6), 2, ctypes.byref(eye_pen))
        gdiplus.GdipDrawPath(graphics, eye_pen, eye_path)
        gdiplus.GdipDeletePen(eye_pen)
        gdiplus.GdipDeletePath(eye_path)

        if openness > 0.22:
            pupil_alpha = int(255 * min(1.0, (openness - 0.22) / 0.45))
            pupil_color = (pupil_alpha << 24) | 0x00F5C26B
            pupil_r = 2.6 * openness
            pupil = ctypes.c_void_p()
            gdiplus.GdipCreateSolidFill(ctypes.c_uint32(pupil_color), ctypes.byref(pupil))
            gdiplus.GdipFillEllipse(graphics, pupil, ctypes.c_float(cx - pupil_r), ctypes.c_float(cy - pupil_r), ctypes.c_float(pupil_r * 2), ctypes.c_float(pupil_r * 2))
            gdiplus.GdipDeleteBrush(pupil)

            if openness > 0.55:
                glint_alpha = int(235 * min(1.0, (openness - 0.55) / 0.35))
                glint = ctypes.c_void_p()
                gdiplus.GdipCreateSolidFill(ctypes.c_uint32((glint_alpha << 24) | 0x00FFFFFF), ctypes.byref(glint))
                gdiplus.GdipFillEllipse(graphics, glint, ctypes.c_float(cx + 0.4), ctypes.c_float(cy - 1.8 * openness), ctypes.c_float(1.4), ctypes.c_float(1.4))
                gdiplus.GdipDeleteBrush(glint)

        # 3. Optically Centered Studio Typography (UnitPixel + GenericTypographic Ink Alignment)
        tag_candidates = self._toast_font_candidates("tag")
        sub_candidates = self._toast_font_candidates("sub")
        tag_px = 12.0
        sub_px = 18.5
        tag_style = 1  # Native Bold face in Linux Biolinum G Bold
        sub_style = 0  # Native face in Elephant (no synthetic GDI+ bold smearing)

        clean_title = self.toast_title.lstrip("●•-— ").strip() or "EYE HYDRATION"
        clean_sub = self.toast_subtitle.strip()

        tl, _tt, _tr, _tw, th = self._measure_text_ink_bounds(tag_candidates, tag_px, tag_style, clean_title)
        sl, _st, _sr, _sw, sh = self._measure_text_ink_bounds(sub_candidates, sub_px, sub_style, clean_sub)
        tt = _tt
        st = _st

        line_gap = 3.5
        total_ink_h = th + line_gap + sh
        top_ink_y = cy - (total_ink_h / 2.0)
        sub_ink_y = top_ink_y + th + line_gap

        ff_tag = self._create_font_family(tag_candidates)
        ff_sub = self._create_font_family(sub_candidates)
        f_tag = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_tag, ctypes.c_float(tag_px), tag_style, 2, ctypes.byref(f_tag))  # UnitPixel
        f_sub = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_sub, ctypes.c_float(sub_px), sub_style, 2, ctypes.byref(f_sub))  # UnitPixel

        tag_b = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xE8F5C26B), ctypes.byref(tag_b))
        sub_b = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xFFFAF8F5), ctypes.byref(sub_b))

        str_format = self._create_typographic_format()
        anchor_left = cx + orb_r + 14.0
        tag_x = anchor_left - tl
        sub_x = anchor_left - sl
        tag_rect = RectF(tag_x, top_ink_y - tt, max(40.0, float(w) - 18.0 - tag_x), 32.0)
        sub_rect = RectF(sub_x, sub_ink_y - st, max(40.0, float(w) - 18.0 - sub_x), 38.0)

        gdiplus.GdipDrawString(graphics, clean_title, -1, f_tag, ctypes.byref(tag_rect), str_format, tag_b)
        gdiplus.GdipDrawString(graphics, clean_sub, -1, f_sub, ctypes.byref(sub_rect), str_format, sub_b)

        gdiplus.GdipDeleteStringFormat(str_format)
        gdiplus.GdipDeleteBrush(tag_b)
        gdiplus.GdipDeleteBrush(sub_b)
        gdiplus.GdipDeleteFont(f_tag)
        gdiplus.GdipDeleteFont(f_sub)
        gdiplus.GdipDeleteFontFamily(ff_tag)
        gdiplus.GdipDeleteFontFamily(ff_sub)

    def _render_interactive_toast(
        self, graphics: ctypes.c_void_p, w: int, h: int, progress: float
    ) -> None:
        now = time.monotonic()
        rem_sec = max(0, math.ceil(self.toast_expire_ts - now))

        # 1. Main Card Pill Shape
        card_path = self._create_pill_path(0.5, 0.5, float(w - 1), float(h - 1), float(h - 1) / 2.0)
        bg_brush = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xF40A101D), ctypes.byref(bg_brush))
        gdiplus.GdipFillPath(graphics, bg_brush, card_path)
        gdiplus.GdipDeleteBrush(bg_brush)

        rim_pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(ctypes.c_uint32(0x702DD4BF), ctypes.c_float(1.2), 2, ctypes.byref(rim_pen))
        gdiplus.GdipDrawPath(graphics, rim_pen, card_path)
        gdiplus.GdipDeletePen(rim_pen)
        gdiplus.GdipDeletePath(card_path)

        # 2. Setup DPI-locked Typography (UnitPixel + GenericTypographic Ink Alignment)
        tag_candidates = self._toast_font_candidates("tag")
        sub_candidates = self._toast_font_candidates("sub")
        ui_candidates = self._toast_font_candidates("ui")
        ff_tag = self._create_font_family(tag_candidates)
        ff_sub = self._create_font_family(sub_candidates)
        ff_ui = self._create_font_family(ui_candidates)

        tag_px = 11.0
        sub_px = 14.5
        badge_px = 15.0
        btn_px = 12.5

        f_tag = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_tag, ctypes.c_float(tag_px), 1, 2, ctypes.byref(f_tag))
        f_sub = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_sub, ctypes.c_float(sub_px), 0, 2, ctypes.byref(f_sub))
        f_badge = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_ui, ctypes.c_float(badge_px), 1, 2, ctypes.byref(f_badge))
        f_btn = ctypes.c_void_p()
        gdiplus.GdipCreateFont(ff_ui, ctypes.c_float(btn_px), 1, 2, ctypes.byref(f_btn))

        fmt_text = self._create_typographic_format()

        b_teal = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xFF2DD4BF), ctypes.byref(b_teal))
        b_white = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xFFF1F5F9), ctypes.byref(b_white))
        b_muted = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0xFF94A3B8), ctypes.byref(b_muted))

        clean_title = self.toast_title.lstrip("●•-— ").strip()
        clean_sub = self.toast_subtitle.strip()
        tl, tt, _tr, _tw, th = self._measure_text_ink_bounds(tag_candidates, tag_px, 0, clean_title)
        sl, st, _sr, _sw, sh = self._measure_text_ink_bounds(sub_candidates, sub_px, 0, clean_sub)

        cy = float(h / 2.0)
        line_gap = 4.0
        top_ink_y = cy - (th + line_gap + sh) / 2.0
        sub_ink_y = top_ink_y + th + line_gap

        cx_badge = 34.0
        btn_postpone_rect = (296.0, 18.0, 114.0, 30.0)
        btn_skip_rect = (418.0, 18.0, 64.0, 30.0)
        cx_close = float(w - 22.0)
        anchor_left = 58.0
        tag_x = anchor_left - tl
        sub_x = anchor_left - sl
        text_tag_rect = RectF(tag_x, top_ink_y - tt, max(80.0, 288.0 - tag_x), 26.0)
        text_sub_rect = RectF(sub_x, sub_ink_y - st, max(80.0, 288.0 - sub_x), 30.0)
        postpone_label = f"Postpone ({self.toast_postpone_mins}m)"
        skip_label = "Skip"

        # 3. Circular Countdown Badge
        badge_r = 17.0
        b_dark = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(ctypes.c_uint32(0x4014B8A6), ctypes.byref(b_dark))
        gdiplus.GdipFillEllipse(graphics, b_dark, ctypes.c_float(cx_badge - badge_r), ctypes.c_float(cy - badge_r), ctypes.c_float(badge_r * 2), ctypes.c_float(badge_r * 2))
        gdiplus.GdipDeleteBrush(b_dark)

        p_track = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(ctypes.c_uint32(0x352DD4BF), ctypes.c_float(2.0), 2, ctypes.byref(p_track))
        gdiplus.GdipDrawEllipse(graphics, p_track, ctypes.c_float(cx_badge - badge_r), ctypes.c_float(cy - badge_r), ctypes.c_float(badge_r * 2), ctypes.c_float(badge_r * 2))
        gdiplus.GdipDeletePen(p_track)

        if progress > 0.01:
            p_prog = ctypes.c_void_p()
            gdiplus.GdipCreatePen1(ctypes.c_uint32(0xFF2DD4BF), ctypes.c_float(2.0), 2, ctypes.byref(p_prog))
            sweep = ctypes.c_float(360.0 * progress)
            gdiplus.GdipDrawArc(graphics, p_prog, ctypes.c_float(cx_badge - badge_r), ctypes.c_float(cy - badge_r), ctypes.c_float(badge_r * 2), ctypes.c_float(badge_r * 2), ctypes.c_float(-90), sweep)
            gdiplus.GdipDeletePen(p_prog)

        badge_str = str(rem_sec)
        badge_rect = RectF(cx_badge - badge_r, cy - badge_r, badge_r * 2, badge_r * 2)
        fmt_b = self._create_typographic_format(align=1, line_align=1)
        gdiplus.GdipDrawString(graphics, badge_str, -1, f_badge, ctypes.byref(badge_rect), fmt_b, b_teal)
        gdiplus.GdipDeleteStringFormat(fmt_b)

        # 4. Texts
        gdiplus.GdipDrawString(graphics, clean_title, -1, f_tag, ctypes.byref(text_tag_rect), fmt_text, b_teal)
        gdiplus.GdipDrawString(graphics, clean_sub, -1, f_sub, ctypes.byref(text_sub_rect), fmt_text, b_white)

        # 5. Postpone Button
        if self.toast_can_postpone:
            px, py, pw, ph = btn_postpone_rect
            p_path = self._create_pill_path(px, py, pw, ph, ph / 2.0)
            is_post_hover = (self.toast_hover_btn == "postpone")
            p_fill = ctypes.c_uint32(0x7014B8A6 if is_post_hover else 0x3514B8A6)
            b_pfill = ctypes.c_void_p()
            gdiplus.GdipCreateSolidFill(p_fill, ctypes.byref(b_pfill))
            gdiplus.GdipFillPath(graphics, b_pfill, p_path)
            gdiplus.GdipDeleteBrush(b_pfill)

            p_pen = ctypes.c_void_p()
            gdiplus.GdipCreatePen1(ctypes.c_uint32(0xFF2DD4BF if is_post_hover else 0x852DD4BF), ctypes.c_float(1.2), 2, ctypes.byref(p_pen))
            gdiplus.GdipDrawPath(graphics, p_pen, p_path)
            gdiplus.GdipDeletePen(p_pen)
            gdiplus.GdipDeletePath(p_path)

            p_btn_rect = RectF(px, py, pw, ph)
            fmt_pbtn = self._create_typographic_format(align=1, line_align=1)
            gdiplus.GdipDrawString(graphics, postpone_label, -1, f_btn, ctypes.byref(p_btn_rect), fmt_pbtn, b_white if is_post_hover else b_teal)
            gdiplus.GdipDeleteStringFormat(fmt_pbtn)

        # 6. Skip Button
        sx, sy, sw, sh = btn_skip_rect
        s_path = self._create_pill_path(sx, sy, sw, sh, sh / 2.0)
        is_skip_hover = (self.toast_hover_btn == "skip")
        s_fill = ctypes.c_uint32(0x55475569 if is_skip_hover else 0x22334155)
        b_sfill = ctypes.c_void_p()
        gdiplus.GdipCreateSolidFill(s_fill, ctypes.byref(b_sfill))
        gdiplus.GdipFillPath(graphics, b_sfill, s_path)
        gdiplus.GdipDeleteBrush(b_sfill)

        s_pen = ctypes.c_void_p()
        gdiplus.GdipCreatePen1(ctypes.c_uint32(0xFFCBD5E1 if is_skip_hover else 0x6064748B), ctypes.c_float(1.0), 2, ctypes.byref(s_pen))
        gdiplus.GdipDrawPath(graphics, s_pen, s_path)
        gdiplus.GdipDeletePen(s_pen)
        gdiplus.GdipDeletePath(s_path)

        s_btn_rect = RectF(sx, sy, sw, sh)
        fmt_sbtn = self._create_typographic_format(align=1, line_align=1)
        gdiplus.GdipDrawString(graphics, skip_label, -1, f_btn, ctypes.byref(s_btn_rect), fmt_sbtn, b_white if is_skip_hover else b_muted)
        gdiplus.GdipDeleteStringFormat(fmt_sbtn)

        # 7. Close Cross '×'
        x_p = ctypes.c_void_p()
        is_close_hover = (self.toast_hover_btn == "close")
        x_color = 0xFFF1F5F9 if is_close_hover else 0x8094A3B8
        gdiplus.GdipCreatePen1(ctypes.c_uint32(x_color), ctypes.c_float(1.5), 2, ctypes.byref(x_p))
        gdiplus.GdipDrawLine(graphics, x_p, ctypes.c_float(cx_close - 4.0), ctypes.c_float(cy - 4.0), ctypes.c_float(cx_close + 4.0), ctypes.c_float(cy + 4.0))
        gdiplus.GdipDrawLine(graphics, x_p, ctypes.c_float(cx_close + 4.0), ctypes.c_float(cy - 4.0), ctypes.c_float(cx_close - 4.0), ctypes.c_float(cy + 4.0))
        gdiplus.GdipDeletePen(x_p)

        # Cleanup
        gdiplus.GdipDeleteBrush(b_teal)
        gdiplus.GdipDeleteBrush(b_white)
        gdiplus.GdipDeleteBrush(b_muted)
        gdiplus.GdipDeleteStringFormat(fmt_text)
        gdiplus.GdipDeleteFont(f_tag)
        gdiplus.GdipDeleteFont(f_sub)
        gdiplus.GdipDeleteFont(f_badge)
        gdiplus.GdipDeleteFont(f_btn)
        gdiplus.GdipDeleteFontFamily(ff_tag)
        gdiplus.GdipDeleteFontFamily(ff_sub)
        gdiplus.GdipDeleteFontFamily(ff_ui)

    def _paint_toolbar_window(self, hwnd: int) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            w = max(1, rc.right - rc.left)
            h = max(1, rc.bottom - rc.top)

            color_ref = hex_to_colorref(self.toolbar_color)
            bg_brush = gdi32.CreateSolidBrush(color_ref)
            user32.FillRect(hdc, ctypes.byref(rc), bg_brush)
            gdi32.DeleteObject(bg_brush)

            border_brush = gdi32.CreateSolidBrush(0x00FFFFFF)
            user32.FrameRect(hdc, ctypes.byref(rc), border_brush)
            gdi32.DeleteObject(border_brush)

            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, 0x00FFFFFF)
            hfont = gdi32.CreateFontW(-12, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Linux Biolinum G")
            old = gdi32.SelectObject(hdc, hfont)

            labels = ["🌙 Dark", "◑ Gray", "🔅 Dim", "📌 Pin"]
            btn_w = w // 4
            div_brush = gdi32.CreateSolidBrush(0x00E2E8F0)
            for idx, lbl in enumerate(labels):
                b_rc = wintypes.RECT(idx * btn_w, 0, (idx + 1) * btn_w, h)
                user32.DrawTextW(
                    hdc,
                    lbl,
                    -1,
                    ctypes.byref(b_rc),
                    DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
                )
                if idx > 0:
                    sep_rc = wintypes.RECT(idx * btn_w, 5, idx * btn_w + 1, h - 5)
                    user32.FillRect(hdc, ctypes.byref(sep_rc), div_brush)
            gdi32.DeleteObject(div_brush)
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(hfont)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    # ------------------------------------------------------------------
    # Thread Loop & Window Creation
    # ------------------------------------------------------------------
    def _thread_loop(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = _OVERLAY_WND_PROC_CB
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_ARROW
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self.CLASS_NAME
        wc.hIconSm = None
        user32.RegisterClassExW(ctypes.byref(wc))

        # 1. Create Mini Floating Window (hidden initially)
        self.hwnd_mini = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
            self.CLASS_NAME,
            "VG_MiniTimer",
            WS_POPUP,
            100,
            100,
            150,
            34,
            None,
            None,
            hinst,
            None,
        )
        if self.hwnd_mini:
            user32.SetLayeredWindowAttributes(self.hwnd_mini, 0, 235, LWA_ALPHA)

        # 2. Create Click-Through Toast Window (hidden initially)
        self.hwnd_toast = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TRANSPARENT,
            self.CLASS_NAME,
            "VG_ToastOverlay",
            WS_POPUP,
            100,
            100,
            430,
            60,
            None,
            None,
            hinst,
            None,
        )

        # 3. Create Floating Magic Toolbar Window (hidden initially)
        self.hwnd_toolbar = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED,
            self.CLASS_NAME,
            "VG_MagicToolbar",
            WS_POPUP,
            100,
            100,
            240,
            28,
            None,
            None,
            hinst,
            None,
        )
        if self.hwnd_toolbar:
            user32.SetLayeredWindowAttributes(self.hwnd_toolbar, 0, 238, LWA_ALPHA)

        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            # Drain commands
            while True:
                try:
                    cmd = self._cmd_queue.get_nowait()
                    self._handle_cmd(cmd)
                except queue.Empty:
                    break

            # Pump Win32 messages
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            now = time.monotonic()

            # Check toast expiration and progress animation
            if self.toast_visible:
                if now >= self.toast_expire_ts:
                    self.toast_visible = False
                    self.toast_hover_btn = None
                    if self.hwnd_toast:
                        user32.ShowWindow(self.hwnd_toast, SW_HIDE)
                else:
                    elapsed = now - self.toast_start_ts
                    progress = max(0.0, min(1.0, 1.0 - (elapsed / self.toast_duration)))
                    self._render_and_update_toast(progress=progress)

            # Update mini window hover translucency
            if self.mini_enabled and self.hwnd_mini:
                self._step_mini_translucency()

            # Track foreground window titlebar for Magic Toolbar
            if self.toolbar_enabled and self.hwnd_toolbar:
                self._step_magic_toolbar(now)

            self._wake_event.clear()
            if not self._cmd_queue.empty() or self._stop_event.is_set():
                continue
            if self.toast_visible or self.toolbar_enabled:
                self._wake_event.wait(timeout=0.04)
            elif self.mini_enabled:
                self._wake_event.wait(timeout=0.12)
            else:
                self._wake_event.wait()

        # Cleanup toast DIB and memory DC
        if self._toast_hbm:
            if self._toast_hdc_mem and getattr(self, "_toast_old_hbm", None):
                gdi32.SelectObject(self._toast_hdc_mem, self._toast_old_hbm)
                self._toast_old_hbm = None
            gdi32.DeleteObject(self._toast_hbm)
            self._toast_hbm = None
        if self._toast_hdc_mem:
            gdi32.DeleteDC(self._toast_hdc_mem)
            self._toast_hdc_mem = None

        # Cleanup HWNDs
        for h in (self.hwnd_mini, self.hwnd_toast, self.hwnd_toolbar):
            if h:
                user32.DestroyWindow(h)

    def _get_work_area(self) -> wintypes.RECT:
        rc = wintypes.RECT()
        if not user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rc), 0):
            rc.left, rc.top = 0, 0
            rc.right = user32.GetSystemMetrics(0)
            rc.bottom = user32.GetSystemMetrics(1)
        return rc

    def _handle_cmd(self, cmd: tuple) -> None:
        op = cmd[0]
        if op == "sync_mini":
            if not self.hwnd_mini:
                return
            if not self.mini_enabled:
                user32.ShowWindow(self.hwnd_mini, SW_HIDE)
                return
            wa = self._get_work_area()
            w = 135 if self.mini_style == "bar" else 195
            h = 32 if self.mini_style == "bar" else 44
            if self.mini_x < 0 or self.mini_y < 0:
                self.mini_x = wa.right - w - 24
                self.mini_y = wa.bottom - h - 20
            user32.SetWindowPos(
                self.hwnd_mini,
                HWND_TOPMOST,
                self.mini_x,
                self.mini_y,
                w,
                h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            user32.InvalidateRect(self.hwnd_mini, None, True)

        elif op == "repaint_mini":
            if self.hwnd_mini and self.mini_enabled:
                user32.InvalidateRect(self.hwnd_mini, None, True)

        elif op == "show_toast":
            if not self.hwnd_toast:
                return
            wa = self._get_work_area()
            if self.toast_is_prompt:
                w, h = 530, 66
                # Remove WS_EX_TRANSPARENT so mouse clicks and hovers are captured
                ex = user32.GetWindowLongW(self.hwnd_toast, -20)
                if ex & WS_EX_TRANSPARENT:
                    user32.SetWindowLongW(self.hwnd_toast, -20, ex & ~WS_EX_TRANSPARENT)
            else:
                sub_len = len((self.toast_subtitle or "").strip())
                w, h = (480, 76) if sub_len > 28 else (296, 68)
                # Restore WS_EX_TRANSPARENT for passive click-through blink reminders
                ex = user32.GetWindowLongW(self.hwnd_toast, -20)
                if not (ex & WS_EX_TRANSPARENT):
                    user32.SetWindowLongW(self.hwnd_toast, -20, ex | WS_EX_TRANSPARENT)

            if self.toast_position == "top_center":
                x = wa.left + ((wa.right - wa.left) - w) // 2
                y = wa.top + 28
            else:
                x = wa.right - w - 24
                y = wa.bottom - h - 68
            self.toast_x = x
            self.toast_y = y
            self.toast_w = w
            self.toast_h = h
            self._render_and_update_toast(progress=1.0)

        elif op == "hide_toast":
            if self.hwnd_toast:
                user32.ShowWindow(self.hwnd_toast, SW_HIDE)

        elif op == "hide_toolbar":
            self.toolbar_visible = False
            if self.hwnd_toolbar:
                user32.ShowWindow(self.hwnd_toolbar, SW_HIDE)

    def _step_mini_translucency(self) -> None:
        if not self.hwnd_mini:
            return
        pt = wintypes.POINT()
        rc = wintypes.RECT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.GetWindowRect(self.hwnd_mini, ctypes.byref(rc))
        hovered = rc.left <= pt.x <= rc.right and rc.top <= pt.y <= rc.bottom
        if hovered or not self.mini_auto_translucent:
            alpha = 250
        else:
            alpha = int(255 * (self.mini_opacity_pct / 100.0))
        user32.SetLayeredWindowAttributes(self.hwnd_mini, 0, max(60, min(255, alpha)), LWA_ALPHA)

    def _step_magic_toolbar(self, now: float) -> None:
        fg = user32.GetForegroundWindow()
        if not fg or fg in (self.hwnd_mini, self.hwnd_toast, self.hwnd_toolbar):
            return

        # Ignore desktop / shell windows
        cls_buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(fg, cls_buf, 64)
        if cls_buf.value in ("Progman", "WorkerW", "Shell_TrayWnd", self.CLASS_NAME):
            if self.toolbar_visible:
                self.toolbar_visible = False
                user32.ShowWindow(self.hwnd_toolbar, SW_HIDE)
            return

        rc = wintypes.RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            fg,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rc),
            ctypes.sizeof(rc),
        )
        if hr != 0:
            user32.GetWindowRect(fg, ctypes.byref(rc))

        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))

        # Check if cursor is in the titlebar zone of the foreground window (or over the toolbar itself)
        tb_rc = wintypes.RECT()
        if self.toolbar_visible:
            user32.GetWindowRect(self.hwnd_toolbar, ctypes.byref(tb_rc))
        on_toolbar = (
            self.toolbar_visible
            and (tb_rc.left - 8 <= pt.x <= tb_rc.right + 8)
            and (tb_rc.top - 8 <= pt.y <= tb_rc.bottom + 8)
        )
        in_titlebar = (
            (rc.left <= pt.x <= rc.right)
            and (rc.top - 4 <= pt.y <= rc.top + 42)
            and (rc.right - rc.left >= 260)
        )

        if in_titlebar or on_toolbar:
            if self.toolbar_hover_start_ts == 0.0:
                self.toolbar_hover_start_ts = now
            if (now - self.toolbar_hover_start_ts) >= self.toolbar_delay_sec:
                self.toolbar_target_hwnd = fg
                w, h = 240, 28
                win_w = rc.right - rc.left
                if self.toolbar_placement == "left":
                    x = rc.left + 16 + self.toolbar_offset_dp
                elif self.toolbar_placement == "right":
                    x = rc.right - w - 145 + self.toolbar_offset_dp
                else:
                    x = rc.left + (win_w - w) // 2 + self.toolbar_offset_dp
                y = max(0, rc.top + 4)
                user32.SetWindowPos(
                    self.hwnd_toolbar,
                    HWND_TOPMOST,
                    x,
                    y,
                    w,
                    h,
                    SWP_NOACTIVATE | SWP_SHOWWINDOW,
                )
                if not self.toolbar_visible:
                    self.toolbar_visible = True
                    user32.InvalidateRect(self.hwnd_toolbar, None, True)
        else:
            self.toolbar_hover_start_ts = 0.0
            if self.toolbar_visible:
                self.toolbar_visible = False
                user32.ShowWindow(self.hwnd_toolbar, SW_HIDE)
