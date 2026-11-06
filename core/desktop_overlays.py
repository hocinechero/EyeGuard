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
DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
TRANSPARENT = 1
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
    ) -> None:
        global _OVERLAY_MGR_REF
        _OVERLAY_MGR_REF = self

        self.on_toolbar_action = on_toolbar_action
        self.on_open_main_window = on_open_main_window

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
        self.toast_title = "Blink Reminder — Rest Your Eyes"
        self.toast_subtitle = "Blink gently now to refresh your tear film and relax focus."

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
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_loop,
            name="VG-DesktopOverlays-Thread",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

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

    def show_toast(
        self,
        title: str,
        subtitle: str,
        position: str = "bottom_right",
        duration_sec: float = 4.0,
    ) -> None:
        self.toast_title = title
        self.toast_subtitle = subtitle
        self.toast_position = "top_center" if position == "top_center" else "bottom_right"
        self.toast_expire_ts = time.monotonic() + max(1.0, float(duration_sec))
        self.toast_visible = True
        self._cmd_queue.put(("show_toast",))

    def hide_toast(self) -> None:
        self.toast_visible = False
        self._cmd_queue.put(("hide_toast",))

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
            if msg == WM_PAINT:
                self._paint_toast_window(hwnd)
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
                -font_h, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI"
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

    def _paint_toast_window(self, hwnd: int) -> None:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        if not hdc:
            return
        try:
            rc = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rc))
            w = max(1, rc.right - rc.left)
            h = max(1, rc.bottom - rc.top)

            bg_brush = gdi32.CreateSolidBrush(0x00241D18)
            user32.FillRect(hdc, ctypes.byref(rc), bg_brush)
            gdi32.DeleteObject(bg_brush)

            # Accent top bar
            top_rc = wintypes.RECT(0, 0, w, 4)
            teal_brush = gdi32.CreateSolidBrush(0x00E9A50E)  # #5eead4 BGR
            user32.FillRect(hdc, ctypes.byref(top_rc), teal_brush)
            gdi32.DeleteObject(teal_brush)

            gdi32.SetBkMode(hdc, TRANSPARENT)
            # Title
            gdi32.SetTextColor(hdc, 0x00E9A50E)
            f_title = gdi32.CreateFontW(-16, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
            old = gdi32.SelectObject(hdc, f_title)
            r1 = wintypes.RECT(10, 8, w - 10, 32)
            user32.DrawTextW(
                hdc,
                f"👁  {self.toast_title}",
                -1,
                ctypes.byref(r1),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            gdi32.SelectObject(hdc, old)
            gdi32.DeleteObject(f_title)

            # Subtitle
            gdi32.SetTextColor(hdc, 0x00E2E8F0)
            f_sub = gdi32.CreateFontW(-13, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
            old2 = gdi32.SelectObject(hdc, f_sub)
            r2 = wintypes.RECT(10, 34, w - 10, h - 6)
            user32.DrawTextW(
                hdc,
                self.toast_subtitle,
                -1,
                ctypes.byref(r2),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            gdi32.SelectObject(hdc, old2)
            gdi32.DeleteObject(f_sub)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

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
            hfont = gdi32.CreateFontW(-12, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 4, 0, "Segoe UI")
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
            340,
            64,
            None,
            None,
            hinst,
            None,
        )
        if self.hwnd_toast:
            user32.SetLayeredWindowAttributes(self.hwnd_toast, 0, 240, LWA_ALPHA)

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

            # Check toast expiration
            if self.toast_visible and now >= self.toast_expire_ts:
                self.toast_visible = False
                if self.hwnd_toast:
                    user32.ShowWindow(self.hwnd_toast, SW_HIDE)

            # Update mini window hover translucency
            if self.mini_enabled and self.hwnd_mini:
                self._step_mini_translucency()

            # Track foreground window titlebar for Magic Toolbar
            if self.toolbar_enabled and self.hwnd_toolbar:
                self._step_magic_toolbar(now)

            time.sleep(0.04)

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
            w, h = 360, 66
            if self.toast_position == "top_center":
                x = wa.left + ((wa.right - wa.left) - w) // 2
                y = wa.top + 24
            else:
                x = wa.right - w - 24
                y = wa.bottom - h - 68
            user32.SetWindowPos(
                self.hwnd_toast,
                HWND_TOPMOST,
                x,
                y,
                w,
                h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            user32.InvalidateRect(self.hwnd_toast, None, True)

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
