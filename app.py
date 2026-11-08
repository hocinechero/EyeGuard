from typing import Optional, Dict, Any, List, Tuple
import os
import sys

# Direct Chromium flags for WebView2: forces razor-sharp grayscale font rendering without color fringing
os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = (
    "--disable-lcd-text "
    "--force-color-profile=srgb "
    "--disable-font-subpixel-positioning "
    "--enable-font-antialiasing"
)

import threading
import time
import traceback
import winsound
import winreg
from pathlib import Path
import webview

# Ensure base directory is in sys.path
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from config import ConfigManager, DEFAULT_CONFIG, _is_running_unit_tests
from core.gamma_engine import EyeGuardGammaEngine, kelvin_to_rgb_extended
from core.idle_detector import IdleDetector
from core.timer_engine import BreakTimerEngine, TimerState
from core.solar_engine import SolarEngine
from core.hotkey_engine import HotkeyEngine
from core.win32_ruler import Win32FocusRuler
from core.window_focus import (
    MagicWindowEngine,
    is_foreground_fullscreen,
    get_running_window_processes,
)
from core.desktop_overlays import DesktopOverlayManager, BreakAudioPlayer
from ui.tray_manager import TrayManager
import ctypes
from ctypes import wintypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32

HOTKEY_METADATA = [
    {
        "id": "brightness_up",
        "name_ar": "Increase Brightness (+5%)",
        "name_en": "Increase Brightness (+5%)",
        "desc_ar": "Increase display software brightness by 5%",
        "desc_en": "Increase display software brightness by 5%",
        "default": "Ctrl + Alt + Up",
    },
    {
        "id": "brightness_down",
        "name_ar": "Decrease Brightness (-5%)",
        "name_en": "Decrease Brightness (-5%)",
        "desc_ar": "Decrease display software brightness by 5%",
        "desc_en": "Decrease display software brightness by 5%",
        "default": "Ctrl + Alt + Down",
    },
    {
        "id": "kelvin_warmer",
        "name_ar": "Warmer Kelvin (+250K)",
        "name_en": "Warmer Kelvin (+250K)",
        "desc_ar": "Reduce blue light emission for eye comfort",
        "desc_en": "Reduce blue light emission for eye comfort",
        "default": "Ctrl + Alt + Right",
    },
    {
        "id": "kelvin_cooler",
        "name_ar": "Cooler Kelvin (-250K)",
        "name_en": "Cooler Kelvin (-250K)",
        "desc_ar": "Increase color temperature for daytime work",
        "desc_en": "Increase color temperature for daytime work",
        "default": "Ctrl + Alt + Left",
    },
    {
        "id": "reset_natural",
        "name_ar": "Universal Screen Restore (6500K / 100%)",
        "name_en": "Universal Screen Restore (6500K / 100%)",
        "desc_ar": "Instantly restore daylight calibration and clear any active overlays or spotlight masks",
        "desc_en": "Instantly restore daylight calibration and clear any active overlays or spotlight masks",
        "default": "Ctrl + Alt + D",
    },
    {
        "id": "toggle_ruler",
        "name_ar": "Reading Focus Ruler",
        "name_en": "Reading Focus Ruler",
        "desc_ar": "Toggle horizontal reading focus slit overlay",
        "desc_en": "Toggle horizontal reading focus slit overlay",
        "default": "Ctrl + Alt + F",
    },
    {
        "id": "toggle_spotlight",
        "name_ar": "Spotlight Focus Mode",
        "name_en": "Spotlight Focus Mode",
        "desc_ar": "Dim all background windows and highlight active window",
        "desc_en": "Dim all background windows and highlight active window",
        "default": "Ctrl + Alt + S",
    },
    {
        "id": "magic_dark",
        "name_ar": "Magic Window Dim / Dark",
        "name_en": "Magic Window Dim / Dark",
        "desc_ar": "Apply per-window dimming and dark titlebar to active window",
        "desc_en": "Apply per-window dimming and dark titlebar to active window",
        "default": "Ctrl + Alt + M",
    },
    {
        "id": "toggle_gray",
        "name_ar": "Toggle E-Ink Grayscale",
        "name_en": "Toggle E-Ink Grayscale",
        "desc_ar": "Desaturate screen into an electronic paper display",
        "desc_en": "Desaturate screen into an electronic paper display",
        "default": "Ctrl + Alt + G",
    },
    {
        "id": "toggle_invert",
        "name_ar": "Toggle Invert Colors",
        "name_en": "Toggle Invert Colors",
        "desc_ar": "Invert display luminance for dark reading",
        "desc_en": "Invert display luminance for dark reading",
        "default": "Ctrl + Alt + I",
    },
    {
        "id": "toggle_timer",
        "name_ar": "Start / Pause Break Timer",
        "name_en": "Start / Pause Break Timer",
        "desc_ar": "Toggle pause or resume of active work timer",
        "desc_en": "Toggle pause or resume of active work timer",
        "default": "Ctrl + Alt + P",
    },
    {
        "id": "take_break",
        "name_ar": "Take Break Now",
        "name_en": "Take Break Now",
        "desc_ar": "Trigger an immediate eye relaxation session",
        "desc_en": "Trigger an immediate eye relaxation session",
        "default": "Ctrl + Alt + B",
    },
]


def force_foreground_window(hwnd: int):
    """
    Guarantees window is brought to absolute foreground on top of all other windows,
    bypassing Windows Foreground Lock Timeout via AttachThreadInput.
    """
    if not hwnd or not user32.IsWindow(hwnd):
        return
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)  # SW_SHOW

        fg_hwnd = user32.GetForegroundWindow()
        if fg_hwnd and fg_hwnd != hwnd:
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
            cur_tid = kernel32.GetCurrentThreadId()
            if fg_tid and cur_tid and fg_tid != cur_tid:
                user32.AttachThreadInput(cur_tid, fg_tid, True)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(cur_tid, fg_tid, False)
            else:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
        else:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)

        # Multi-monitor bounds
        vx = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
        vy = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        vw = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
        vh = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if vw <= 0 or vh <= 0:
            vx, vy = 0, 0
            vw = user32.GetSystemMetrics(0)
            vh = user32.GetSystemMetrics(1)

        user32.SetWindowPos(
            hwnd,
            ctypes.c_void_p(-1),  # HWND_TOPMOST
            vx, vy, vw, vh,
            0x0020 | 0x0040  # SWP_FRAMECHANGED | SWP_SHOWWINDOW
        )
        user32.UpdateWindow(hwnd)
    except Exception:
        pass



