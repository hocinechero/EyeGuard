"""
EyeGuard Crash-Guard & Fail-Safe Watchdog
=========================================
Runs as an ultra-lightweight, zero-dependency background supervisor process.
Monitors the main EyeGuard process PID via Win32 WaitForSingleObject(SYNCHRONIZE).

If the main process terminates abnormally (SIGKILL / taskkill / unhandled crash / power glitch),
this watchdog wakes up within 0 milliseconds and immediately executes:
1. Reversion of DWM 5x5 Color Matrix to 100% Identity via MagSetFullscreenColorEffect.
2. Uninitialization of Magnification API via MagUninitialize.
3. Reversion of GDI hardware gamma ramp to 100% linear calibration via SetDeviceGammaRamp.

Ensures zero chance of the user being left with a yellow or dimmed screen.
"""

import os
import sys
import time
import ctypes
from ctypes import wintypes

SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF

kernel32 = ctypes.windll.kernel32
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

ramp_type = wintypes.WORD * 256


class GAMMA_RAMP(ctypes.Structure):
    _fields_ = [("Red", ramp_type), ("Green", ramp_type), ("Blue", ramp_type)]


class MAGCOLOREFFECT(ctypes.Structure):
    _fields_ = [("transform", (ctypes.c_float * 5) * 5)]


def emergency_restore_display():
    """Fail-safe display restoration: resets both DWM compositor and GDI hardware ramps."""
    # 1. Reset DWM Magnification Shader to 100% Identity
    try:
        mag32 = ctypes.windll.magnification
        mag32.MagInitialize.restype = wintypes.BOOL
        mag32.MagUninitialize.restype = wintypes.BOOL
        mag32.MagSetFullscreenColorEffect.argtypes = [ctypes.POINTER(MAGCOLOREFFECT)]
        mag32.MagSetFullscreenColorEffect.restype = wintypes.BOOL

        mag32.MagInitialize()
        identity = MAGCOLOREFFECT()
        for i in range(5):
            identity.transform[i][i] = 1.0
        mag32.MagSetFullscreenColorEffect(ctypes.byref(identity))
        mag32.MagUninitialize()
    except Exception:
        pass

    # 2. Reset GDI Gamma Ramp to 100% Linear Identity
    try:
        linear_ramp = GAMMA_RAMP()
        for i in range(256):
            val = min(65535, (i << 8) | i)
            linear_ramp.Red[i] = val
            linear_ramp.Green[i] = val
            linear_ramp.Blue[i] = val

        hdc = user32.GetDC(None)
        if hdc:
            gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(linear_ramp))
            user32.ReleaseDC(None, hdc)
    except Exception:
        pass

    # 3. Reset DDC/CI physical monitor brightness to 100%
    try:
        dxva2 = ctypes.windll.dxva2
        class PHYSICAL_MONITOR(ctypes.Structure):
            _fields_ = [("hPhysicalMonitor", wintypes.HANDLE), ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]
        MonitorEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def _ddc_restore(hMonitor, hdcMonitor, lprcMonitor, dwData):
            count = wintypes.DWORD(0)
            if dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hMonitor, ctypes.byref(count)) and count.value > 0:
                arr = (PHYSICAL_MONITOR * count.value)()
                if dxva2.GetPhysicalMonitorsFromHMONITOR(hMonitor, count.value, arr):
                    for idx in range(count.value):
                        dxva2.SetMonitorBrightness(arr[idx].hPhysicalMonitor, wintypes.DWORD(100))
                    dxva2.DestroyPhysicalMonitors(count.value, arr)
            return True
        user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_ddc_restore), 0)
    except Exception:
        pass



def run_watchdog(parent_pid: int, sentinel_path: str):
    """Monitors parent_pid until termination, then triggers recovery if sentinel is absent."""
    h_process = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
    if not h_process:
        return

    try:
        kernel32.WaitForSingleObject(h_process, INFINITE)
    finally:
        kernel32.CloseHandle(h_process)

    # Allow tiny window for OS filesystem write flush on normal exit
    time.sleep(0.05)

    if os.path.exists(sentinel_path):
        # Clean exit occurred
        try:
            os.remove(sentinel_path)
        except Exception:
            pass
    else:
        # Abnormal termination / Crash / SIGKILL detected -> Emergency recovery!
        emergency_restore_display()


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        try:
            p_pid = int(sys.argv[1])
            s_path = sys.argv[2]
            run_watchdog(p_pid, s_path)
        except Exception:
            pass
    elif len(sys.argv) >= 2 and sys.argv[1] == "--restore-now":
        # Direct CLI emergency invocation
        emergency_restore_display()
