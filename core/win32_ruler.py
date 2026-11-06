"""
EyeGuard — Pure Native Win32 Layered Focus Engine (Reading Focus + Spotlight Focus)
Runs on a dedicated background message pump thread (EyeGuard-RulerThread).
Supports:
1. Reading Focus: Horizontal reading slit with custom height, custom opacity, custom RGB color picker, and ESC to quit preview.
2. Spotlight Focus: Active foreground window tracker dimming all background windows using 4 surround layered windows,
   with configurable transition animation (ms), taskbar area inclusion, monitor scoping, custom color, and opacity.
"""

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Optional, Tuple, Dict, Any

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

# Win32 Window Styles
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

# Extended Window Styles: Layered, Click-Through, Topmost, ToolWindow, NoActivate
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

# Layered Window Alpha Flag
LWA_ALPHA = 0x00000002

# SetWindowPos Flags
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080

# Win32 Message Identifiers
WM_QUIT = 0x0012
WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
VK_ESCAPE = 0x1B
SPI_GETWORKAREA = 0x0030

# System Metrics for Multi-Monitor Virtual Desktop
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

# GDI Stock Objects
BLACK_BRUSH = 4

# Window Procedure and Class Definition (64-bit safe sizes)
LRESULT = ctypes.c_ssize_t
LPARAM = ctypes.c_size_t
WPARAM = ctypes.c_size_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", wintypes.UINT),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", getattr(wintypes, "HCURSOR", wintypes.HANDLE)),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


# Setup Win32 Function Signatures with 64-bit cleanliness
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD
]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

user32.RegisterClassExW.argtypes = [ctypes.c_void_p]
user32.RegisterClassExW.restype = wintypes.ATOM

user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
user32.UnregisterClassW.restype = wintypes.BOOL

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int

user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT), wintypes.BOOL]
user32.InvalidateRect.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD

gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.GetStockObject.restype = wintypes.HBRUSH

gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH

gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

# Global reference to current brush for dynamic background painting
_ACTIVE_BRUSH = None


def hex_to_colorref(hex_str: str) -> int:
    """Convert '#RRGGBB' to Win32 COLORREF (0x00BBGGRR)."""
    if not hex_str or not isinstance(hex_str, str):
        return 0x00000000
    s = hex_str.lstrip("#")
    if len(s) != 6:
        return 0x00000000
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (b << 16) | (g << 8) | r
    except ValueError:
        return 0x00000000