class EyeGuardApp:
    VERSION = "2.1.1"

    def __init__(self):
        # 1. Config & Dynamic LocalAppData Storage
        self.config_mgr = ConfigManager()
        local_app_data = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        self.data_dir = os.path.join(local_app_data, "EyeGuardWebview")
        os.makedirs(self.data_dir, exist_ok=True)
        self._install_crash_reporter()

        # 2. Audio & Native Desktop Overlays
        self.audio_player = BreakAudioPlayer(str(self.config_mgr.config_dir))
        self.desktop_overlays = DesktopOverlayManager(
            on_toolbar_action=self._on_magic_toolbar_action,
            on_open_main_window=self.show_window,
        )

        # 3. Display & Solar Engines
        self.gamma_engine = EyeGuardGammaEngine()
        from core.system_events import SystemEventsMonitor
        self.system_events = SystemEventsMonitor(on_resume=self._on_system_resume, on_display_change=self._on_display_change, on_meeting_status_change=self._on_meeting_status_change)
        cir_cfg = self.config_mgr.get("circadian", default={}) or {}
        self.solar_engine = SolarEngine(
            sunrise=cir_cfg.get("sunrise", "06:30"),
            sunset=cir_cfg.get("sunset", "18:30"),
            bedtime=cir_cfg.get("bedtime", "22:30"),
            day_kelvin=cir_cfg.get("day_kelvin", 6500),
            night_kelvin=cir_cfg.get("night_kelvin", 3400),
            bedtime_kelvin=cir_cfg.get("bedtime_kelvin", 1900),
            day_brightness=cir_cfg.get("day_brightness", 100),
            night_brightness=cir_cfg.get("night_brightness", 80),
            bedtime_brightness=cir_cfg.get("bedtime_brightness", 55),
        )
        self.hotkey_engine = HotkeyEngine()

        fr_cfg = self.config_mgr.get("focus_ruler", default={}) or {}
        self.focus_ruler = Win32FocusRuler(
            slit_height=fr_cfg.get("slit_height", 300),
            opacity=fr_cfg.get("opacity", 0.50),
            color_hex=fr_cfg.get("color_hex", "#000000"),
        )
        self.focus_ruler.configure_spotlight(
            opacity=fr_cfg.get("spotlight_opacity", 0.60),
            color_hex=fr_cfg.get("spotlight_color", "#000000"),
            include_taskbar=fr_cfg.get("include_taskbar", False),
            enable_anim=fr_cfg.get("enable_anim", True),
            anim_ms=fr_cfg.get("anim_ms", 200),
            current_monitor_only=fr_cfg.get("current_monitor_only", True),
        )

        mx_cfg = self.config_mgr.get("magic_x", default={}) or {}
        cr_cfg = self.config_mgr.get("custom_rules", default={}) or {}
        self.magic_engine = MagicWindowEngine(on_rule_trigger=self._on_app_rule_trigger)
        self.magic_engine.configure(
            enabled=mx_cfg.get("enabled", False),
            show_toolbar=mx_cfg.get("show_toolbar", False),
            toolbar_color=mx_cfg.get("toolbar_color", "#7c3aed"),
            placement=mx_cfg.get("placement", "center"),
            offset_dp=mx_cfg.get("offset_dp", 0),
            delay_sec=mx_cfg.get("delay_sec", 0.50),
            window_opacity=mx_cfg.get("window_opacity", 85),
            auto_dark_enabled=mx_cfg.get("auto_dark_enabled", False)
            or bool(cr_cfg.get("enable_custom_rules", True)),
            disable_fullscreen=bool(cr_cfg.get("disable_fullscreen", False)),
            app_rules=mx_cfg.get(
                "app_rules",
                {
                    "notepad.exe": "dark",
                    "winword.exe": "warm",
                    "acrord32.exe": "grayscale",
                    "photoshop.exe": "exclude",
                },
            ),
        )
        self._sync_custom_rules_to_engine()
        self._sync_magic_toolbar_overlay()

        scr_cfg = self.config_mgr.get("screen", default={}) or {}

        # Reading Themes & Scope (Full-Screen vs Dedicated Reader Application)
        self.active_reading_theme: str = str(scr_cfg.get("reading_theme", "normal")).lower()
        self.reading_theme_scope: str = str(scr_cfg.get("reading_theme_scope", "fullscreen")).lower()
        self.reading_theme_target_app: str = str(scr_cfg.get("reading_theme_target_app", "")).lower()
        if self.reading_theme_target_app == "sumatrapdf.exe":
            self.reading_theme_target_app = ""
            self.config_mgr.set("screen", "reading_theme_target_app", "")
        self.custom_bg_hex: str = str(scr_cfg.get("custom_bg_hex", "#FAF0E6"))
        self.custom_txt_hex: str = str(scr_cfg.get("custom_txt_hex", "#2D3748"))

        self.magic_engine.reading_theme_scope = self.reading_theme_scope
        self.magic_engine.reading_theme_target_app = self.reading_theme_target_app
        self.magic_engine.on_reading_app_focus = self._on_reading_app_focus
        self.magic_engine.start()
        scr_mode = str(scr_cfg.get("mode", "Health"))
        raw_k = scr_cfg.get("kelvin")
        if raw_k is None or int(raw_k) <= 0 or scr_mode.lower() in ("natural", "standard"):
            self.current_k = 6500
        else:
            try:
                self.current_k = max(1000, min(6500, int(raw_k)))
            except Exception:
                self.current_k = 6500

        raw_b = scr_cfg.get("brightness")
        if raw_b is None or int(raw_b) <= 0:
            self.current_b = 100
        else:
            self.current_b = int(raw_b)

        self.current_sat = int(scr_cfg.get("saturation", 100))
        self.current_hw_b = int(scr_cfg.get("hardware_brightness", 100))
        self.is_gray = bool(scr_cfg.get("grayscale", False))
        self.is_invert = bool(scr_cfg.get("invert", False))
        self.color_filter_mode = str(scr_cfg.get("color_filter_mode", "day"))
        if self.color_filter_mode in ("normal", "standard"):
            self.color_filter_mode = "day" 

        self.gamma_engine.saturation = max(0.0, min(1.0, self.current_sat / 100.0))
        self.gamma_engine.green_background = bool(scr_cfg.get("keep_background_green", False))
        self.gamma_engine.smooth_transition = bool(scr_cfg.get("smooth_transition", True))
        self.gamma_engine.software_filter_mode = bool(scr_cfg.get("software_filter_mode", False))
        self.gamma_engine.standard_calibration_mode = bool(scr_cfg.get("standard_calibration_mode", True))
        self.gamma_engine.software_mouse_cursor = bool(scr_cfg.get("software_mouse_cursor", False))

        self.circadian_active = bool(cir_cfg.get("enabled", False))
        self._filter_disabled_until_ts: Optional[float] = None
        self.selected_monitor = -1  # -1 = All Monitors
        try:
            self._monitors_list = self.gamma_engine.get_monitors()
            self._monitor_count = max(1, len(self._monitors_list))
        except Exception:
            self._monitors_list = []
            self._monitor_count = 1

        self._rule_override_active = False

        # 4. Timer Engine
        tm_cfg = self.config_mgr.get("timer", default={}) or {}
        self.timer_engine = BreakTimerEngine(
            work_duration_sec=int(tm_cfg.get("work_minutes", 45)) * 60,
            short_break_sec=int(tm_cfg.get("short_break_minutes", 5)) * 60,
            long_break_sec=int(tm_cfg.get("long_break_minutes", 15)) * 60,
            cycles_before_long=int(tm_cfg.get("long_break_interval", 0)),
            enable_20_20=bool(tm_cfg.get("enable_20_20_rule", True)),
            enable_pomodoro=bool(tm_cfg.get("enable_pomodoro", True)),
            auto_pause=bool(tm_cfg.get("auto_pause_on_idle", True)),
            idle_threshold_sec=int(tm_cfg.get("idle_threshold_seconds", 180)),
            auto_start=bool(tm_cfg.get("enabled", True)),
            enabled=bool(tm_cfg.get("enabled", True)),
            break_mode=str(tm_cfg.get("break_mode", "fullscreen")),
            blink_reminder=bool(tm_cfg.get("blink_reminder", False)),
            sound_enabled=bool(tm_cfg.get("sound_enabled", True)),
            dnd_fullscreen=bool(tm_cfg.get("dnd_fullscreen", True)),
            restart_on_idle=bool(tm_cfg.get("restart_on_idle", True)),
            restart_idle_minutes=int(tm_cfg.get("restart_idle_minutes", 15)),
            restart_on_unlock=bool(tm_cfg.get("restart_on_unlock", True)),
            pre_break_prompt_sec=int(tm_cfg.get("pre_break_prompt_sec", 30)),
            allow_delay_timer=bool(tm_cfg.get("allow_delay_timer", True)),
            max_delay_times=int(tm_cfg.get("max_delay_times", 3)),
        )
        self._latest_timer_status = {}
        self._active_break = None
        self._was_hidden_before_break = False
        self._is_window_hidden = False
        self._prev_foreground_hwnd = None
        self._saved_win_rect = None
        self._saved_win_style = None

        # Connect Timer Engine Callbacks
        self.timer_engine.on_break_trigger = self._on_break_trigger
        self.timer_engine.start()
        self.timer_engine.on_break_end = self._on_break_end
        self.timer_engine.on_tick = self._on_timer_tick
        self.timer_engine.on_pre_break_prompt = self._on_pre_break_prompt
        self.timer_engine.on_blink_reminder = self._on_blink_reminder

        self._sync_mini_window_overlay()

        # 5. Tray Manager
        self.tray_mgr = TrayManager(
            on_open_dashboard=self.show_window,
            on_apply_preset=self.apply_preset_name,
            on_toggle_ruler=self.toggle_focus_ruler,
            on_trigger_break=self.trigger_break,
            on_exit=self.hard_exit,
            on_toggle_spotlight=lambda: self.toggle_spotlight_focus(),
            on_pause_timer=lambda mins: self.pause_timer_for(mins),
            on_disable_temp=lambda mins: self.disable_filter_temporarily(mins),
            on_toggle_magic=lambda: self.magic_engine.dim_foreground_window(force=True),
        )

        self._window = None
        self._closing = False
        self._tray_update_timer = None

        # Circadian Background Worker Thread
        self._circadian_stop_event = threading.Event()
        self._circadian_wake_event = threading.Event()
        self._circadian_thread = threading.Thread(
            target=self._circadian_worker_loop,
            name="EyeGuard-CircadianWorker",
            daemon=True,
        )
        self._circadian_thread.start()

    def _install_crash_reporter(self):
        orig_hook = sys.excepthook

        def _crash_hook(exc_type, exc_val, exc_tb):
            try:
                if self.config_mgr.get("general", "send_crash_reports", default=False):
                    log_file = self.config_mgr.config_dir / "crash_reports.log"
                    with open(log_file, "a", encoding="utf-8") as f:
                        f.write(f"\n--- Crash at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                        traceback.print_exception(exc_type, exc_val, exc_tb, file=f)
            except Exception:
                pass
            orig_hook(exc_type, exc_val, exc_tb)

        sys.excepthook = _crash_hook

    @property
    def window(self):
        return self._window

    @window.setter
    def window(self, val):
        self._window = val

    def _get_main_hwnd(self) -> int:
        return int(user32.FindWindowW(None, "EyeGuard") or 0)

    def show_window(self):
        self._is_window_hidden = False
        hwnd = self._get_main_hwnd()
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            except Exception:
                pass
        if self._window:
            try:
                self._window.show()
                self._window.restore()
            except Exception:
                pass
        if hwnd:
            try:
                force_foreground_window(hwnd)
            except Exception:
                pass

    def hide_window(self):
        self._is_window_hidden = True
        hwnd = self._get_main_hwnd()
        if hwnd:
            try:
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
            except Exception:
                pass
        if self._window:
            try:
                self._window.hide()
            except Exception:
                pass


    def _enter_fullscreen_kiosk_mode(self, transparency_pct: int = 100):
        hwnd = self._get_main_hwnd()
        if not hwnd:
            return
        try:
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            self._saved_win_rect = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
            self._saved_win_style = user32.GetWindowLongW(hwnd, -16)  # GWL_STYLE

            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            # WS_POPUP | WS_VISIBLE = 0x90000000
            user32.SetWindowLongW(hwnd, -16, 0x90000000)
            if transparency_pct < 100:
                ex = user32.GetWindowLongW(hwnd, -20)
                user32.SetWindowLongW(hwnd, -20, ex | 0x00080000)  # WS_EX_LAYERED
                alpha = max(140, min(255, int(255 * (transparency_pct / 100.0))))
                user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 2)
            force_foreground_window(hwnd)
        except Exception:
            pass

    def _exit_fullscreen_kiosk_mode(self):
        hwnd = self._get_main_hwnd()
        try:
            if hwnd and self._saved_win_rect:
                x, y, w, h = self._saved_win_rect
                style = self._saved_win_style or 0x10CF0000  # WS_OVERLAPPEDWINDOW
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
                user32.SetWindowLongW(hwnd, -16, style)
                user32.SetWindowPos(
                    hwnd,
                    ctypes.c_void_p(-2),  # HWND_NOTOPMOST
                    x,
                    y,
                    max(700, w),
                    max(480, h),
                    0x0080 | 0x0020,  # SWP_HIDEWINDOW | SWP_FRAMECHANGED
                )
            self._saved_win_rect = None

            # Always hide window after break exit so the user is never dumped into the app
            self._was_hidden_before_break = False
            self.hide_window()

            # Restore focus to previous foreground window
            prev_h = getattr(self, "_prev_foreground_hwnd", None)
            self._prev_foreground_hwnd = None
            if prev_h and user32.IsWindow(prev_h):
                try:
                    force_foreground_window(prev_h)
                except Exception:
                    pass
        except Exception:
            pass

    # --- Native Desktop Overlays Synchronization ---
    def _sync_mini_window_overlay(self):
        tm = self.config_mgr.get("timer", default={}) or {}
        show = bool(tm.get("show_mini_window", False))
        style_raw = str(tm.get("mini_window_style", "progress_bar"))
        style = "window" if style_raw in ("window", "mini_window") else "bar"
        translucent = bool(tm.get("mini_window_translucent", True))
        opacity = int(tm.get("mini_window_opacity", 100))
        self.desktop_overlays.configure_mini_window(
            enabled=show,
            style=style,
            auto_translucent=translucent,
            opacity_pct=opacity,
        )

    def _sync_magic_toolbar_overlay(self):
        mx = self.config_mgr.get("magic_x", default={}) or {}
        enabled = bool(mx.get("enabled", False)) and bool(mx.get("show_toolbar", False))
        self.desktop_overlays.configure_magic_toolbar(
            enabled=enabled,
            color=str(mx.get("toolbar_color", "#7c3aed")),
            placement=str(mx.get("placement", "center")),
            offset_dp=int(mx.get("offset_dp", 0)),
            delay_sec=float(mx.get("delay_sec", 0.50)),
        )

    def _on_magic_toolbar_action(self, action: str, target_hwnd: int):
        if action == "dark":
            self.toggle_invert(not self.is_invert)
        elif action == "grayscale":
            self.toggle_grayscale(not self.is_gray)
        elif action == "dim":
            self.magic_engine.dim_foreground_window(target_hwnd=target_hwnd, force=True)
        elif action == "pin":
            self.magic_engine.toggle_pin_foreground_window(target_hwnd=target_hwnd)

    def _on_pre_break_prompt(self, remaining_sec: int):
        tm = self.config_mgr.get("timer", default={}) or {}
        pos = str(tm.get("reminder_popup_pos", "top_center"))
        self.desktop_overlays.show_toast(
            title=f"Break Reminder — Starting in {remaining_sec}s",
            subtitle="Save your work now; an eye relaxation break is about to start.",
            position=pos,
            duration_sec=5.0,
        )

    def _on_blink_reminder(self):
        try:
            tm = self.config_mgr.get("timer", default={}) or {}
            pos = str(tm.get("reminder_popup_pos", "top_center"))
            lang = str(self.config_mgr.get("general", "language", default="en"))
            if lang == "ar":
                tag = "ترطيب القرنية وتجديد الدموع"
                msg = "ارمش مرتين بلطف"
            else:
                tag = "TEAR FILM HYDRATION"
                msg = "Blink gently twice"
            self.desktop_overlays.show_toast(
                title=tag,
                subtitle=msg,
                position=pos,
                duration_sec=4.0,
            )
        except Exception:
            self.desktop_overlays.show_toast(
                title="TEAR FILM HYDRATION",
                subtitle="Blink gently twice",
                position="top_center",
                duration_sec=4.0,
            )

    def trigger_blink_preview(self):
        """Immediately trigger the native desktop blink reminder toast for testing."""
        self._on_blink_reminder()

    def _on_app_rule_trigger(self, rule_mode):
        """Called by MagicWindowEngine when foreground app matches an Auto Dark / Smart App Rule."""
        try:
            preset_map = {
                "Health": (5000, 85),
                "Office": (5500, 90),
                "Reading": (4000, 95),
                "Editing": (6500, 100),
                "Cinema": (5800, 90),
                "Gaming": (6500, 100),
                "Bedtime": (2700, 65),
                "Biohacker": (1000, 50),
            }
            if rule_mode in preset_map:
                pk, pb = preset_map[rule_mode]
                self._rule_override_active = True
                self.gamma_engine.set_display(pk, pb, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode)
            elif rule_mode == "dark":
                self._rule_override_active = True
                self.gamma_engine.set_display(self.current_k, self.current_b, self.is_gray, True, smooth=False, color_mode=self.color_filter_mode)
            elif rule_mode == "grayscale":
                self._rule_override_active = True
                self.gamma_engine.set_display(self.current_k, self.current_b, True, self.is_invert, smooth=False, color_mode=self.color_filter_mode)
            elif rule_mode == "warm":
                self._rule_override_active = True
                self.gamma_engine.set_display(3800, self.current_b, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode)
            elif rule_mode == "exclude":
                self._rule_override_active = True
                self.gamma_engine.set_display(6500, 100, False, False, smooth=False, color_mode="normal")
            elif rule_mode in AUTHENTIC_READING_THEMES or rule_mode == "custom":
                self._rule_override_active = True
                self.gamma_engine.set_reading_theme(rule_mode, self.custom_bg_hex, self.custom_txt_hex)
            else:
                if self._rule_override_active:
                    self._rule_override_active = False
                    self.gamma_engine.set_reading_theme(
                        self.active_reading_theme if self.reading_theme_scope == "fullscreen" else "normal"
                    )
                    self.gamma_engine.set_display(
                        self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True
                    )
        except Exception:
            pass

    def reset_natural(self):
        self._filter_disabled_until_ts = None
        self.current_k = 6500
        self.current_b = 100
        self.current_sat = 100
        self.is_gray = False
        self.is_invert = False
        self.color_filter_mode = "day"
        self.config_mgr.set("screen", "kelvin", 6500)
        self.config_mgr.set("screen", "brightness", 100)
        self.config_mgr.set("screen", "saturation", 100)
        self.config_mgr.set("screen", "grayscale", False)
        self.config_mgr.set("screen", "invert", False)
        self.config_mgr.set("screen", "color_filter_mode", "day")
        self.config_mgr.set("screen", "mode", "Natural")
        self.gamma_engine.set_display(6500, 100, False, False, smooth=False, color_mode="day")

        # Universal Emergency Unlock: disable focus ruler, spotlight mask, and clear stuck overlays
        if hasattr(self, "focus_ruler"):
            try:
                self.focus_ruler.disable()
            except Exception:
                pass
        if hasattr(self, "magic_engine"):
            try:
                self.magic_engine.restore_all_windows()
            except Exception:
                pass
        if hasattr(self, "desktop_overlays"):
            try:
                self.desktop_overlays.hide_overlay()
            except Exception:
                pass

        self._schedule_tray_update()
        return {"kelvin": 6500, "brightness": 100, "mode": "day"}

    def disable_filter_temporarily(self, minutes: int = 60):
        """Temporarily disables the display filter for N minutes (e.g. 60m for color work) or 0 to resume."""
        if int(minutes) <= 0:
            self._filter_disabled_until_ts = None
            self.gamma_engine.set_display(
                self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode
            )
            return {"disabled": False, "until_ts": None}
        self._filter_disabled_until_ts = time.time() + int(minutes) * 60
        self.gamma_engine.set_display(6500, 100, False, False, smooth=True)
        return {"disabled": True, "until_ts": self._filter_disabled_until_ts}

    def apply_preset_name(self, name: str):
        self._filter_disabled_until_ts = None
        if self.circadian_active:
            self.circadian_active = False
            self.config_mgr.set("circadian", "enabled", False)
        self.config_mgr.set("screen", "mode", str(name))
        saved_presets = self.config_mgr.get("presets", default={}) or {}
        if name in saved_presets and isinstance(saved_presets[name], dict):
            k = int(saved_presets[name].get("kelvin", 5000))
            b = int(saved_presets[name].get("brightness", 85))
            self.current_k = max(1000, min(6500, k))
            self.current_b = max(5, min(100, b))
            self.gamma_engine.set_display(
                self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode
            )
            self.config_mgr.set("screen", "kelvin", self.current_k)
            self.config_mgr.set("screen", "brightness", self.current_b)
            self._schedule_tray_update()
            return {"name": name, "kelvin": self.current_k, "brightness": self.current_b}

        presets = {
            "Health": (5000, 85),
            "Office": (5500, 90),
            "Reading": (4000, 95),
            "Editing": (6500, 100),
            "Cinema": (5800, 90),
            "Gaming": (6500, 100),
            "Bedtime": (2700, 65),
            "Biohacker": (1000, 50),
            "Natural": (6500, 100),
        }
        if name in presets:
            k, b = presets[name]
            self.current_k = k
            self.current_b = b
            self.gamma_engine.set_display(
                self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode
            )
            self.config_mgr.set("screen", "kelvin", self.current_k)
            self.config_mgr.set("screen", "brightness", self.current_b)
            self._schedule_tray_update()
        return {"name": name, "kelvin": self.current_k, "brightness": self.current_b}

    def save_custom_preset(self, name: str, kelvin: int, brightness: int):
        presets = self.config_mgr.get("presets", default={}) or {}
        presets[str(name)] = {
            "kelvin": int(kelvin),
            "brightness": int(brightness),
            "k": int(kelvin),
            "b": int(brightness),
        }
        self.config_mgr.set("presets", presets)
        return presets

    def reset_presets_to_default(self):
        default_presets = {}
        for k_name, vals in DEFAULT_CONFIG["presets"].items():
            kv = int(vals.get("kelvin", vals.get("k", 5000)))
            bv = int(vals.get("brightness", vals.get("b", 85)))
            default_presets[k_name] = {"kelvin": kv, "brightness": bv, "k": kv, "b": bv}
        self.config_mgr.set("presets", default_presets)
        return default_presets

    def set_kelvin(self, k: int):
        self._filter_disabled_until_ts = None
        if self.circadian_active:
            self.circadian_active = False
            self.config_mgr.set("circadian", "enabled", False)
        self.current_k = max(1000, min(6500, int(k)))
        self.gamma_engine.set_display(
            self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=False, color_mode=self.color_filter_mode
        )
        self.config_mgr.set("screen", "kelvin", self.current_k)
        self._schedule_tray_update()

    def set_brightness(self, b: int):
        self._filter_disabled_until_ts = None
        if self.circadian_active:
            self.circadian_active = False
            self.config_mgr.set("circadian", "enabled", False)
        self.current_b = max(5, min(100, int(b)))
        self.gamma_engine.set_display(
            self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=False, color_mode=self.color_filter_mode
        )
        self.config_mgr.set("screen", "brightness", self.current_b)

    def set_saturation(self, sat_pct: int):
        self.current_sat = max(0, min(100, int(sat_pct)))
        self.gamma_engine.set_saturation(self.current_sat)
        self.config_mgr.set("screen", "saturation", self.current_sat)
        return self.current_sat

    def set_hardware_brightness(self, hw_pct: int):
        self.current_hw_b = max(0, min(100, int(hw_pct)))
        self.gamma_engine.set_hardware_brightness(self.current_hw_b)
        self.config_mgr.set("screen", "hardware_brightness", self.current_hw_b)
        return self.current_hw_b

    def _schedule_tray_update(self):
        if self._tray_update_timer:
            self._tray_update_timer.cancel()

        def _update():
            rf, gf, bf = kelvin_to_rgb_extended(self.current_k)
            self.tray_mgr.update_icon_color(int(rf * 255), int(gf * 255), int(bf * 255))

        self._tray_update_timer = threading.Timer(0.35, _update)
        self._tray_update_timer.daemon = True
        self._tray_update_timer.start()

    def set_color_filter_mode(self, mode: str):
        mode = str(mode or "normal").lower().strip()
        if mode in ("day", "standard"):
            mode = "normal"
        return self.set_reading_theme(mode)

    def toggle_grayscale(self, enable: bool):
        self.is_gray = bool(enable)
        self.gamma_engine.set_display(
            self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=False, color_mode=self.color_filter_mode
        )
        self.config_mgr.set("screen", "grayscale", self.is_gray)

    def toggle_invert(self, enable: bool):
        self.is_invert = bool(enable)
        self.gamma_engine.set_display(
            self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=False, color_mode=self.color_filter_mode
        )
        self.config_mgr.set("screen", "invert", self.is_invert)

    # --- Focus Engine (Reading Focus + Spotlight Focus) ---
    def toggle_focus_ruler(self):
        self.focus_ruler.toggle()
        return self.get_focus_config()

    def toggle_spotlight_focus(self, enable: bool = None):
        state = self.focus_ruler.toggle_spotlight(enable)
        self.config_mgr.set("focus_ruler", "spotlight_enabled", state)
        return self.get_focus_config()

    def set_slit_height(self, h: int):
        self.focus_ruler.update(slit_height=int(h))
        self.config_mgr.set("focus_ruler", "slit_height", int(h))

    def set_slit_opacity(self, a: float):
        self.focus_ruler.update(opacity=float(a))
        self.config_mgr.set("focus_ruler", "opacity", float(a))

    def set_slit_color(self, color_hex: str):
        self.focus_ruler.set_color(str(color_hex))
        self.config_mgr.set("focus_ruler", "color_hex", str(color_hex))

    def set_spotlight_opacity(self, a: float):
        val = max(0.05, min(0.98, float(a)))
        res = self.focus_ruler.configure_spotlight(opacity=val)
        self.config_mgr.set("focus_ruler", "spotlight_opacity", val)
        return res

    def configure_spotlight_focus(self, **kwargs):
        res = self.focus_ruler.configure_spotlight(**kwargs)
        for k, v in kwargs.items():
            if v is not None:
                cfg_key = "spotlight_enabled" if k == "enabled" else k
                self.config_mgr.set("focus_ruler", cfg_key, v)
        return res

    def get_focus_config(self):
        st = self.focus_ruler.get_focus_state()
        hk = self.hotkey_engine.get_bindings()
        st["reading_hotkey"] = hk.get("toggle_ruler", "Ctrl + Alt + F")
        st["spotlight_hotkey"] = hk.get("toggle_spotlight", "Ctrl + Alt + S")
        return st

    # --- Window Focus Engine (Active Window Isolation) ---
    def get_magic_config(self):
        st = self.magic_engine.get_state()
        hk = self.hotkey_engine.get_bindings()
        st["magic_hotkey"] = hk.get("magic_dark", "Ctrl + Alt + M")
        return st

    def update_magic_config(self, **kwargs):
        st = self.magic_engine.configure(**kwargs)
        if st.get("auto_dark_enabled") or st.get("disable_fullscreen"):
            self.magic_engine.start()
        for k, v in kwargs.items():
            if v is not None:
                self.config_mgr.set("magic_x", k, v)
        self._sync_magic_toolbar_overlay()
        return self.get_magic_config()

    def set_magic_app_rule(self, exe_name: str, mode: str):
        clean_exe = str(exe_name).strip().lower()
        if clean_exe and not clean_exe.endswith(".exe"):
            clean_exe += ".exe"
        st = self.magic_engine.set_app_rule(clean_exe, mode)
        self.config_mgr.set("magic_x", "app_rules", st["app_rules"])
        # Also mirror into custom_rules.rules_list so both tabs stay 100% synchronized
        cr = self.config_mgr.get("custom_rules", default={}) or {}
        rules_list = list(cr.get("rules_list", []) or [])
        updated = False
        for item in rules_list:
            if str(item.get("trigger", "")).strip().lower() == clean_exe:
                if not mode or mode == "off":
                    rules_list.remove(item)
                else:
                    item["mode"] = str(mode)
                updated = True
                break
        if not updated and mode and mode != "off":
            next_id = max([int(r.get("id", 0)) for r in rules_list] + [0]) + 1
            rules_list.append({"id": next_id, "rule": clean_exe, "trigger": clean_exe, "mode": str(mode)})
        self.config_mgr.set("custom_rules", "rules_list", rules_list)
        return self.get_magic_config()

    # --- Break Reminder Engine ---
    def toggle_timer_pause(self):
        if not self.timer_engine.enabled:
            # Master toggle is OFF - cannot start or toggle timer!
            return self.get_timer_status()
        elif self.timer_engine.state == TimerState.PAUSED:
            self.timer_engine.resume()
        elif self.timer_engine.state in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK, TimerState.BLINK_20_20):
            self.timer_engine.skip_break()
        else:
            self.timer_engine.pause()
        return self.get_timer_status()

    def pause_timer_for(self, minutes: int):
        self.timer_engine.pause_for_minutes(int(minutes))
        return self.get_timer_status()

    def set_timer_enabled(self, enabled: bool):
        self.timer_engine.set_enabled(bool(enabled))
        self.config_mgr.set("timer", "enabled", bool(enabled))
        return self.get_timer_status()

    def set_break_mode(self, mode: str):
        self.timer_engine.configure(break_mode=str(mode))
        self.config_mgr.set("timer", "break_mode", str(mode))
        return self.get_timer_status()

    def set_blink_reminder(self, enabled: bool):
        self.timer_engine.configure(blink_reminder=bool(enabled))
        self.config_mgr.set("timer", "blink_reminder", bool(enabled))
        return self.get_timer_status()

    def reset_timer(self):
        if not self.timer_engine.enabled:
            return
        self.timer_engine.reset_work()

    def _on_break_trigger(self, break_type, duration_sec: int):
        if self.timer_engine.dnd_fullscreen and is_foreground_fullscreen():
            self.timer_engine.postpone_break(minutes=5)
            return

        tm_cfg = self.config_mgr.get("timer", default={}) or {}

        if self.timer_engine.sound_enabled:
            try:
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception:
                pass

        b_str = break_type.value if hasattr(break_type, "value") else str(break_type)
        if b_str == "blink_20_20":
            title = "EyeGuard — 20-20-20 Rule"
            msg = f"Rest your eyes: Look at an object 20 feet away for {duration_sec} seconds."
        elif b_str == "long_break":
            title = "EyeGuard — Long Break"
            msg = f"Great focus session! Rest your eyes for {max(1, duration_sec // 60)} minutes."
        else:
            title = "EyeGuard — Short Break"
            msg = f"Close your eyes and relax your ocular muscles for {max(1, duration_sec // 60)} minutes."

        self.tray_mgr.notify(title, msg)

        if self.timer_engine.break_mode == "gentle":
            self._active_break = None
            pos = str(tm_cfg.get("reminder_popup_pos", "top_center"))
            self.desktop_overlays.show_toast(
                title=title,
                subtitle=msg,
                position=pos,
                duration_sec=8.0,
            )
        else:
            cust_wp_url = ""
            wp_path = str(tm_cfg.get("custom_wallpaper_path", "")).strip()
            if tm_cfg.get("use_custom_wallpaper", False) and wp_path and os.path.isfile(wp_path):
                try:
                    cust_wp_url = Path(wp_path).as_uri()
                except Exception:
                    cust_wp_url = ""

            self._active_break = {
                "type": b_str,
                "duration": int(duration_sec),
                "mode": self.timer_engine.break_mode,
                "id": int(time.time() * 1000),
                "transparency": int(tm_cfg.get("lock_screen_transparency", 100)),
                "fade": str(tm_cfg.get("lock_screen_fade", "fast")),
                "random_wallpaper": bool(tm_cfg.get("random_lock_wallpaper", True)),
                "use_custom_wallpaper": bool(tm_cfg.get("use_custom_wallpaper", False)),
                "custom_wallpaper": cust_wp_url,
                "custom_wallpaper_url": cust_wp_url,
                "use_password": bool(tm_cfg.get("use_password_protection", False)),
                "password": str(tm_cfg.get("break_password", "")),
                "allow_delay": bool(self.timer_engine.allow_delay_timer),
                "max_delays": int(self.timer_engine.max_delay_times),
                "delays_used": int(self.timer_engine.delays_used),
                "delays_remaining": max(
                    0, self.timer_engine.max_delay_times - self.timer_engine.delays_used
                )
                if self.timer_engine.allow_delay_timer
                else 0,
            }

            if tm_cfg.get("play_music_during_break", False):
                self.audio_player.start_break_music(str(tm_cfg.get("break_music_path", "")))

            main_h = self._get_main_hwnd()
            fg = user32.GetForegroundWindow()
            if fg and fg != main_h:
                self._prev_foreground_hwnd = fg
            else:
                self._prev_foreground_hwnd = None

            self._was_hidden_before_break = self._is_window_hidden
            self.show_window()
            main_h = self._get_main_hwnd()
            if main_h:
                force_foreground_window(main_h)
            if not _is_running_unit_tests():
                threading.Timer(
                    0.05,
                    lambda: self._enter_fullscreen_kiosk_mode(
                        int(tm_cfg.get("lock_screen_transparency", 100))
                    ),
                ).start()

    def _on_break_end(self):
        last_break = self._active_break
        self._active_break = None
        self.audio_player.stop_break_music()
        if not _is_running_unit_tests():
            self._exit_fullscreen_kiosk_mode()

        tm_cfg = self.config_mgr.get("timer", default={}) or {}

        if tm_cfg.get("play_alert_sound_end", True) and self.timer_engine.sound_enabled:
            self.audio_player.play_alert(str(tm_cfg.get("alert_sound_path", "")))

        self.tray_mgr.notify(
            "EyeGuard — Break Finished", "Resume your work with refreshed focus."
        )

        if last_break and not last_break.get("is_preview"):
            btype = last_break.get("type", "short_break")
            if btype == "long_break" and tm_cfg.get("lock_windows_end_break", False):
                try:
                    user32.LockWorkStation()
                except Exception:
                    pass
            elif btype == "short_break" and tm_cfg.get("lock_windows_end_short_break", False):
                try:
                    user32.LockWorkStation()
                except Exception:
                    pass

    def _on_timer_tick(self, status: dict):
        self._latest_timer_status = status
        try:
            st_val = status.get("state", "working")
            phase_lbl = "BREAK" if "break" in str(st_val) else ("PAUSED" if status.get("is_paused") else "WORK")
            tot = status.get("work_duration", 2700)
            self.desktop_overlays.update_mini_timer(
                status.get("remaining_seconds", 0), tot, phase_lbl
            )
        except Exception:
            pass

    def trigger_break(self):
        self.show_window()
        self.take_break_now("short_break")

    def preview_break_mode(self):
        mode = self.timer_engine.break_mode
        if mode == "gentle":
            self._on_blink_reminder()
            self.tray_mgr.notify(
                "EyeGuard — Gentle Break Preview",
                "Gentle notification without locking the screen: take a deep breath and rest your eyes.",
            )
        else:
            self.take_break_now("short_break", duration_sec=15)
            if self._active_break:
                self._active_break["is_preview"] = True

    def take_break_now(self, break_type: str = "short_break", duration_sec: int = None):
        if not self.timer_engine.enabled:
            return
        self.timer_engine.take_break_now(break_type, duration_sec)

    def skip_break(self, password_attempt: str = None) -> dict:
        tm_cfg = self.config_mgr.get("timer", default={}) or {}
        if tm_cfg.get("use_password_protection", False):
            saved_pwd = str(tm_cfg.get("break_password", "")).strip()
            if saved_pwd and str(password_attempt or "").strip() != saved_pwd:
                return {"ok": False, "error": "invalid_password", "reason": "invalid_password"}
        self._active_break = None
        self.timer_engine.skip_break()
        self._exit_fullscreen_kiosk_mode()
        return {"ok": True}

    def postpone_break(self, minutes: int = 5) -> dict:
        ok = self.timer_engine.postpone_break(int(minutes))
        if ok:
            self._active_break = None
            self._exit_fullscreen_kiosk_mode()
        return {
            "ok": ok,
            "delays_remaining": max(
                0, self.timer_engine.max_delay_times - self.timer_engine.delays_used
            )
            if self.timer_engine.allow_delay_timer
            else 0,
        }

    def get_timer_status(self):
        return self.timer_engine.get_status()

    def configure_timer(
        self,
        work_min: int = 25,
        break_sec: int = 300,
        enable_20_20: bool = True,
        enable_pomodoro: bool = True,
        long_cycles: int = None,
    ):
        kwargs = {
            "work_duration_sec": int(work_min) * 60,
            "short_break_sec": int(break_sec),
            "enable_20_20": bool(enable_20_20),
            "enable_pomodoro": bool(enable_pomodoro),
        }
        if long_cycles is not None:
            kwargs["cycles_before_long"] = int(long_cycles)
            self.config_mgr.set("timer", "long_break_interval", int(long_cycles))

        self.timer_engine.configure(**kwargs)
        self.config_mgr.set("timer", "work_minutes", int(work_min))
        self.config_mgr.set("timer", "short_break_minutes", max(1, int(break_sec) // 60))
        self.config_mgr.set("timer", "enable_20_20_rule", bool(enable_20_20))
        self.config_mgr.set("timer", "enable_pomodoro", bool(enable_pomodoro))
        return self.get_timer_status()

    def update_timer_config(
        self,
        e20: bool,
        idle: bool,
        sound: bool = None,
        dnd: bool = None,
        idle_sec: int = None,
    ):
        self.timer_engine.configure(
            enable_20_20=bool(e20),
            auto_pause=bool(idle),
            sound_enabled=sound,
            dnd_fullscreen=dnd,
            idle_threshold_sec=idle_sec,
        )
        self.config_mgr.set("timer", "enable_20_20_rule", bool(e20))
        self.config_mgr.set("timer", "auto_pause_on_idle", bool(idle))
        if sound is not None:
            self.config_mgr.set("timer", "sound_enabled", bool(sound))
        if dnd is not None:
            self.config_mgr.set("timer", "dnd_fullscreen", bool(dnd))
        if idle_sec is not None:
            self.config_mgr.set("timer", "idle_threshold_seconds", int(idle_sec))
        return self.get_timer_status()

    # --- Circadian Engine (3-Stage circadian Curve: Daytime / Sunset / Bedtime) ---
    def toggle_circadian(self, enable: bool):
        self.circadian_active = bool(enable)
        self.config_mgr.set("circadian", "enabled", self.circadian_active)
        if self.circadian_active:
            self._circadian_wake_event.set()
        return self.get_solar_curve()

    def update_circadian_schedule(
        self,
        sunrise: str,
        sunset: str,
        day_k: int = 6500,
        night_k: int = 3400,
        bedtime: str = "22:30",
        bedtime_k: int = 1900,
        day_b: int = 100,
        night_b: int = 80,
        bedtime_b: int = 55,
    ):
        self.solar_engine = SolarEngine(
            sunrise=str(sunrise),
            sunset=str(sunset),
            bedtime=str(bedtime),
            day_kelvin=int(day_k),
            night_kelvin=int(night_k),
            bedtime_kelvin=int(bedtime_k),
            day_brightness=int(day_b),
            night_brightness=int(night_b),
            bedtime_brightness=int(bedtime_b),
        )
        self.config_mgr.set("circadian", "sunrise", self.solar_engine.sunrise_str)
        self.config_mgr.set("circadian", "sunset", self.solar_engine.sunset_str)
        self.config_mgr.set("circadian", "bedtime", self.solar_engine.bedtime_str)
        self.config_mgr.set("circadian", "day_kelvin", self.solar_engine.day_kelvin)
        self.config_mgr.set("circadian", "night_kelvin", self.solar_engine.night_kelvin)
        self.config_mgr.set("circadian", "bedtime_kelvin", self.solar_engine.bedtime_kelvin)
        self.config_mgr.set("circadian", "day_brightness", self.solar_engine.day_brightness)
        self.config_mgr.set("circadian", "night_brightness", self.solar_engine.night_brightness)
        self.config_mgr.set("circadian", "bedtime_brightness", self.solar_engine.bedtime_brightness)
        if self.circadian_active:
            self._circadian_wake_event.set()
        return self.get_solar_curve()

    def get_solar_curve(self):
        state = self.solar_engine.get_solar_state_for_time()
        pts = self.solar_engine.get_24h_curve_points()
        return {
            "enabled": self.circadian_active,
            "sunrise": self.solar_engine.sunrise_str,
            "sunset": self.solar_engine.sunset_str,
            "bedtime": self.solar_engine.bedtime_str,
            "day_kelvin": self.solar_engine.day_kelvin,
            "night_kelvin": self.solar_engine.night_kelvin,
            "bedtime_kelvin": self.solar_engine.bedtime_kelvin,
            "day_brightness": self.solar_engine.day_brightness,
            "night_brightness": self.solar_engine.night_brightness,
            "bedtime_brightness": self.solar_engine.bedtime_brightness,
            "current_state": state,
            "points": pts,
            "curve_points": pts,
        }

    def _circadian_worker_loop(self):
        while not self._closing and not self._circadian_stop_event.is_set():
            # Check temporary filter bypass expiration
            if (
                self._filter_disabled_until_ts is not None
                and time.time() >= self._filter_disabled_until_ts
            ):
                self._filter_disabled_until_ts = None
                self.gamma_engine.set_display(
                    self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True, color_mode=self.color_filter_mode
                )

            if self.circadian_active and self._filter_disabled_until_ts is None:
                try:
                    solar_k = self.solar_engine.get_kelvin_for_time()
                    st = self.solar_engine.get_solar_state_for_time()
                    self.current_k = int(solar_k)
                    self.current_b = int(st["brightness"])
                    self.gamma_engine.set_display(
                        self.current_k,
                        self.current_b,
                        self.is_gray,
                        self.is_invert,
                        smooth=True,
                        color_mode=self.color_filter_mode,
                    )
                    if self.config_mgr.get("circadian", "auto_windows_dark_mode", default=False):
                        try:
                            from core.windows_theme import set_windows_dark_mode
                            is_dark_period = (st.get("stage") in ("sunset", "bedtime", "night"))
                            set_windows_dark_mode(is_dark_period)
                        except Exception:
                            pass
                    self._schedule_tray_update()
                except Exception:
                    pass
            self._circadian_wake_event.wait(timeout=30.0)
            self._circadian_wake_event.clear()

    # --- Windows Startup Integration ---
    def set_windows_startup(self, enable: bool) -> bool:
        en = bool(enable)
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        app_name = "EyeGuard"
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            )
            if en:
                exe_path = (
                    sys.executable
                    if getattr(sys, "frozen", False)
                    else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                )
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, str(exe_path))
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception:
            pass
        self.config_mgr.set("general", "launch_on_startup", en)
        return en


    def _on_reading_app_focus(self, is_focused: bool):
        """Called by MagicWindowEngine when target reader application gains or loses foreground focus."""
        if self.reading_theme_scope != "app":
            return
        if is_focused and self.active_reading_theme != "normal":
            self.gamma_engine.set_reading_theme(
                self.active_reading_theme,
                self.custom_bg_hex,
                self.custom_txt_hex,
            )
        else:
            self.gamma_engine.set_reading_theme("normal")

    def set_reading_theme(self, mode: str, custom_bg: Optional[str] = None, custom_txt: Optional[str] = None):
        """Activates a reading color theme (Document Themes or Custom Hex)."""
        clean_mode = str(mode or "normal").lower().strip()
        if clean_mode in ("day", "standard"):
            clean_mode = "normal"
        if clean_mode != "normal" and self.circadian_active:
            self.circadian_active = False
            self.config_mgr.set("circadian", "enabled", False)
        self.active_reading_theme = clean_mode
        self.color_filter_mode = clean_mode
        self.is_gray = (clean_mode == "e_ink")
        self.is_invert = (clean_mode in ("dark", "night"))

        if custom_bg:
            self.custom_bg_hex = str(custom_bg).strip()
            self.config_mgr.set("screen", "custom_bg_hex", self.custom_bg_hex)
        if custom_txt:
            self.custom_txt_hex = str(custom_txt).strip()
            self.config_mgr.set("screen", "custom_txt_hex", self.custom_txt_hex)

        self.config_mgr.set("screen", "reading_theme", self.active_reading_theme)
        self.config_mgr.set("screen", "color_filter_mode", self.active_reading_theme)
        self.config_mgr.set("screen", "grayscale", self.is_gray)
        self.config_mgr.set("screen", "invert", self.is_invert)

        if self.reading_theme_scope == "fullscreen":
            self.gamma_engine.set_reading_theme(
                self.active_reading_theme,
                self.custom_bg_hex,
                self.custom_txt_hex,
            )
        else:
            from core.window_focus import get_foreground_process_name
            hwnd, exe = get_foreground_process_name()
            exe_low = (exe or "").lower()
            if clean_mode == "normal":
                self.gamma_engine.set_reading_theme("normal")
            elif exe_low == self.reading_theme_target_app.lower() or "eyeguard" in exe_low:
                self.gamma_engine.set_reading_theme(
                    self.active_reading_theme,
                    self.custom_bg_hex,
                    self.custom_txt_hex,
                )
            else:
                self.gamma_engine.set_reading_theme("normal")

        self._schedule_tray_update()
        return self.get_reading_theme_state()

    def set_reading_theme_scope(self, scope: str, target_app: Optional[str] = None):
        """Toggles between global Full-Screen reading mode and Dedicated Reader Application targeting."""
        clean_scope = str(scope or "fullscreen").lower().strip()
        if clean_scope in ("fullscreen", "app"):
            self.reading_theme_scope = clean_scope
            self.config_mgr.set("screen", "reading_theme_scope", self.reading_theme_scope)
            self.magic_engine.reading_theme_scope = self.reading_theme_scope

        if target_app:
            t = str(target_app).strip().lower()
            if not t.endswith(".exe"):
                t += ".exe"
            self.reading_theme_target_app = t
            self.config_mgr.set("screen", "reading_theme_target_app", self.reading_theme_target_app)
            self.magic_engine.reading_theme_target_app = self.reading_theme_target_app

        if self.reading_theme_scope == "fullscreen":
            self.gamma_engine.set_reading_theme(
                self.active_reading_theme,
                self.custom_bg_hex,
                self.custom_txt_hex,
            )
        else:
            from core.window_focus import get_foreground_process_name
            hwnd, exe = get_foreground_process_name()
            if exe and exe.lower() == self.reading_theme_target_app.lower() and self.active_reading_theme != "normal":
                self.gamma_engine.set_reading_theme(
                    self.active_reading_theme,
                    self.custom_bg_hex,
                    self.custom_txt_hex,
                )
            else:
                self.gamma_engine.set_reading_theme("normal")
        return self.get_reading_theme_state()

    def get_reading_theme_state(self):
        from core.window_focus import get_running_window_processes
        running = get_running_window_processes()
        return {
            "success": True,
            "active_theme": self.active_reading_theme,
            "reading_theme": self.active_reading_theme,
            "color_filter_mode": self.active_reading_theme,
            "scope": self.reading_theme_scope,
            "target_app": self.reading_theme_target_app,
            "custom_bg_hex": self.custom_bg_hex,
            "custom_txt_hex": self.custom_txt_hex,
            "known_apps": [],  # Offline apps removed; only active processes shown
            "running_apps": running,
        }

    def get_poll_state(self):
        st_data = self.timer_engine.get_status()
        time_str = st_data["time_str"]

        st = self.timer_engine.state
        if not st_data["enabled"]:
            status_text = "Break Reminder Off"
        elif st == TimerState.PAUSED:
            if self.timer_engine._paused_manually:
                status_text = "Timer Paused (Manual)"
            elif st_data["is_idle"]:
                status_text = "Paused (User Idle)"
            else:
                status_text = "Timer Paused"
        elif st in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK):
            status_text = "Break in Progress — Rest Your Eyes"
        elif st == TimerState.BLINK_20_20:
            status_text = "20-20-20 Rule: Look 20 feet away for 20s"
        else:
            status_text = "Active Work Session"

        return {
            "time_str": time_str,
            "status_text": status_text,
            "monitor_count": self._monitor_count,
            "current_k": self.current_k,
            "current_b": self.current_b,
            "current_sat": self.current_sat,
            "saturation": self.current_sat,
            "current_hw_b": self.current_hw_b,
            "hardware_b": self.current_hw_b,
            "active_preset": self.config_mgr.get("screen", "mode", default="Health"),
            "reading_active": bool(getattr(self.focus_ruler, '_is_active', False)),
            "spotlight_active": bool(getattr(self.focus_ruler, '_spotlight_active', False)),
            "meeting_mode": bool(getattr(self, "_meeting_paused", False) or (getattr(self, "system_events", None) and getattr(self.system_events, "_in_meeting", False))),
            "color_filter_mode": self.active_reading_theme,
            "reading_theme": self.active_reading_theme,
            "reading_theme_scope": self.reading_theme_scope,
            "reading_theme_target_app": self.reading_theme_target_app,
            "custom_bg_hex": self.custom_bg_hex,
            "custom_txt_hex": self.custom_txt_hex,
            "is_gray": self.is_gray,
            "is_invert": self.is_invert,
            "filter_temp_disabled": self._filter_disabled_until_ts is not None,
            "circadian_active": self.circadian_active,
            "timer_enabled": st_data["enabled"],
            "break_mode": st_data["break_mode"],
            "blink_reminder": st_data["blink_reminder"],
            "last_blink_anim_ts": st_data["last_blink_anim_ts"],
            "timer_state": st_data["state"],
            "remaining_seconds": st_data["remaining_seconds"],
            "is_idle": st_data["is_idle"],
            "cycle_count": st_data["cycle_count"],
            "is_paused": st_data["is_paused"],
            "work_duration": st_data.get("work_duration", 2700),
            "work_remaining_sec": st_data["work_remaining_sec"],
            "break_remaining_sec": st_data["break_remaining_sec"],
            "blink_remaining_sec": st_data["blink_remaining_sec"],
            "delays_remaining": st_data.get("delays_remaining", 3),
            "active_break": self._active_break,
            "focus_active": self.focus_ruler.is_active,
            "spotlight_active": self.focus_ruler.is_spotlight_active,
            "active_rule_mode": self.magic_engine._active_rule_mode,
            "reading_theme_scope": self.reading_theme_scope,
            "reading_theme_target_app": self.reading_theme_target_app,
            "reading_app_focused": getattr(self.magic_engine, "_reading_app_was_focused", False),
        }

    def _setup_hotkeys(self):
        saved_hotkeys = self.config_mgr.get("hotkeys", default={})
        actions_map = {
            "brightness_up": lambda: self.set_brightness(min(100, self.current_b + 5)),
            "brightness_down": lambda: self.set_brightness(max(5, self.current_b - 5)),
            "kelvin_warmer": lambda: self.set_kelvin(max(1000, self.current_k - 250)),
            "kelvin_cooler": lambda: self.set_kelvin(min(6500, self.current_k + 250)),
            "reset_natural": lambda: (self.set_kelvin(6500), self.set_brightness(100)),
            "toggle_ruler": self.toggle_focus_ruler,
            "toggle_spotlight": lambda: self.toggle_spotlight_focus(),
            "magic_dark": lambda: self.magic_engine.dim_foreground_window(),
            "toggle_gray": lambda: self.toggle_grayscale(not self.is_gray),
            "toggle_invert": lambda: self.toggle_invert(not self.is_invert),
            "toggle_timer": self.toggle_timer_pause,
            "take_break": self.trigger_break,
        }

        for meta in HOTKEY_METADATA:
            act_id = meta["id"]
            default_combo = meta["default"]
            combo = (
                saved_hotkeys.get(act_id, default_combo)
                if isinstance(saved_hotkeys, dict)
                else default_combo
            )
            cb = actions_map.get(act_id)
            if cb:
                self.hotkey_engine.bind_action(act_id, combo, cb)

    def get_hotkeys_config(self):
        saved = self.config_mgr.get("hotkeys", default={})
        current_bindings = self.hotkey_engine.get_bindings()
        result = []
        for meta in HOTKEY_METADATA:
            act_id = meta["id"]
            current_combo = current_bindings.get(act_id)
            if current_combo is None:
                current_combo = (
                    saved.get(act_id, meta["default"])
                    if isinstance(saved, dict)
                    else meta["default"]
                )
            result.append(
                {
                    "id": act_id,
                    "name_ar": meta["name_ar"],
                    "name_en": meta["name_en"],
                    "desc_ar": meta["desc_ar"],
                    "desc_en": meta["desc_en"],
                    "combo": current_combo,
                    "default": meta["default"],
                }
            )
        return result

    def update_hotkey_combo(self, action_id: str, new_combo: str):
        cleaned_combo = new_combo.strip() if new_combo else ""
        self.hotkey_engine.set_combo(action_id, cleaned_combo)
        saved = self.config_mgr.get("hotkeys", default={})
        if not isinstance(saved, dict):
            saved = {}
        saved[action_id] = cleaned_combo
        self.config_mgr.set("hotkeys", saved)
        return self.get_hotkeys_config()

    def reset_default_hotkeys(self):
        defaults = {m["id"]: m["default"] for m in HOTKEY_METADATA}
        for act_id, combo in defaults.items():
            self.hotkey_engine.set_combo(act_id, combo)
        self.config_mgr.set("hotkeys", defaults)
        return self.get_hotkeys_config()

    # --- Preferences & Options Suite (General | Timer | Display | Hotkeys | Rules) ---
    def get_options_config(self):
        return {
            "version": self.VERSION,
            "general": self.config_mgr.get("general", default={}) or {},
            "timer": self.config_mgr.get("timer", default={}) or {},
            "screen": self.config_mgr.get("screen", default={}) or {},
            "presets": self.config_mgr.get("presets", default={}) or {},
            "custom_rules": self.config_mgr.get("custom_rules", default={}) or {},
        }

    def update_general_options(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None:
                if k == "launch_on_startup":
                    self.set_windows_startup(bool(v))
                else:
                    self.config_mgr.set("general", k, v)
        return self.get_options_config()

    def update_timer_options(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None:
                self.config_mgr.set("timer", k, v)
        tm = self.config_mgr.get("timer", default={}) or {}
        self.timer_engine.configure(
            auto_pause=bool(tm.get("auto_pause_on_idle", True)),
            idle_threshold_sec=int(tm.get("idle_threshold_seconds", 180)),
            sound_enabled=bool(tm.get("sound_enabled", True)),
            dnd_fullscreen=bool(tm.get("dnd_fullscreen", True)),
            restart_on_idle=bool(tm.get("restart_on_idle", True)),
            restart_idle_minutes=int(tm.get("restart_idle_minutes", 15)),
            restart_on_unlock=bool(tm.get("restart_on_unlock", True)),
            pre_break_prompt_sec=int(tm.get("pre_break_prompt_sec", 30)),
            allow_delay_timer=bool(tm.get("allow_delay_timer", True)),
            max_delay_times=int(tm.get("max_delay_times", 3)),
        )
        self._sync_mini_window_overlay()
        return self.get_options_config()

    def update_display_options(self, **kwargs):
        for k, v in kwargs.items():
            if v is not None:
                self.config_mgr.set("screen", k, v)
        scr = self.config_mgr.get("screen", default={}) or {}
        self.gamma_engine.green_background = bool(scr.get("keep_background_green", False))
        self.gamma_engine.smooth_transition = bool(scr.get("smooth_transition", True))
        self.gamma_engine.software_filter_mode = bool(scr.get("software_filter_mode", False))
        self.gamma_engine.standard_calibration_mode = bool(scr.get("standard_calibration_mode", True))
        self.gamma_engine.software_mouse_cursor = bool(scr.get("software_mouse_cursor", False))
        self.gamma_engine.set_display(
            self.current_k, self.current_b, self.is_gray, self.is_invert, smooth=True
        )
        return self.get_options_config()

    def _sync_custom_rules_to_engine(self):
        cr = self.config_mgr.get("custom_rules", default={}) or {}
        mx = self.config_mgr.get("magic_x", default={}) or {}
        enabled = bool(cr.get("enable_custom_rules", True)) or bool(mx.get("auto_dark_enabled", False))
        disable_fs = bool(cr.get("disable_fullscreen", False))
        rules_list = cr.get("rules_list", []) or []

        # Merge rules from both magic_x.app_rules and custom_rules.rules_list
        app_rules_dict = dict(mx.get("app_rules", {}) or {})
        for item in rules_list:
            trig = str(item.get("trigger", "")).strip().lower()
            mode = str(item.get("mode", "exclude")).strip()
            if trig:
                app_rules_dict[trig] = mode

        self.magic_engine.configure(
            auto_dark_enabled=enabled,
            disable_fullscreen=disable_fs,
            app_rules=app_rules_dict,
        )
        if enabled or disable_fs:
            self.magic_engine.start()

    def update_rules_options(
        self, disable_fullscreen: bool = None, enable_custom_rules: bool = None
    ):
        if disable_fullscreen is not None:
            self.config_mgr.set(
                "custom_rules", "disable_fullscreen", bool(disable_fullscreen)
            )
        if enable_custom_rules is not None:
            self.config_mgr.set(
                "custom_rules", "enable_custom_rules", bool(enable_custom_rules)
            )
        self._sync_custom_rules_to_engine()
        return self.get_options_config()

    def add_custom_rule(self, rule_name: str, trigger_exe: str, mode: str):
        cr = self.config_mgr.get("custom_rules", default={}) or {}
        rules_list = list(cr.get("rules_list", []) or [])
        next_id = max([int(r.get("id", 0)) for r in rules_list] + [0]) + 1
        trig = str(trigger_exe).strip().lower()
        if trig and not trig.endswith(".exe"):
            trig += ".exe"
        rules_list.append(
            {
                "id": next_id,
                "rule": str(rule_name or trig).strip(),
                "trigger": trig,
                "mode": str(mode or "Health").strip(),
            }
        )
        self.config_mgr.set("custom_rules", "rules_list", rules_list)
        self._sync_custom_rules_to_engine()
        return self.get_options_config()

    def delete_custom_rule(self, rule_id: int):
        cr = self.config_mgr.get("custom_rules", default={}) or {}
        removed_trigs = [
            str(r.get("trigger", "")).strip().lower()
            for r in (cr.get("rules_list", []) or [])
            if int(r.get("id", -1)) == int(rule_id)
        ]
        rules_list = [
            r
            for r in (cr.get("rules_list", []) or [])
            if int(r.get("id", -1)) != int(rule_id)
        ]
        self.config_mgr.set("custom_rules", "rules_list", rules_list)
        mx_rules = dict(self.config_mgr.get("magic_x", "app_rules", default={}) or {})
        for t in removed_trigs:
            mx_rules.pop(t, None)
        self.config_mgr.set("magic_x", "app_rules", mx_rules)
        self._sync_custom_rules_to_engine()
        return self.get_options_config()

    def browse_file_dialog(self, kind: str = "audio") -> str:
        if not self._window:
            return ""
        try:
            if kind == "image":
                file_types = (
                    "Image Files (*.png;*.jpg;*.jpeg;*.bmp;*.webp)",
                    "All files (*.*)",
                )
            elif kind == "exe":
                file_types = ("Executable Files (*.exe)", "All files (*.*)")
            else:
                file_types = ("Audio Files (*.wav;*.mp3;*.ogg)", "All files (*.*)")
            res = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False, file_types=file_types
            )
            if res and len(res) > 0:
                selected = str(res[0])
                if kind == "exe":
                    return os.path.basename(selected).lower()
                return selected
        except Exception:
            pass
        return ""

    def _on_system_resume(self):
        """Restores screen calibration and DWM color matrix upon OS wake from sleep."""
        try:
            time.sleep(1.0)
            self.gamma_engine.set_display(
                self.current_k, self.current_b, self.is_gray, self.is_invert,
                smooth=True, color_mode=self.color_filter_mode
            )
            if hasattr(self, "focus_ruler"):
                self.focus_ruler.recalibrate_metrics()
        except Exception:
            pass

    def _on_display_change(self):
        """Re-calibrates monitor geometry and color effect on display resolution / monitor change."""
        try:
            self.gamma_engine.reset_all_monitors()
            self.gamma_engine.set_display(
                self.current_k, self.current_b, self.is_gray, self.is_invert,
                smooth=False, color_mode=self.color_filter_mode
            )
            if hasattr(self, "focus_ruler"):
                self.focus_ruler.recalibrate_metrics()
        except Exception:
            pass

    def _on_meeting_status_change(self, in_meeting: bool):
        """Auto-pause screen warmth during conference/screen sharing to prevent yellow presentation screens."""
        try:
            if self.config_mgr.get("general", "auto_meeting_bypass", default=True):
                if in_meeting:
                    self._meeting_paused = True
                    self.gamma_engine.reset()
                    self.tray_mgr.notify("EyeGuard Meeting Mode", "Filter temporarily paused during video conference.")
                elif getattr(self, "_meeting_paused", False):
                    self._meeting_paused = False
                    self.gamma_engine.set_display(
                        self.current_k, self.current_b, self.is_gray, self.is_invert,
                        smooth=True, color_mode=self.color_filter_mode
                    )
        except Exception:
            pass

    def hard_exit(self):
        self._closing = True
        self._circadian_stop_event.set()
        self._circadian_wake_event.set()
        try:
            self.audio_player.stop_break_music()
        except Exception:
            pass
        try:
            self.desktop_overlays.stop()
        except Exception:
            pass
        try:
            self.focus_ruler.destroy()
        except Exception:
            pass
        try:
            self.magic_engine.stop()
        except Exception:
            pass
        try:
            self.system_events.stop()
        except Exception:
            pass
        self.hotkey_engine.stop()
        self.timer_engine.stop()
        self.tray_mgr.stop()
        if self._circadian_thread and self._circadian_thread.is_alive():
            self._circadian_thread.join(timeout=0.5)
        try:
            self.gamma_engine.shutdown()
        except Exception:
            pass
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass


class JSBridgeApi:
    def __init__(self, app: EyeGuardApp):
        self._app = app

    def browse_file_dialog(self, file_type="all"):
        return self.browse_file(kind=file_type)

    def set_focus_ruler_height(self, h):
        return self.set_slit_height(h)

    def set_focus_ruler_opacity(self, a):
        return self.set_slit_opacity(a)

    def show_notification(self, title, message):
        try:
            self._app.tray_mgr.notify(str(title), str(message))
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def poll_state(self):
        return self._app.get_poll_state()

    def get_state(self):
        return self._app.get_poll_state()

    def set_kelvin(self, k):
        self._app.set_kelvin(int(k))

    def set_brightness(self, b):
        self._app.set_brightness(int(b))

    def set_saturation(self, sat_pct):
        return self._app.set_saturation(int(sat_pct))

    def set_hardware_brightness(self, hw_pct):
        return self._app.set_hardware_brightness(int(hw_pct))

    def apply_preset(self, name):
        return self._app.apply_preset_name(str(name))

    def save_custom_preset(self, name, kelvin, brightness):
        return self._app.save_custom_preset(str(name), int(kelvin), int(brightness))

    def save_preset_values(self, name, kelvin, brightness):
        return self._app.save_custom_preset(str(name), int(kelvin), int(brightness))

    def reset_presets_to_default(self):
        return self._app.reset_presets_to_default()

    def reset_presets_default(self):
        return self._app.reset_presets_to_default()

    def reset_natural(self):
        return self._app.reset_natural()

    def disable_filter_temporarily(self, minutes=60):
        return self._app.disable_filter_temporarily(int(minutes))

    def set_reading_theme(self, mode, custom_bg=None, custom_txt=None):
        return self._app.set_reading_theme(mode, custom_bg, custom_txt)

    def set_reading_theme_scope(self, scope, target_app=None):
        return self._app.set_reading_theme_scope(scope, target_app)

    def get_reading_theme_state(self):
        return self._app.get_reading_theme_state()

    def get_running_reader_apps(self):
        return self._app.get_reading_theme_state()

    def set_color_filter_mode(self, mode):
        return self._app.set_color_filter_mode(str(mode))

    def toggle_grayscale(self, enable):
        self._app.toggle_grayscale(bool(enable))

    def toggle_invert(self, enable):
        self._app.toggle_invert(bool(enable))

    def toggle_extended_range(self, enable):
        self._app.config_mgr.set("screen", "unlock_extended_range", bool(enable))

    def toggle_extended(self, enable=None):
        en = (
            bool(enable)
            if enable is not None
            else not self._app.config_mgr.get(
                "screen", "unlock_extended_range", default=True
            )
        )
        self._app.config_mgr.set("screen", "unlock_extended_range", en)

    # --- Focus Tab API (Reading Focus + Spotlight Focus) ---
    def toggle_focus_ruler(self):
        return self._app.toggle_focus_ruler()

    def toggle_ruler(self, enable=None):
        if enable is True:
            self._app.focus_ruler.enable()
        elif enable is False:
            self._app.focus_ruler.disable()
        else:
            self._app.toggle_focus_ruler()

    def set_slit_height(self, h):
        self._app.set_slit_height(int(h))

    def set_slit_opacity(self, a):
        self._app.set_slit_opacity(float(a))

    def set_slit_color(self, color_hex):
        self._app.set_slit_color(str(color_hex))

    def set_spotlight_opacity(self, a):
        return self._app.set_spotlight_opacity(float(a))

    def toggle_spotlight(self, enable=None):
        en = bool(enable) if enable is not None else None
        return self._app.toggle_spotlight_focus(en)

    def configure_spotlight(
        self,
        enabled=None,
        opacity=None,
        color_hex=None,
        include_taskbar=None,
        enable_anim=None,
        anim_ms=None,
        current_monitor_only=None,
    ):
        return self._app.configure_spotlight_focus(
            enabled=enabled,
            opacity=opacity,
            color_hex=color_hex,
            include_taskbar=include_taskbar,
            enable_anim=enable_anim,
            anim_ms=anim_ms,
            current_monitor_only=current_monitor_only,
        )

    def get_focus_config(self):
        return self._app.get_focus_config()

    # --- Window Focus API (Active Window Isolation) ---
    def get_magic_config(self):
        return self._app.get_magic_config()

    def update_magic_window(
        self,
        enabled=None,
        show_toolbar=None,
        toolbar_color=None,
        placement=None,
        offset_dp=None,
        delay_sec=None,
        window_opacity=None,
        auto_dark_enabled=None,
    ):
        return self._app.update_magic_config(
            enabled=enabled,
            show_toolbar=show_toolbar,
            toolbar_color=toolbar_color,
            placement=placement,
            offset_dp=offset_dp,
            delay_sec=delay_sec,
            window_opacity=window_opacity,
            auto_dark_enabled=auto_dark_enabled,
        )

    def set_magic_app_rule(self, exe_name, mode):
        return self._app.set_magic_app_rule(str(exe_name), str(mode))

    def trigger_magic_dim(self, opacity_pct=None):
        return self._app.magic_engine.dim_foreground_window(opacity_pct, force=True)

    def toggle_magic_pin(self):
        return self._app.magic_engine.toggle_pin_foreground_window()

    def restore_magic_windows(self):
        self._app.magic_engine.restore_all_windows()
        return self._app.get_magic_config()

    # --- Break Reminder Tab API ---
    def toggle_timer_pause(self):
        self._app.toggle_timer_pause()

    def pause_timer_for(self, minutes=0):
        return self._app.pause_timer_for(int(minutes))

    def set_timer_enabled(self, enabled):
        return self._app.set_timer_enabled(bool(enabled))

    def set_break_mode(self, mode):
        return self._app.set_break_mode(str(mode))

    def preview_break_mode(self):
        self._app.preview_break_mode()

    def trigger_blink_preview(self):
        self._app.trigger_blink_preview()

    def set_blink_reminder(self, enabled):
        return self._app.set_blink_reminder(bool(enabled))

    def reset_timer(self):
        self._app.reset_timer()

    def trigger_break(self):
        self._app.trigger_break()

    def take_break_now(self, break_type="short_break", duration_sec=None):
        self._app.take_break_now(break_type, duration_sec)

    def skip_break(self, password_attempt=None):
        return self._app.skip_break(password_attempt)

    def postpone_break(self, minutes=5):
        return self._app.postpone_break(int(minutes))

    def get_timer_status(self):
        return self._app.get_timer_status()

    def configure_timer(
        self,
        work_min=25,
        break_sec=300,
        enable_20_20=True,
        enable_pomodoro=True,
        long_cycles=None,
    ):
        return self._app.configure_timer(
            work_min, break_sec, enable_20_20, enable_pomodoro, long_cycles
        )

    def update_timer_config(self, e20, idle, sound=None, dnd=None, idle_sec=None):
        return self._app.update_timer_config(
            bool(e20), bool(idle), sound, dnd, idle_sec
        )

    # --- Circadian & Options API ---
    def toggle_windows_dark_mode(self, enable):
        val = bool(enable)
        self._app.config_mgr.set("circadian", "auto_windows_dark_mode", val)
        if val:
            try:
                from core.windows_theme import set_windows_dark_mode
                st = self._app.solar_engine.get_solar_state_for_time()
                is_dark_period = (st.get("stage") in ("sunset", "bedtime", "night"))
                set_windows_dark_mode(is_dark_period)
            except Exception:
                pass
        return {"success": True, "auto_windows_dark_mode": val}

    def get_windows_dark_mode_state(self):
        try:
            from core.windows_theme import get_windows_dark_mode
            is_dark = get_windows_dark_mode()
        except Exception:
            is_dark = False
        return {
            "auto_windows_dark_mode": self._app.config_mgr.get("circadian", "auto_windows_dark_mode", default=False),
            "is_system_dark": is_dark,
        }

    def toggle_circadian(self, enable):
        return self._app.toggle_circadian(bool(enable))

    def update_circadian_schedule(
        self,
        sunrise,
        sunset,
        day_k=6500,
        night_k=3400,
        bedtime="22:30",
        bedtime_k=1900,
        day_b=100,
        night_b=80,
        bedtime_b=55,
    ):
        return self._app.update_circadian_schedule(
            str(sunrise),
            str(sunset),
            int(day_k),
            int(night_k),
            str(bedtime),
            int(bedtime_k),
            int(day_b),
            int(night_b),
            int(bedtime_b),
        )

    def get_solar_curve(self):
        return self._app.get_solar_curve()

    def toggle_startup(self, enable):
        return self._app.set_windows_startup(bool(enable))

    def get_hotkeys(self):
        return self._app.get_hotkeys_config()

    def update_hotkey(self, action_id, new_combo):
        return self._app.update_hotkey_combo(str(action_id), str(new_combo))

    def reset_default_hotkeys(self):
        return self._app.reset_default_hotkeys()

    # --- Preferences & Options Suite API ---
    def get_options_config(self):
        return self._app.get_options_config()

    def update_general_options(
        self,
        language=None,
        auto_check_updates=None,
        launch_on_startup=None,
        send_crash_reports=None,
        appearance=None,
        close_to_tray=None,
    ):
        return self._app.update_general_options(
            language=language,
            auto_check_updates=auto_check_updates,
            launch_on_startup=launch_on_startup,
            send_crash_reports=send_crash_reports,
            appearance=appearance,
            close_to_tray=close_to_tray,
        )

    def update_timer_options(self, options_dict=None):
        if isinstance(options_dict, dict):
            return self._app.update_timer_options(**options_dict)
        return self._app.get_options_config()

    def update_display_options(self, options_dict=None):
        if isinstance(options_dict, dict):
            return self._app.update_display_options(**options_dict)
        return self._app.get_options_config()

    def update_rules_options(self, disable_fullscreen=None, enable_custom_rules=None):
        return self._app.update_rules_options(disable_fullscreen, enable_custom_rules)

    def add_custom_rule(self, rule_name, trigger_exe, mode):
        return self._app.add_custom_rule(rule_name, trigger_exe, mode)

    def delete_custom_rule(self, rule_id):
        return self._app.delete_custom_rule(int(rule_id))

    def get_running_processes(self):
        return get_running_window_processes()

    def browse_file(self, kind="audio"):
        return self._app.browse_file_dialog(str(kind))

    def check_for_updates(self):
        return {
            "up_to_date": True,
            "current_version": self._app.VERSION,
            "latest_version": self._app.VERSION,
            "message": "EyeGuard is up to date.",
        }

    def check_updates_now(self):
        return self.check_for_updates()


def enforce_single_instance():
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, True, "EyeGuard_SingleInstance_Mutex")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = user32.FindWindowW(None, "EyeGuard")
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE = 9
            user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return mutex


