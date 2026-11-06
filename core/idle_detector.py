import ctypes
from ctypes import wintypes

from typing import Optional, Callable

class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD)
    ]

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Configure 64-bit return type for GetTickCount64 to eliminate signed 32-bit overflow
try:
    kernel32.GetTickCount64.restype = ctypes.c_ulonglong
    _HAS_TICK64 = True
except AttributeError:
    kernel32.GetTickCount.restype = wintypes.DWORD
    _HAS_TICK64 = False

class IdleDetector:
    """
    Monitors user input activity across the OS using Win32 GetLastInputInfo.
    Uses GetTickCount64 and 32-bit unsigned rollover arithmetic to guarantee 100%
    accuracy across system uptimes > 24.85 days, 49.7 days, and beyond.
    """
    def __init__(self,
                 idle_threshold_seconds: int = 120,
                 tick_getter: Optional[Callable[[], int]] = None,
                 last_input_getter: Optional[Callable[[], int]] = None):
        self.threshold = idle_threshold_seconds
        self._lii = LASTINPUTINFO()
        self._lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        self._custom_tick_getter = tick_getter
        self._custom_last_input_getter = last_input_getter

    def _get_system_tick(self) -> int:
        if self._custom_tick_getter is not None:
            return int(self._custom_tick_getter())
        if _HAS_TICK64:
            return int(kernel32.GetTickCount64())
        return int(kernel32.GetTickCount())

    def get_idle_seconds(self) -> float:
        """
        Returns seconds since last user input across the entire OS.
        Resilient against 24.85-day signed overflow and 49.7-day 32-bit wrap.
        """
        if self._custom_last_input_getter is not None:
            last_dw_time = int(self._custom_last_input_getter()) & 0xFFFFFFFF
            cur_tick32 = self._get_system_tick() & 0xFFFFFFFF
            idle_ms = (cur_tick32 - last_dw_time) & 0xFFFFFFFF
            return float(idle_ms) / 1000.0

        if user32.GetLastInputInfo(ctypes.byref(self._lii)):
            cur_tick32 = self._get_system_tick() & 0xFFFFFFFF
            # dwTime is 32-bit unsigned (wintypes.DWORD). Compute modular difference:
            idle_ms = (cur_tick32 - self._lii.dwTime) & 0xFFFFFFFF
            return float(idle_ms) / 1000.0
        return 0.0

    def is_idle(self, threshold_seconds: Optional[int] = None) -> bool:
        """
        Returns True if the system has been idle for >= threshold seconds.
        """
        th = self.threshold if threshold_seconds is None else threshold_seconds
        return self.get_idle_seconds() >= th