def _overlay_wnd_proc(hwnd, msg, wparam, lparam):
    """
    Window procedure for the focus overlay windows.
    Guarantees click-through via HTTRANSPARENT and custom color painting via WM_ERASEBKGND.
    """
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT
    if msg == WM_ERASEBKGND and _ACTIVE_BRUSH:
        rc = wintypes.RECT()
        if user32.GetClientRect(hwnd, ctypes.byref(rc)):
            user32.FillRect(wintypes.HDC(wparam), ctypes.byref(rc), _ACTIVE_BRUSH)
            return 1
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class Win32FocusRuler:
    """
    Dual-mode native Win32 Focus Engine:
    - Mode 1 ('reading'): Horizontal Reading Focus slit tracking cursor Y.
    - Mode 2 ('spotlight'): Spotlight Focus dimming all background windows around the active foreground window.
    """
    _CLASS_NAME = "EyeGuardRulerOverlay"

    def __init__(self, slit_height: int = 260, opacity: float = 0.65, color_hex: str = "#000000"):
        self._slit_height = max(50, min(1000, int(slit_height)))
        self._opacity = self._normalize_opacity(opacity)
        self._color_hex = color_hex if color_hex else "#000000"

        # Spotlight Focus settings
        self._spotlight_active = False
        self._spotlight_opacity = 0.60
        self._spotlight_color = "#000000"
        self._spotlight_include_taskbar = False
        self._spotlight_transition = True
        self._spotlight_anim_ms = 200
        self._spotlight_current_monitor = True

        # Smooth interpolation state for Spotlight rectangle
        self._cur_spot_rect: Optional[Tuple[float, float, float, float]] = None

        self._hwnd_top: Optional[int] = None
        self._hwnd_bot: Optional[int] = None
        self._hwnd_left: Optional[int] = None
        self._hwnd_right: Optional[int] = None
        self._is_active = False
        self._preview_esc_quit = False
        self._custom_brush = None

        self._vx = 0
        self._vy = 0
        self._vw = 1920
        self._vh = 1080
        self._current_y = 540

        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._running = False
        self._ready_event = threading.Event()
        self._wake_event = threading.Event()
        self._cmd_queue: queue.Queue = queue.Queue()
        self._lifecycle_lock = threading.Lock()

        self._wnd_proc_cb = WNDPROC(_overlay_wnd_proc)
        self._class_registered = False
        self._hinstance = kernel32.GetModuleHandleW(None)

        self._ensure_thread_running()

    @staticmethod
    def _normalize_opacity(val: float | int) -> float:
        if val is None:
            return 0.65
        f_val = float(val)
        if f_val > 1.0:
            f_val = f_val / 100.0
        return max(0.05, min(0.95, f_val))

    # =========================================================================
    # Public Thread-Safe API
    # =========================================================================
    @property
    def is_active(self) -> bool:
        return self._is_active

    @property
    def is_spotlight_active(self) -> bool:
        return self._spotlight_active

    @property
    def hwnd_top(self) -> Optional[int]:
        return self._hwnd_top

    @property
    def hwnd_bot(self) -> Optional[int]:
        return self._hwnd_bot

    @property
    def slit_height(self) -> int:
        return self._slit_height

    @slit_height.setter
    def slit_height(self, val: int):
        self.set_height(val)

    @property
    def opacity(self) -> float:
        return self._opacity

    @opacity.setter
    def opacity(self, val: float | int):
        self.set_opacity(val)

    @property
    def color_hex(self) -> str:
        return self._color_hex

    @property
    def current_y(self) -> int:
        return self._current_y

    @property
    def virtual_bounds(self) -> Tuple[int, int, int, int]:
        return (self._vx, self._vy, self._vw, self._vh)

    def show(self, slit_height: Optional[int] = 260, opacity: Optional[float] = 0.65):
        if self._is_active:
            self.update(slit_height=slit_height, opacity=opacity)
        else:
            self.enable(slit_height=slit_height, opacity=opacity)

    def hide(self):
        self.disable()

    def toggle(self, preview_esc: bool = False):
        self._ensure_thread_running()
        self._send_command('TOGGLE', preview_esc)

    def enable(self, slit_height: Optional[int] = None, opacity: Optional[float] = None,
               color_hex: Optional[str] = None, preview_esc: bool = False):
        self._ensure_thread_running()
        self._send_command('ENABLE', (slit_height, opacity, color_hex, preview_esc))

    def disable(self):
        if not self._running:
            return
        self._send_command('DISABLE', None)

    def update(self, slit_height: Optional[int] = None, opacity: Optional[float] = None,
               color_hex: Optional[str] = None):
        self._ensure_thread_running()
        self._send_command('UPDATE', (slit_height, opacity, color_hex))

    def set_height(self, h: int):
        self.update(slit_height=h)

    def set_opacity(self, pct: int | float):
        self.update(opacity=pct)

    def set_color(self, color_hex: str):
        self.update(color_hex=color_hex)

    # Spotlight Focus Controls
    def configure_spotlight(self, enabled: Optional[bool] = None,
                            opacity: Optional[float] = None,
                            color_hex: Optional[str] = None,
                            include_taskbar: Optional[bool] = None,
                            transition: Optional[bool] = None,
                            enable_anim: Optional[bool] = None,
                            anim_ms: Optional[int] = None,
                            current_monitor: Optional[bool] = None,
                            current_monitor_only: Optional[bool] = None):
        trans = transition if transition is not None else enable_anim
        cur_mon = current_monitor if current_monitor is not None else current_monitor_only
        if opacity is not None:
            self._spotlight_opacity = self._normalize_opacity(opacity)
        if color_hex is not None:
            self._spotlight_color = str(color_hex)
        if include_taskbar is not None:
            self._spotlight_include_taskbar = bool(include_taskbar)
        if trans is not None:
            self._spotlight_transition = bool(trans)
        if anim_ms is not None:
            self._spotlight_anim_ms = max(50, min(500, int(anim_ms)))
        if cur_mon is not None:
            self._spotlight_current_monitor = bool(cur_mon)

        if enabled is not None or self._running:
            self._ensure_thread_running()
            self._send_command('CONFIG_SPOTLIGHT', {
                "enabled": enabled,
                "opacity": opacity,
                "color_hex": color_hex,
                "include_taskbar": include_taskbar,
                "transition": trans,
                "anim_ms": anim_ms,
                "current_monitor": cur_mon,
            })
        return self.get_focus_state()

    def toggle_spotlight(self, enable: Optional[bool] = None):
        self._ensure_thread_running()
        if enable is not None:
            self.configure_spotlight(enabled=bool(enable))
        else:
            self._send_command('TOGGLE_SPOTLIGHT', None)
        return self._spotlight_active

    def get_focus_state(self) -> Dict[str, Any]:
        return {
            "reading_active": self._is_active,
            "slit_height": self._slit_height,
            "reading_opacity": int(round(self._opacity * 100)),
            "reading_color": self._color_hex,
            "spotlight_active": self._spotlight_active,
            "spotlight_opacity": int(round(self._spotlight_opacity * 100)),
            "spotlight_color": self._spotlight_color,
            "spotlight_include_taskbar": self._spotlight_include_taskbar,
            "spotlight_transition": self._spotlight_transition,
            "spotlight_anim_ms": self._spotlight_anim_ms,
            "spotlight_current_monitor": self._spotlight_current_monitor,
        }

    def destroy(self):
        with self._lifecycle_lock:
            if not self._running:
                return
            thread = self._thread
            tid = self._thread_id

        self._send_command('DESTROY', None)

        if tid:
            user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)

        if thread and thread.is_alive():
            thread.join(timeout=1.0)

        with self._lifecycle_lock:
            self._running = False
            self._thread = None
            self._thread_id = None
            self._hwnd_top = None
            self._hwnd_bot = None
            self._hwnd_left = None
            self._hwnd_right = None
            self._is_active = False
            self._spotlight_active = False

    # =========================================================================
    # Internal Thread Management & Command Dispatch
    # =========================================================================
    def _ensure_thread_running(self):
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive() and self._running:
                return
            self._running = True
            self._ready_event.clear()
            self._wake_event.clear()
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                except queue.Empty:
                    break

            self._thread = threading.Thread(
                target=self._ruler_thread_func,
                name="EyeGuard-RulerThread",
                daemon=True
            )
            self._thread.start()

        self._ready_event.wait(timeout=2.0)

    def _send_command(self, cmd: str, args):
        if threading.current_thread() == self._thread:
            self._dispatch_command(cmd, args)
            return

        ack = threading.Event()
        self._cmd_queue.put((cmd, args, ack))
        self._wake_event.set()
        ack.wait(timeout=2.0)

    def _ruler_thread_func(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        self._register_class()
        self._update_virtual_desktop_metrics()
        self._current_y = self._vy + self._vh // 2
        self._update_brush(self._color_hex)

        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._ready_event.set()

        while self._running:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_QUIT:
                    self._running = False
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            if not self._running:
                break

            self._drain_commands()
            if not self._running:
                break
            if not self._cmd_queue.empty():
                continue

            if self._is_active or self._spotlight_active:
                t0 = time.perf_counter()
                # Check ESC key to exit if preview mode or active ruler
                if self._preview_esc_quit and (user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000):
                    self._disable_internal()
                    self._preview_esc_quit = False
                    continue

                if self._spotlight_active:
                    self._track_spotlight_step()
                elif self._is_active:
                    self._track_cursor_step()

                elapsed = time.perf_counter() - t0
                sleep_sec = max(0.001, (1.0 / 60.0) - elapsed)
                self._wake_event.clear()
                if not self._cmd_queue.empty():
                    continue
                self._wake_event.wait(timeout=sleep_sec)
            else:
                self._wake_event.clear()
                if not self._cmd_queue.empty():
                    continue
                self._wake_event.wait(timeout=0.05)

        self._disable_internal()
        self._cleanup_brush()
        self._unregister_class()

    def _drain_commands(self):
        while not self._cmd_queue.empty():
            try:
                cmd, args, ack = self._cmd_queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._dispatch_command(cmd, args)
            except Exception:
                pass
            finally:
                ack.set()

    def _dispatch_command(self, cmd: str, args):
        if cmd == 'ENABLE':
            if isinstance(args, tuple) and len(args) == 4:
                h, op, col, esc = args
            elif isinstance(args, tuple) and len(args) == 2:
                h, op = args
                col, esc = None, False
            else:
                h, op, col, esc = None, None, None, False
            self._spotlight_active = False
            self._preview_esc_quit = bool(esc)
            self._enable_internal(h, op, col)
        elif cmd == 'DISABLE':
            self._spotlight_active = False
            self._preview_esc_quit = False
            self._disable_internal()
        elif cmd == 'TOGGLE':
            self._preview_esc_quit = bool(args) if args is not None else False
            if self._is_active:
                self._disable_internal()
            else:
                self._spotlight_active = False
                self._enable_internal(None, None, None)
        elif cmd == 'UPDATE':
            if isinstance(args, tuple) and len(args) == 3:
                h, op, col = args
            elif isinstance(args, tuple) and len(args) == 2:
                h, op = args
                col = None
            else:
                h, op, col = None, None, None
            self._update_internal(h, op, col)
        elif cmd == 'CONFIG_SPOTLIGHT':
            cfg = args or {}
            if cfg.get("opacity") is not None:
                self._spotlight_opacity = self._normalize_opacity(cfg["opacity"])
            if cfg.get("color_hex"):
                self._spotlight_color = str(cfg["color_hex"])
            if cfg.get("include_taskbar") is not None:
                self._spotlight_include_taskbar = bool(cfg["include_taskbar"])
            if cfg.get("transition") is not None:
                self._spotlight_transition = bool(cfg["transition"])
            if cfg.get("anim_ms") is not None:
                self._spotlight_anim_ms = max(20, min(1000, int(cfg["anim_ms"])))
            if cfg.get("current_monitor") is not None:
                self._spotlight_current_monitor = bool(cfg["current_monitor"])
            if cfg.get("enabled") is not None:
                en = bool(cfg["enabled"])
                if en:
                    self._is_active = False
                    self._spotlight_active = True
                    self._enable_spotlight_internal()
                else:
                    self._spotlight_active = False
                    self._disable_internal()
            elif self._spotlight_active:
                self._enable_spotlight_internal()
        elif cmd == 'TOGGLE_SPOTLIGHT':
            if self._spotlight_active:
                self._spotlight_active = False
                self._disable_internal()
            else:
                self._is_active = False
                self._spotlight_active = True
                self._enable_spotlight_internal()
        elif cmd == 'DESTROY':
            self._spotlight_active = False
            self._disable_internal()
            self._running = False

    def _update_brush(self, hex_color: str):
        global _ACTIVE_BRUSH
        cref = hex_to_colorref(hex_color)
        new_brush = gdi32.CreateSolidBrush(cref)
        old_brush = self._custom_brush
        self._custom_brush = new_brush
        _ACTIVE_BRUSH = new_brush
        if old_brush:
            gdi32.DeleteObject(old_brush)
        for hw in (self._hwnd_top, self._hwnd_bot, self._hwnd_left, self._hwnd_right):
            if hw:
                user32.InvalidateRect(hw, None, True)

    def _cleanup_brush(self):
        global _ACTIVE_BRUSH
        if self._custom_brush:
            gdi32.DeleteObject(self._custom_brush)
            self._custom_brush = None
        _ACTIVE_BRUSH = None

    def _register_class(self):
        if self._class_registered:
            return
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = self._wnd_proc_cb
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self._hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = gdi32.GetStockObject(BLACK_BRUSH)
        wc.lpszMenuName = None
        wc.lpszClassName = self._CLASS_NAME
        wc.hIconSm = None

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if atom or kernel32.GetLastError() == 1410:
            self._class_registered = True

    def _unregister_class(self):
        if self._class_registered:
            user32.UnregisterClassW(self._CLASS_NAME, self._hinstance)
            self._class_registered = False

    def _update_virtual_desktop_metrics(self):
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 0 or vh <= 0:
            vx = 0
            vy = 0
            vw = user32.GetSystemMetrics(SM_CXSCREEN)
            vh = user32.GetSystemMetrics(SM_CYSCREEN)
        self._vx = vx
        self._vy = vy
        self._vw = vw
        self._vh = vh

    def _create_overlay_hwnd(self, title: str, x: int, y: int, w: int, h: int, alpha_byte: int) -> int:
        ex_style = (
            WS_EX_LAYERED |
            WS_EX_TRANSPARENT |
            WS_EX_TOPMOST |
            WS_EX_TOOLWINDOW |
            WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex_style, self._CLASS_NAME, title, WS_POPUP,
            x, y, max(0, w), max(0, h),
            None, None, self._hinstance, None
        )
        if hwnd:
            user32.SetLayeredWindowAttributes(hwnd, 0, alpha_byte, LWA_ALPHA)
        return hwnd

    def _enable_internal(self, slit_height: Optional[int], opacity: Optional[float], color_hex: Optional[str] = None):
        if slit_height is not None:
            self._slit_height = max(50, min(1000, int(slit_height)))
        if opacity is not None:
            self._opacity = self._normalize_opacity(opacity)
        if color_hex:
            self._color_hex = str(color_hex)

        self._update_virtual_desktop_metrics()
        self._register_class()
        self._update_brush(self._color_hex)

        # Destroy left/right spotlight windows if switching from spotlight mode
        for attr in ("_hwnd_left", "_hwnd_right"):
            hw = getattr(self, attr)
            if hw:
                user32.DestroyWindow(hw)
                setattr(self, attr, None)

        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            self._current_y = max(self._vy, min(self._vy + self._vh, pt.y))
        else:
            self._current_y = self._vy + self._vh // 2

        vx, vy, vw, vh = self._vx, self._vy, self._vw, self._vh
        half = self._slit_height // 2
        slit_top = self._current_y - half
        slit_bot = self._current_y + half

        top_h = max(0, min(vh, slit_top - vy))
        bot_y = max(vy, min(vy + vh, slit_bot))
        bot_h = max(0, (vy + vh) - bot_y)

        alpha_byte = int(max(0, min(255, round(self._opacity * 255))))

        if not self._hwnd_top:
            self._hwnd_top = self._create_overlay_hwnd("EyeGuardRulerTop", vx, vy, vw, top_h, alpha_byte)
        else:
            user32.SetLayeredWindowAttributes(self._hwnd_top, 0, alpha_byte, LWA_ALPHA)

        if not self._hwnd_bot:
            self._hwnd_bot = self._create_overlay_hwnd("EyeGuardRulerBot", vx, bot_y, vw, bot_h, alpha_byte)
        else:
            user32.SetLayeredWindowAttributes(self._hwnd_bot, 0, alpha_byte, LWA_ALPHA)

        flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
        if self._hwnd_top:
            user32.SetWindowPos(self._hwnd_top, None, vx, vy, vw, top_h, flags)
        if self._hwnd_bot:
            user32.SetWindowPos(self._hwnd_bot, None, vx, bot_y, vw, bot_h, flags)

        self._is_active = True

    def _enable_spotlight_internal(self):
        self._update_virtual_desktop_metrics()
        self._register_class()
        self._update_brush(self._spotlight_color)

        alpha_byte = int(max(0, min(255, round(self._spotlight_opacity * 255))))
        for attr, name in [
            ("_hwnd_top", "VGSpotTop"),
            ("_hwnd_bot", "VGSpotBot"),
            ("_hwnd_left", "VGSpotLeft"),
            ("_hwnd_right", "VGSpotRight"),
        ]:
            hw = getattr(self, attr)
            if not hw:
                hw = self._create_overlay_hwnd(name, 0, 0, 0, 0, alpha_byte)
                setattr(self, attr, hw)
            else:
                user32.SetLayeredWindowAttributes(hw, 0, alpha_byte, LWA_ALPHA)

        self._cur_spot_rect = None
        self._track_spotlight_step(force=True)

    def _disable_internal(self):
        for attr in ("_hwnd_top", "_hwnd_bot", "_hwnd_left", "_hwnd_right"):
            hw = getattr(self, attr)
            if hw:
                user32.DestroyWindow(hw)
                setattr(self, attr, None)
        self._is_active = False
        self._cur_spot_rect = None

    def _update_internal(self, slit_height: Optional[int], opacity: Optional[float], color_hex: Optional[str] = None):
        if slit_height is not None:
            self._slit_height = max(50, min(1000, int(slit_height)))
        if color_hex:
            self._color_hex = str(color_hex)
            self._update_brush(self._color_hex)
        if opacity is not None:
            self._opacity = self._normalize_opacity(opacity)
            alpha_byte = int(max(0, min(255, round(self._opacity * 255))))
            if self._hwnd_top:
                user32.SetLayeredWindowAttributes(self._hwnd_top, 0, alpha_byte, LWA_ALPHA)
            if self._hwnd_bot:
                user32.SetLayeredWindowAttributes(self._hwnd_bot, 0, alpha_byte, LWA_ALPHA)
        self._reposition()

    def _reposition(self):
        if not self._is_active or not self._hwnd_top or not self._hwnd_bot:
            return
        vx, vy, vw, vh = self._vx, self._vy, self._vw, self._vh
        half = self._slit_height // 2
        slit_top = self._current_y - half
        slit_bot = self._current_y + half

        top_h = max(0, min(vh, slit_top - vy))
        bot_y = max(vy, min(vy + vh, slit_bot))
        bot_h = max(0, (vy + vh) - bot_y)

        flags = SWP_NOZORDER | SWP_NOACTIVATE
        user32.SetWindowPos(self._hwnd_top, None, vx, vy, vw, top_h, flags)
        user32.SetWindowPos(self._hwnd_bot, None, vx, bot_y, vw, bot_h, flags)

    def _track_cursor_step(self):
        if not self._is_active or not self._hwnd_top or not self._hwnd_bot:
            return

        # Game & Fullscreen Guard: auto-pause ruler overlay over exclusive fullscreen games
        fg = user32.GetForegroundWindow()
        if fg:
            try:
                from core.window_focus import is_foreground_fullscreen
                if is_foreground_fullscreen(fg):
                    if user32.IsWindowVisible(self._hwnd_top):
                        user32.ShowWindow(self._hwnd_top, 0)
                        user32.ShowWindow(self._hwnd_bot, 0)
                    return
                elif not user32.IsWindowVisible(self._hwnd_top):
                    user32.ShowWindow(self._hwnd_top, 4)
                    user32.ShowWindow(self._hwnd_bot, 4)
            except Exception:
                pass
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            target_y = max(self._vy, min(self._vy + self._vh, pt.y))
            if target_y != self._current_y:
                self._current_y = target_y
                self._reposition()

    def _track_spotlight_step(self, force: bool = False):
        if not self._spotlight_active:
            return
        fg = user32.GetForegroundWindow()
        if not fg:
            return

        # Game & Fullscreen Guard: auto-pause spotlight overlay over exclusive fullscreen viewports
        try:
            from core.window_focus import is_foreground_fullscreen
            if is_foreground_fullscreen(fg):
                for h in (self._hwnd_spot_top, self._hwnd_spot_bot, self._hwnd_spot_left, self._hwnd_spot_right):
                    if h and user32.IsWindowVisible(h):
                        user32.ShowWindow(h, 0)
                return
        except Exception:
            pass

        rc = wintypes.RECT()
        # Prefer DWMWA_EXTENDED_FRAME_BOUNDS (9) to exclude Windows 10/11 invisible drop shadow
        hr = dwmapi.DwmGetWindowAttribute(
            fg, 9, ctypes.byref(rc), ctypes.sizeof(rc)
        )
        if hr != 0:
            if not user32.GetWindowRect(fg, ctypes.byref(rc)):
                return

        # Determine bounds: either the current monitor of the active window or full virtual desktop
        bx1, by1, bx2, by2 = self._vx, self._vy, self._vx + self._vw, self._vy + self._vh
        if self._spotlight_current_monitor:
            hmon = user32.MonitorFromWindow(fg, 2)  # MONITOR_DEFAULTTONEAREST
            if hmon:
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                    bound_rc = mi.rcMonitor if self._spotlight_include_taskbar else mi.rcWork
                    bx1, by1, bx2, by2 = bound_rc.left, bound_rc.top, bound_rc.right, bound_rc.bottom
            elif not self._spotlight_include_taskbar:
                work_rc = wintypes.RECT()
                if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work_rc), 0):
                    bx1, by1, bx2, by2 = work_rc.left, work_rc.top, work_rc.right, work_rc.bottom
        elif not self._spotlight_include_taskbar:
            work_rc = wintypes.RECT()
            if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(work_rc), 0):
                bx1, by1, bx2, by2 = work_rc.left, work_rc.top, work_rc.right, work_rc.bottom

        tx1 = float(max(bx1, min(bx2, rc.left)))
        ty1 = float(max(by1, min(by2, rc.top)))
        tx2 = float(max(bx1, min(bx2, rc.right)))
        ty2 = float(max(by1, min(by2, rc.bottom)))

        if self._cur_spot_rect is None or not self._spotlight_transition or force:
            self._cur_spot_rect = (tx1, ty1, tx2, ty2)
        else:
            cx1, cy1, cx2, cy2 = self._cur_spot_rect
            # Smooth exponential interpolation factor based on animation duration
            frames = max(1.0, (self._spotlight_anim_ms / 1000.0) * 60.0)
            alpha = min(1.0, 3.0 / frames)
            nx1 = cx1 + (tx1 - cx1) * alpha
            ny1 = cy1 + (ty1 - cy1) * alpha
            nx2 = cx2 + (tx2 - cx2) * alpha
            ny2 = cy2 + (ty2 - cy2) * alpha
            if abs(nx1 - tx1) < 1 and abs(ny1 - ty1) < 1 and abs(nx2 - tx2) < 1 and abs(ny2 - ty2) < 1:
                self._cur_spot_rect = (tx1, ty1, tx2, ty2)
            else:
                self._cur_spot_rect = (nx1, ny1, nx2, ny2)

        fx1, fy1, fx2, fy2 = [int(round(v)) for v in self._cur_spot_rect]
        bw = bx2 - bx1
        bh = by2 - by1

        flags = SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
        # Top rectangle: (bx1, by1) -> width bw, height (fy1 - by1)
        if self._hwnd_top:
            user32.SetWindowPos(self._hwnd_top, None, bx1, by1, bw, max(0, fy1 - by1), flags)
        # Bottom rectangle: (bx1, fy2) -> width bw, height (by2 - fy2)
        if self._hwnd_bot:
            user32.SetWindowPos(self._hwnd_bot, None, bx1, fy2, bw, max(0, by2 - fy2), flags)
        # Left rectangle: (bx1, fy1) -> width (fx1 - bx1), height (fy2 - fy1)
        if self._hwnd_left:
            user32.SetWindowPos(self._hwnd_left, None, bx1, fy1, max(0, fx1 - bx1), max(0, fy2 - fy1), flags)
        # Right rectangle: (fx2, fy1) -> width (bx2 - fx2), height (fy2 - fy1)
        if self._hwnd_right:
            user32.SetWindowPos(self._hwnd_right, None, fx2, fy1, max(0, bx2 - fx2), max(0, fy2 - fy1), flags)
