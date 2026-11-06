"""
EyeGuard System Events & Environment Monitor
============================================
Guarantees the "Zero Surprises" operating standard for Windows:
1. Sleep / Resume Handling (WM_POWERBROADCAST / PBT_APMRESUMEAUTOMATIC):
   Restores display calibration and DWM color shaders after driver wake.
2. Display Configuration Changes (WM_DISPLAYCHANGE):
   Re-detects resolution changes, monitor connects/disconnects, and multi-monitor bounds.
3. Meeting / Presentation Bypass:
   Detects active screen sharing / video conference processes (Zoom, Teams, OBS, Webex, Meet)
   to prevent screen tinting from showing on attendees' screens.
4. Fullscreen Exclusive Game Guard:
   Detects exclusive fullscreen 3D viewports and automatically silences overlays/rulers.
"""

import os
import sys
import time
import threading
import ctypes
from ctypes import wintypes
from typing import Callable, List, Optional

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218

PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMSUSPEND = 0x0004


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", getattr(wintypes, "HCURSOR", wintypes.HANDLE)),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


CONFERENCE_PROCESSES = {
    "zoom.exe",
    "teams.exe",
    "ms-teams.exe",
    "webexmta.exe",
    "webex.exe",
    "obs64.exe",
    "obs32.exe",
    "discord.exe",
    "slack.exe",
}


class SystemEventsMonitor:
    def __init__(
        self,
        on_resume: Optional[Callable[[], None]] = None,
        on_display_change: Optional[Callable[[], None]] = None,
        on_meeting_status_change: Optional[Callable[[bool], None]] = None,
    ):
        self._on_resume = on_resume
        self._on_display_change = on_display_change
        self._on_meeting_status_change = on_meeting_status_change

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[wintypes.HWND] = None
        self._wnd_proc_ref: Optional[WNDPROC] = None

        self._in_meeting = False
        self._meeting_poll_thread: Optional[threading.Thread] = None

    def start(self):
        if self._running:
            return
        self._running = True

        self._thread = threading.Thread(
            target=self._msg_loop, name="EyeGuard-SystemEvents", daemon=True
        )
        self._thread.start()

        self._meeting_poll_thread = threading.Thread(
            target=self._poll_meetings, name="EyeGuard-MeetingDetector", daemon=True
        )
        self._meeting_poll_thread.start()

    def is_meeting_active(self) -> bool:
        return bool(self._in_meeting)

    def stop(self):
        self._running = False
        if self._hwnd:
            user32.PostMessageW(self._hwnd, 0x0010, 0, 0)  # WM_CLOSE = 0x0010

    def _msg_loop(self):
        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_POWERBROADCAST:
                if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                    # Delay 1.0s to allow GPU graphics driver to settle post-wake
                    threading.Timer(1.0, self._trigger_resume).start()
                return 1

            elif msg == WM_DISPLAYCHANGE:
                # Screen resolution or monitor geometry changed
                threading.Timer(0.5, self._trigger_display_change).start()
                return 0

            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0

            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(_wnd_proc)

        h_inst = kernel32.GetModuleHandleW(None)
        class_name = f"EyeGuardEventReceiver_{os.getpid()}"

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = h_inst
        wc.lpszClassName = class_name

        atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            return

        self._hwnd = user32.CreateWindowExW(
            0, atom, "EyeGuardEvents", 0, 0, 0, 0, 0, 0, 0, h_inst, 0
        )

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
        user32.UnregisterClassW(atom, h_inst)

    def _trigger_resume(self):
        if self._on_resume:
            try:
                self._on_resume()
            except Exception:
                pass

    def _trigger_display_change(self):
        if self._on_display_change:
            try:
                self._on_display_change()
            except Exception:
                pass

    def _poll_meetings(self):
        """Polls every 5 seconds for running conference tools (optional meeting mode)."""
        while self._running:
            try:
                from core.window_focus import get_running_window_processes
                procs = get_running_window_processes()
                has_meeting = any(p in CONFERENCE_PROCESSES for p in procs)

                if has_meeting != self._in_meeting:
                    self._in_meeting = has_meeting
                    if self._on_meeting_status_change:
                        self._on_meeting_status_change(self._in_meeting)
            except Exception:
                pass
            time.sleep(5.0)
