"""
EyeGuard Startup Environment Diagnostics, Preflight Checks & Crash Logging
========================================================================
Ensures frozen GUI builds never fail silently on Windows 10 (including v1803
and older office PCs missing Edge WebView2 Runtime or .NET Framework 4.6.2+):
  1. Redirects None stdout/stderr and enables faulthandler native crash dumps.
  2. Logs unhandled exceptions + OS/runtime diagnostics to startup_error.log.
  3. Detects .NET Framework >= 4.6.2 and Microsoft Edge WebView2 Runtime
     (via registry or filesystem fallback) before starting GUI threads.
  4. Displays native Win32 MessageBoxW guidance instead of silent exit.
"""

import ctypes
from ctypes import wintypes
import faulthandler
import io
import os
import pathlib
import platform
import sys
import tempfile
import time
import traceback
import webbrowser
import winreg
from typing import Dict, Optional, Tuple

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int

_shell32.ShellExecuteW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_int,
]
_shell32.ShellExecuteW.restype = wintypes.HINSTANCE

MB_OK = 0x00000000
MB_YESNO = 0x00000004
MB_ICONERROR = 0x00000010
MB_ICONWARNING = 0x00000030
MB_SETFOREGROUND = 0x00010000
IDYES = 6

WEBVIEW2_DOWNLOAD_URL = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
DOTNET48_DOWNLOAD_URL = "https://dotnet.microsoft.com/en-us/download/dotnet-framework/net48"

WEBVIEW2_CLIENT_GUIDS = (
    "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",  # Evergreen WebView2 Runtime
    "{2CD8A007-E189-409D-A2C8-9AF4EF3C72AA}",  # Beta
    "{0D50BFEC-CD6A-4F9A-964C-C7416E3ACB10}",  # Dev
    "{65C35B14-6C1D-4122-AC46-7148CC9D6497}",  # Canary
)

_MIN_WEBVIEW2_VER = (86, 0, 622, 0)
_MIN_DOTNET_RELEASE = 394802  # .NET Framework 4.6.2

_DOTNET_LABELS = (
    (533320, "4.8.1"),
    (528040, "4.8"),
    (461808, "4.7.2"),
    (461308, "4.7.1"),
    (460798, "4.7"),
    (394802, "4.6.2"),
)

_FAULT_LOG_HANDLE = None


def _is_unit_test_env() -> bool:
    return (
        (not getattr(sys, "frozen", False))
        and ("unittest" in sys.modules)
        and any("test" in str(a).lower() or "unittest" in str(a).lower() for a in sys.argv)
    )


def get_logs_dir() -> pathlib.Path:
    """Returns a writable directory for startup and crash diagnostics."""
    if _is_unit_test_env():
        p = pathlib.Path(tempfile.gettempdir()) / "EyeGuard_TestConfig" / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p

    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(pathlib.Path(appdata) / "EyeGuard" / "logs")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(pathlib.Path(local_appdata) / "EyeGuardWebview" / "logs")
    candidates.append(pathlib.Path(tempfile.gettempdir()) / "EyeGuard_logs")

    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            return cand
        except OSError:
            continue
    return pathlib.Path(".")


def install_stdio_fallback() -> None:
    """Replaces None stdout/stderr in console-disabled Windows builds."""
    for attr in ("stdout", "stderr"):
        if getattr(sys, attr, None) is None:
            try:
                setattr(sys, attr, io.StringIO())
            except Exception:
                pass


def enable_native_fault_handler(log_dir: Optional[pathlib.Path] = None) -> Optional[pathlib.Path]:
    """Enables faulthandler logging for native crashes (SIGSEGV / SIGILL / access violations)."""
    global _FAULT_LOG_HANDLE
    try:
        target_dir = pathlib.Path(log_dir) if log_dir else get_logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        log_path = target_dir / "native_crash.log"
        if log_path.exists() and log_path.stat().st_size > 262144:
            log_path.unlink(missing_ok=True)
        _FAULT_LOG_HANDLE = open(log_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=_FAULT_LOG_HANDLE, all_threads=True)
        return log_path
    except Exception:
        return None


