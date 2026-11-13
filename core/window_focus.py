import ctypes
from ctypes import wintypes
import os
import threading
import time
from typing import Dict, List, Optional, Callable, Any, Tuple

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
LWA_ALPHA = 0x00000002

SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_USE_IMMERSIVE_DARK_MODE = 20

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def get_process_name_for_hwnd(hwnd: int) -> str:
    if not hwnd:
        return ""
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not hproc:
        return ""
    try:
        buf_len = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(buf_len)):
            return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(hproc)
    return ""


def get_foreground_process_name() -> Tuple[int, str]:
    """Returns (hwnd, exe_basename_lower) of the current foreground window."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0, ""
    return hwnd, get_process_name_for_hwnd(hwnd)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260)
    ]

def get_running_window_processes() -> List[str]:
    """Returns sorted list of unique application .exe names currently running."""
    found = set()
    own_pid = os.getpid()

    def _enum_cb(hwnd, lparam):
        try:
            if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value and pid.value != own_pid:
                    exe = get_process_name_for_hwnd(hwnd)
                    if exe:
                        clean = exe.lower().strip()
                        if clean not in ("explorer.exe", "applicationframehost.exe", "textinputhost.exe"):
                            found.add(clean)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception:
        pass

    if found:
        return sorted(found)

    # Robust Toolhelp fallback if EnumWindows returns empty set
    try:
        SYSTEM_EXCLUDES = {
            "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
            "lsass.exe", "svchost.exe", "fontdrvhost.exe", "dwm.exe", "spoolsv.exe",
            "conhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "explorer.exe",
            "searchindexer.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
            "applicationframehost.exe", "textinputhost.exe", "securityhealthservice.exe",
            "runtimebroker.exe", "dllhost.exe", "smartscreen.exe", "audiodg.exe"
        }
        hSnapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
            while True:
                exe = pe.szExeFile.decode("utf-8", errors="ignore").lower().strip()
                if exe and exe.endswith(".exe") and exe not in SYSTEM_EXCLUDES and not exe.startswith("system"):
                    found.add(exe)
                if not kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(hSnapshot)
    except Exception:
        pass

    return sorted(found)


def is_any_process_running(target_names: set) -> bool:
    """Fast O(n) Toolhelp snapshot check for process names (zero OpenProcess calls)."""
    if not target_names:
        return False
    try:
        hSnapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not hSnapshot or hSnapshot == -1:
            return False
        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if kernel32.Process32First(hSnapshot, ctypes.byref(pe)):
                while True:
                    exe = pe.szExeFile.decode("utf-8", errors="ignore").lower().strip()
                    if exe in target_names:
                        return True
                    if not kernel32.Process32Next(hSnapshot, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(hSnapshot)
    except Exception:
        pass
    return False


def is_foreground_fullscreen(hwnd: Optional[int] = None) -> bool:
    """Checks if the target (or foreground) application window is running in true fullscreen mode."""
    hwnd = hwnd or user32.GetForegroundWindow()
    if not hwnd:
        return False
    class_buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, class_buf, 64)
    if class_buf.value in (
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "EyeGuardFocusRulerWindowClass",
        "EyeGuardDesktopOverlayClass",
    ):
        return False
    rc = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return False
    sw = user32.GetSystemMetrics(0)
    sh = user32.GetSystemMetrics(1)
    return rc.left <= 0 and rc.top <= 0 and rc.right >= sw and rc.bottom >= sh


class MagicWindowEngine:
    """
    Smart Application Rules & Window Focus Engine:
    - Magic Window: Per-window transparency dimming, Dark Titlebar DWM attribute, Always-on-Top pinning,
      and hover-activated Floating Magic Toolbar on window title bars.
    - Auto Dark & App Rules: Automatically applies Dark Invert, E-Ink Grayscale, Warm Reading,
      Color-Critical Bypass, or any clinical preset when specific applications are focused,
      plus automatic bypass when full-screen software/games are active.
    """

    def __init__(self, on_rule_trigger: Optional[Callable[[Optional[str]], None]] = None):
        self._lock = threading.RLock()
        self.enabled = False
        self.show_toolbar = False
        self.toolbar_color = "#7c3aed"
        self.placement = "center"       # "center" | "left" | "right"
        self.offset_dp = 0              # -200 to +200
        self.delay_sec = 0.50           # 0.10 to 2.00
        self.window_opacity = 85        # 25% to 100%

        self.disable_fullscreen = False
        self.auto_dark_enabled = False
        self.app_rules: Dict[str, str] = {
            "notepad.exe": "dark",
            "winword.exe": "warm",
            "acrord32.exe": "grayscale",
            "photoshop.exe": "exclude",
        }

        self.reading_theme_scope: str = "fullscreen"
        self.reading_theme_target_app: str = ""
        self.on_reading_app_focus: Optional[Callable[[bool], None]] = None
        self._reading_app_was_focused: Optional[bool] = None

        self._on_rule_trigger = on_rule_trigger
        self._active_rule_mode: Optional[str] = None
        self._modified_hwnds: Dict[int, int] = {}  # hwnd -> original exstyle
        self._pinned_hwnds: set = set()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._own_pid = os.getpid()

    def wake(self):
        self._wake_event.set()

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._wake_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="EyeGuard-MagicWindowThread",
                daemon=True,
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
            self._stop_event.set()
            self._wake_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.restore_all_windows()

    def configure(
        self,
        enabled: Optional[bool] = None,
        show_toolbar: Optional[bool] = None,
        toolbar_color: Optional[str] = None,
        placement: Optional[str] = None,
        offset_dp: Optional[int] = None,
        delay_sec: Optional[float] = None,
        window_opacity: Optional[int] = None,
        auto_dark_enabled: Optional[bool] = None,
        disable_fullscreen: Optional[bool] = None,
        app_rules: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if enabled is not None:
                self.enabled = bool(enabled)
                if not self.enabled:
                    self.restore_all_windows()
            if show_toolbar is not None:
                self.show_toolbar = bool(show_toolbar)
            if toolbar_color is not None:
                self.toolbar_color = str(toolbar_color)
            if placement in ("center", "left", "right"):
                self.placement = placement
            if offset_dp is not None:
                self.offset_dp = max(-200, min(200, int(offset_dp)))
            if delay_sec is not None:
                self.delay_sec = max(0.10, min(2.00, float(delay_sec)))
            if window_opacity is not None:
                self.window_opacity = max(25, min(100, int(window_opacity)))
            if auto_dark_enabled is not None:
                self.auto_dark_enabled = bool(auto_dark_enabled)
            if disable_fullscreen is not None:
                self.disable_fullscreen = bool(disable_fullscreen)
            if isinstance(app_rules, dict):
                self.app_rules = {
                    str(k).lower().strip(): str(v) for k, v in app_rules.items() if k
                }
            self._wake_event.set()
            return self.get_state()

    def set_app_rule(self, exe_name: str, mode: str) -> Dict[str, Any]:
        clean_exe = str(exe_name).strip().lower()
        if clean_exe and not clean_exe.endswith(".exe"):
            clean_exe += ".exe"
        with self._lock:
            if not mode or mode == "off":
                self.app_rules.pop(clean_exe, None)
            else:
                self.app_rules[clean_exe] = str(mode)
            return self.get_state()

    def dim_foreground_window(
        self,
        opacity_pct: Optional[int] = None,
        target_hwnd: Optional[int] = None,
        force: bool = False,
    ) -> bool:
        """Applies or toggles per-window alpha dimming & DWM dark titlebar on the target/foreground window."""
        if not self.enabled and not force:
            return False

        if target_hwnd:
            hwnd = int(target_hwnd)
            exe_name = get_process_name_for_hwnd(hwnd)
        else:
            hwnd, exe_name = get_foreground_process_name()

        if not hwnd or exe_name in ("explorer.exe", ""):
            return False
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == self._own_pid:
            return False

        with self._lock:
            # Toggle off if already modified and no explicit opacity was passed
            if hwnd in self._modified_hwnds and opacity_pct is None:
                orig_ex = self._modified_hwnds.pop(hwnd)
                if user32.IsWindow(hwnd):
                    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
                    if not (orig_ex & WS_EX_LAYERED):
                        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, orig_ex)
                return False

            pct = max(
                25,
                min(
                    100,
                    int(opacity_pct if opacity_pct is not None else self.window_opacity),
                ),
            )
            alpha_byte = int((pct / 100.0) * 255)

            orig_ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if hwnd not in self._modified_hwnds:
                self._modified_hwnds[hwnd] = orig_ex

            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, orig_ex | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, alpha_byte, LWA_ALPHA)

            dark_val = ctypes.c_int(1)
            try:
                dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    DWMWA_USE_IMMERSIVE_DARK_MODE,
                    ctypes.byref(dark_val),
                    ctypes.sizeof(dark_val),
                )
            except Exception:
                pass
        return True

    def toggle_pin_foreground_window(self, target_hwnd: Optional[int] = None) -> bool:
        """Toggles Always-on-Top state for the target or active window."""
        if target_hwnd:
            hwnd = int(target_hwnd)
        else:
            hwnd, _ = get_foreground_process_name()
        if not hwnd:
            return False
        with self._lock:
            if hwnd in self._pinned_hwnds:
                user32.SetWindowPos(
                    hwnd,
                    HWND_NOTOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
                self._pinned_hwnds.discard(hwnd)
                return False
            else:
                user32.SetWindowPos(
                    hwnd,
                    HWND_TOPMOST,
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                )
                self._pinned_hwnds.add(hwnd)
                return True

    def restore_all_windows(self):
        with self._lock:
            for hwnd, orig_ex in list(self._modified_hwnds.items()):
                if user32.IsWindow(hwnd):
                    user32.SetLayeredWindowAttributes(hwnd, 0, 255, LWA_ALPHA)
                    if not (orig_ex & WS_EX_LAYERED):
                        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, orig_ex)
            self._modified_hwnds.clear()
            for hwnd in list(self._pinned_hwnds):
                if user32.IsWindow(hwnd):
                    user32.SetWindowPos(
                        hwnd,
                        HWND_NOTOPMOST,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                    )
            self._pinned_hwnds.clear()

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "show_toolbar": self.show_toolbar,
                "toolbar_color": self.toolbar_color,
                "placement": self.placement,
                "offset_dp": self.offset_dp,
                "delay_sec": self.delay_sec,
                "window_opacity": self.window_opacity,
                "auto_dark_enabled": self.auto_dark_enabled,
                "disable_fullscreen": self.disable_fullscreen,
                "app_rules": dict(self.app_rules),
                "active_rule_mode": self._active_rule_mode,
                "modified_window_count": len(self._modified_hwnds),
            }

    def _worker_loop(self):
        while self._running and not self._stop_event.is_set():
            needs_watch = bool(
                self.disable_fullscreen
                or self.auto_dark_enabled
                or (self.reading_theme_scope == "app" and self.reading_theme_target_app)
            )
            if not needs_watch:
                if self._active_rule_mode is not None:
                    self._active_rule_mode = None
                    if self._on_rule_trigger:
                        try:
                            self._on_rule_trigger(None)
                        except Exception:
                            pass
                self._wake_event.clear()
                if self._stop_event.is_set() or not self._running:
                    break
                needs_watch_recheck = bool(
                    self.disable_fullscreen
                    or self.auto_dark_enabled
                    or (self.reading_theme_scope == "app" and self.reading_theme_target_app)
                )
                if not needs_watch_recheck:
                    self._wake_event.wait()
                continue

            try:
                hwnd, exe_name = get_foreground_process_name()
                pid = wintypes.DWORD(0)
                if hwnd:
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                if pid.value != self._own_pid:
                    desired_mode: Optional[str] = None
                    # 1. Check Fullscreen Bypass first if enabled
                    if self.disable_fullscreen and is_foreground_fullscreen(hwnd):
                        desired_mode = "exclude"
                    # 2. Check Custom App Rules if enabled
                    elif self.auto_dark_enabled and exe_name:
                        desired_mode = self.app_rules.get(exe_name)

                    if desired_mode != self._active_rule_mode:
                        self._active_rule_mode = desired_mode
                        if self._on_rule_trigger:
                            self._on_rule_trigger(desired_mode)

                    # 3. Check Dedicated Reading App Scope
                    if self.reading_theme_scope == "app" and self.reading_theme_target_app:
                        tgt = self.reading_theme_target_app.strip().lower()
                        is_target_focused = (exe_name.lower() == tgt)
                        if is_target_focused != self._reading_app_was_focused:
                            self._reading_app_was_focused = is_target_focused
                            if self.on_reading_app_focus:
                                try:
                                    self.on_reading_app_focus(is_target_focused)
                                except Exception:
                                    pass
            except Exception:
                pass
            self._wake_event.clear()
            if not self._stop_event.is_set() and self._running:
                self._wake_event.wait(timeout=0.35)