def run_app():
    single_mutex = enforce_single_instance()
    app = EyeGuardApp()
    api = JSBridgeApi(app)

    app._setup_hotkeys()
    app.hotkey_engine.start()
    app.desktop_overlays.start()
    if app.magic_engine.auto_dark_enabled or app.magic_engine.disable_fullscreen:
        app.magic_engine.start()
    app.tray_mgr.start()
    app.system_events.start()
    app.timer_engine.start()
    app.gamma_engine.set_display(
        app.current_k, app.current_b, app.is_gray, app.is_invert, smooth=False, color_mode=app.color_filter_mode
    )
    app._schedule_tray_update()

    candidate_paths = [
        os.path.join(BASE_DIR, "web", "index.html"),
        os.path.join(BASE_DIR, "_internal", "web", "index.html"),
        os.path.join(os.path.dirname(sys.executable), "web", "index.html"),
        os.path.join(os.path.dirname(sys.executable), "_internal", "web", "index.html"),
        os.path.join(os.path.dirname(sys.executable), "web", "index.html"),
    ]
    html_file = next(
        (p for p in candidate_paths if os.path.exists(p)), candidate_paths[0]
    )

    app._window = webview.create_window(
        title="EyeGuard",
        url=html_file,
        js_api=api,
        width=820,
        height=640,
        min_size=(760, 560),
        background_color="#181b20",
    )

    def on_closing():
        if not app._closing:
            close_to_tray = bool(
                app.config_mgr.get("general", "close_to_tray", default=True)
            )
            if not close_to_tray:
                threading.Timer(0.05, app.hard_exit).start()
                return True
            threading.Timer(0.05, app.hide_window).start()
            app.tray_mgr.notify(
                "EyeGuard Active in Background",
                "Minimized to the system tray to protect your eyes.",
            )
            return False
        return True

    app._window.events.closing += on_closing

    webview.start(storage_path=app.data_dir)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset", "-r"):
        from core.crash_guard import emergency_restore_display
        emergency_restore_display()
        print("EyeGuard: Screen reset to factory identity (6500K / 100%).")
        sys.exit(0)
    run_app()