def parse_version_tuple(ver_str: str) -> Tuple[int, ...]:
    """Parses dotted numeric version strings safely into comparable integer tuples."""
    parts = []
    for raw in str(ver_str or "").strip().split("."):
        digits = ""
        for ch in raw:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
        else:
            break
    return tuple(parts)


def is_version_at_least(found_ver: str, minimum_tuple: Tuple[int, ...]) -> bool:
    parsed = parse_version_tuple(found_ver)
    if not parsed:
        return False
    return parsed >= minimum_tuple


def detect_dotnet_framework() -> Tuple[bool, Optional[int], str]:
    """Checks HKLM for .NET Framework 4.6.2+ required by pythonnet / pywebview."""
    key = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        )
        release_val, _ = winreg.QueryValueEx(key, "Release")
        rel = int(release_val)
        label = "<4.6.2"
        for threshold, name in _DOTNET_LABELS:
            if rel >= threshold:
                label = name
                break
        return rel >= _MIN_DOTNET_RELEASE, rel, label
    except OSError:
        return False, None, "missing"
    except Exception:
        return False, None, "unknown"
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass


def _read_reg_pv(hive: int, subkey: str) -> Optional[str]:
    key = None
    try:
        key = winreg.OpenKey(hive, subkey)
        val, _ = winreg.QueryValueEx(key, "pv")
        return str(val).strip() if val is not None else None
    except OSError:
        return None
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass


def detect_webview2_runtime() -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Detects Microsoft Edge WebView2 Runtime from registry or disk fallback.
    Returns (found, version_str, custom_folder_or_None).
    """
    for guid in WEBVIEW2_CLIENT_GUIDS:
        reg_candidates = (
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
        )
        for hive, subkey in reg_candidates:
            pv = _read_reg_pv(hive, subkey)
            if pv and is_version_at_least(pv, _MIN_WEBVIEW2_VER):
                return True, pv, None

    # Filesystem fallback for portable/unregistered or Office-bundled WebView2 installations
    base_roots = []
    for env_var in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(env_var)
        if root:
            base_roots.append(pathlib.Path(root) / "Microsoft" / "EdgeWebView" / "Application")

    best: Optional[Tuple[Tuple[int, ...], str, str]] = None
    for app_root in base_roots:
        if not app_root.is_dir():
            continue
        try:
            for sub in app_root.iterdir():
                if not sub.is_dir():
                    continue
                if (sub / "msedgewebview2.exe").is_file() and is_version_at_least(sub.name, _MIN_WEBVIEW2_VER):
                    vt = parse_version_tuple(sub.name)
                    if best is None or vt > best[0]:
                        best = (vt, sub.name, str(sub))
        except OSError:
            continue

    if best is not None:
        return True, best[1], best[2]

    return False, None, None


def collect_startup_diagnostics(app_version: str = "4.0.1") -> str:
    """Collects non-sensitive OS, Python, .NET, and WebView2 diagnostic context."""
    try:
        wv = sys.getwindowsversion()
        win_str = f"{wv.major}.{wv.minor}.{wv.build} (sp={wv.service_pack_major})"
    except Exception:
        win_str = platform.platform()

    dn_ok, dn_rel, dn_lbl = detect_dotnet_framework()
    wv2_ok, wv2_ver, wv2_path = detect_webview2_runtime()

    lines = [
        f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"EyeGuard: {app_version} | frozen={getattr(sys, 'frozen', False)}",
        f"Executable: {sys.executable}",
        f"Python: {sys.version.split()[0]} ({platform.architecture()[0]} / {platform.machine()})",
        f"Windows: {win_str}",
        f".NET 4.6.2+: {dn_ok} (release={dn_rel}, version={dn_lbl})",
        f"WebView2 Runtime: {wv2_ok} (version={wv2_ver}, path={wv2_path or 'registry'})",
    ]
    return "\n".join(lines)


def write_startup_log(
    message: str,
    exc: Optional[BaseException] = None,
    app_version: str = "4.0.1",
    log_dir: Optional[pathlib.Path] = None,
) -> Optional[pathlib.Path]:
    """Appends startup error details and environment diagnostics to startup_error.log."""
    try:
        target_dir = pathlib.Path(log_dir) if log_dir else get_logs_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        log_path = target_dir / "startup_error.log"
        diag = collect_startup_diagnostics(app_version=app_version)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n========================================\n")
            f.write(diag + "\n")
            f.write(f"Message: {message}\n")
            if exc is not None:
                f.write("Traceback:\n")
                traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return log_path
    except Exception:
        return None


def show_native_error_box(title: str, message: str, flags: int = MB_OK | MB_ICONERROR) -> int:
    """Shows a native Win32 MessageBoxW that works even when .NET/WebView2 cannot load."""
    try:
        return int(_user32.MessageBoxW(None, str(message), str(title), int(flags | MB_SETFOREGROUND)))
    except Exception:
        return 0


def open_external_url(url: str) -> None:
    try:
        rc = int(_shell32.ShellExecuteW(None, "open", str(url), None, None, 1))
        if rc <= 32:
            webbrowser.open(str(url))
    except Exception:
        try:
            webbrowser.open(str(url))
        except Exception:
            pass


def preflight_gui_runtime(interactive: bool = True, app_version: str = "4.0.1") -> Tuple[bool, Optional[str], Dict]:
    """
    Verifies .NET Framework 4.6.2+ and Edge WebView2 Runtime before launching the UI.
    Returns (can_launch, custom_webview2_path, diagnostics_dict).
    """
    dn_ok, dn_rel, dn_lbl = detect_dotnet_framework()
    wv2_ok, wv2_ver, wv2_path = detect_webview2_runtime()
    info = {
        "dotnet_ok": dn_ok,
        "dotnet_release": dn_rel,
        "dotnet_label": dn_lbl,
        "webview2_ok": wv2_ok,
        "webview2_version": wv2_ver,
        "webview2_path": wv2_path,
    }

    if not dn_ok:
        log_path = write_startup_log(
            ".NET Framework 4.6.2 or newer was not detected in registry.",
            app_version=app_version,
        )
        if interactive:
            msg = (
                "EyeGuard could not start because Microsoft .NET Framework 4.6.2 or newer "
                "(recommended: .NET Framework 4.8) was not found on this PC.\n\n"
                "Would you like to open Microsoft's official .NET Framework 4.8 download page?"
            )
            if log_path:
                msg += f"\n\nDiagnostics log:\n{log_path}"
            if show_native_error_box("EyeGuard — Missing .NET Framework", msg, MB_YESNO | MB_ICONERROR) == IDYES:
                open_external_url(DOTNET48_DOWNLOAD_URL)
        return False, None, info

    if not wv2_ok:
        log_path = write_startup_log(
            "Microsoft Edge WebView2 Runtime (>= 86.0.622.0) was not detected.",
            app_version=app_version,
        )
        if interactive:
            msg = (
                "EyeGuard requires Microsoft Edge WebView2 Runtime to display its interface.\n\n"
                "Older Windows 10 builds (such as version 1803) did not include WebView2 Runtime by default.\n\n"
                "Open Microsoft's official WebView2 Runtime download page now?"
            )
            if log_path:
                msg += f"\n\nDiagnostics log:\n{log_path}"
            if show_native_error_box("EyeGuard — WebView2 Runtime Required", msg, MB_YESNO | MB_ICONWARNING) == IDYES:
                open_external_url(WEBVIEW2_DOWNLOAD_URL)
        return False, None, info

    return True, wv2_path, info
