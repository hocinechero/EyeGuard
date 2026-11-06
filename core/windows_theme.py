"""
Windows System & Application Theme Controller
==============================================
Synchronizes Windows 10/11 system and application theme (Dark / Light)
with the circadian solar schedule (Daytime: Light, Sunset/Bedtime: Dark).
Updates registry keys and broadcasts WM_SETTINGCHANGE for instant shell update.
"""

import sys
import ctypes
from ctypes import wintypes
import winreg

HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002

user32 = ctypes.windll.user32


def set_windows_dark_mode(enable_dark: bool) -> bool:
    """
    Sets Windows 10/11 system and application theme (0 = Dark, 1 = Light).
    Broadcasts WM_SETTINGCHANGE to notify File Explorer, Settings, and running apps.
    """
    if sys.platform != "win32":
        return False
    val = 0 if enable_dark else 1
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "AppsUseLightTheme", 0, winreg.REG_DWORD, val)
            winreg.SetValueEx(key, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)

        res = ctypes.c_ulong()
        user32.SendMessageTimeoutW(
            HWND_BROADCAST,
            WM_SETTINGCHANGE,
            0,
            "ImmersiveColorSet",
            SMTO_ABORTIFHUNG,
            500,
            ctypes.byref(res),
        )
        return True
    except Exception:
        return False


def get_windows_dark_mode() -> bool:
    """Returns True if Windows application theme is currently Dark mode (AppsUseLightTheme == 0)."""
    if sys.platform != "win32":
        return False
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return val == 0
    except Exception:
        return False
