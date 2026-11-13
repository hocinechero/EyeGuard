import json
import os
import sys
import tempfile
import threading
from pathlib import Path

DEFAULT_CONFIG = {
    "version": "3.0.0",
    "screen": {
        "enabled": True,
        "mode": "Health",
        "kelvin": 5000,
        "brightness": 85,
        "saturation": 100,
        "hardware_brightness": 85,
        "unlock_extended_range": True,
        "wider_brightness_range": True,
        "link_hardware_brightness": False,
        "grayscale": False,
        "invert": False,
        "sync_monitors": True,
        "smooth_transition": True,
        "software_filter_mode": False,
        "standard_calibration_mode": True,
        "software_mouse_cursor": False,
        "keep_background_green": False,
        "deep_crimson": False,
        "tint_cursor": True
    },
    "presets": {
        "Health": {"kelvin": 5000, "brightness": 85},
        "Office": {"kelvin": 5500, "brightness": 90},
        "Reading": {"kelvin": 4000, "brightness": 95},
        "Editing": {"kelvin": 6500, "brightness": 100},
        "Cinema": {"kelvin": 5800, "brightness": 90},
        "Gaming": {"kelvin": 6500, "brightness": 100},
        "Bedtime": {"kelvin": 2700, "brightness": 65},
        "Biohacker": {"kelvin": 0, "brightness": 50}
    },
    "timer": {
        "enabled": True,
        "enable_pomodoro": True,
        "work_minutes": 45,
        "short_break_minutes": 5,
        "long_break_minutes": 15,
        "long_break_interval": 0,
        "enable_20_20_rule": True,
        "auto_pause_on_idle": True,
        "idle_threshold_seconds": 180,
        "restart_on_idle": True,
        "restart_idle_minutes": 15,
        "restart_on_unlock": True,
        "reminder_popup_pos": "top_center",
        "pre_break_prompt_sec": 10,
        "allow_delay_timer": True,
        "max_delay_times": 3,
        "postpone_minutes": 5,
        "require_hold_3s_skip": True,
        "break_mode": "fullscreen",
        "blink_reminder": False,
        "sound_enabled": True,
        "dnd_fullscreen": True,
        "strict_mode": False,
        "show_mini_window": False,
        "mini_window_style": "progress_bar",
        "mini_window_translucent": True,
        "mini_window_opacity": 100,
        "lock_screen_24h_clock": True,
        "random_lock_wallpaper": True,
        "custom_lock_tips": False,
        "custom_lock_tips_text": "Close your eyes and breathe deeply to relax your ocular muscles.",
        "lock_screen_transparency": 100,
        "lock_screen_fade": "fast",
        "use_custom_wallpaper": False,
        "custom_wallpaper_path": "",
        "lock_windows_end_break": False,
        "lock_windows_end_short_break": False,
        "use_password_protection": False,
        "break_password": "",
        "play_alert_sound_end": True,
        "alert_sound_path": r"C:\Windows\Media\chimes.wav",
        "play_music_during_break": False,
        "break_music_path": ""
    },
    "focus_ruler": {
        "enabled": False,
        "slit_height": 300,
        "opacity": 0.50,
        "color_hex": "#000000",
        "spotlight_enabled": False,
        "spotlight_opacity": 0.60,
        "spotlight_color": "#000000",
        "include_taskbar": False,
        "enable_anim": True,
        "anim_ms": 200,
        "current_monitor_only": True
    },
    "magic_x": {
        "enabled": False,
        "show_toolbar": False,
        "toolbar_color": "#7c3aed",
        "placement": "center",
        "offset_dp": 0,
        "delay_sec": 0.50,
        "window_opacity": 85,
        "auto_dark_enabled": False,
        "app_rules": {
            "notepad.exe": "dark",
            "winword.exe": "warm",
            "acrord32.exe": "grayscale",
            "photoshop.exe": "exclude"
        }
    },
    "circadian": {
        "enabled": False,
        "sunrise": "06:30",
        "sunset": "18:30",
        "bedtime": "22:30",
        "day_kelvin": 6500,
        "night_kelvin": 3400,
        "bedtime_kelvin": 1900,
        "day_brightness": 100,
        "night_brightness": 80,
        "bedtime_brightness": 55,
        "auto_windows_dark_mode": False
    },
    "hotkeys": {
        "brightness_up": "Ctrl + Alt + Up",
        "brightness_down": "Ctrl + Alt + Down",
        "kelvin_warmer": "Ctrl + Alt + Right",
        "kelvin_cooler": "Ctrl + Alt + Left",
        "reset_natural": "Ctrl + Alt + D",
        "toggle_ruler": "Ctrl + Alt + F",
        "toggle_spotlight": "Ctrl + Alt + S",
        "magic_dark": "Ctrl + Alt + M",
        "toggle_gray": "Ctrl + Alt + G",
        "toggle_invert": "Ctrl + Alt + I",
        "toggle_timer": "Ctrl + Alt + P",
        "take_break": "Ctrl + Alt + B"
    },
    "general": {
        "language": "en",
        "close_to_tray": True,
        "launch_on_startup": False,
        "auto_check_updates": False,
        "send_crash_reports": False,
        "appearance": "dark"
    },
    "custom_rules": {
        "disable_fullscreen": False,
        "enable_custom_rules": True,
        "rules_list": [
            {"id": 1, "rule": "Notepad Dark Reading", "trigger": "notepad.exe", "mode": "dark"},
            {"id": 2, "rule": "Word Office Comfort", "trigger": "winword.exe", "mode": "warm"},
            {"id": 3, "rule": "Acrobat E-Ink Paper", "trigger": "acrord32.exe", "mode": "grayscale"},
            {"id": 4, "rule": "Photoshop Color Accuracy", "trigger": "photoshop.exe", "mode": "exclude"}
        ]
    }
}


def _deep_copy_default():
    return json.loads(json.dumps(DEFAULT_CONFIG))


def _is_running_unit_tests() -> bool:
    if getattr(sys, "frozen", False):
        return False
    return ("unittest" in sys.modules) and any(
        "test" in str(arg).lower() or "unittest" in str(arg).lower() for arg in sys.argv
    )


class ConfigManager:
    def __init__(self, config_dir=None):
        if config_dir is None:
            if _is_running_unit_tests():
                self.config_dir = Path(tempfile.gettempdir()) / "EyeGuard_TestConfig"
            else:
                self.config_dir = Path(os.environ.get("APPDATA", ".")) / "EyeGuard"
        else:
            self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self._save_timer = None
        self._lock = threading.Lock()
        self.data = self.load()

    def load(self):
        if not self.config_path.exists():
            fresh = _deep_copy_default()
            self._write_to_disk(fresh)
            return fresh
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            config = _deep_copy_default()
            for section, values in saved.items():
                if isinstance(values, dict) and section in config and isinstance(config[section], dict):
                    config[section].update(values)
                else:
                    config[section] = values

            # Respect extended ranges and user-saved modes on startup
            scr = config.get("screen", {})
            k = int(scr.get("kelvin", 5000))
            b = int(scr.get("brightness", 85))
            ext_k = bool(scr.get("unlock_extended_range", True))
            ext_b = bool(scr.get("wider_brightness_range", True))
            min_k, max_k = (0, 6500) if ext_k else (1900, 6500)
            min_b = 1 if ext_b else 25
            scr["kelvin"] = max(min_k, min(max_k, k))
            scr["brightness"] = max(min_b, min(100, b))
            scr["saturation"] = max(0, min(100, int(scr.get("saturation", 100))))
            scr["grayscale"] = bool(scr.get("grayscale", False))
            scr["invert"] = bool(scr.get("invert", False))
            scr["deep_crimson"] = bool(scr.get("deep_crimson", False))
            scr["tint_cursor"] = bool(scr.get("tint_cursor", True))
            if "general" in config and isinstance(config["general"], dict):
                config["general"]["language"] = "en"
            if "timer" in config and isinstance(config["timer"], dict):
                tip_txt = str(config["timer"].get("custom_lock_tips_text", ""))
                if any("\u0600" <= ch <= "\u06FF" for ch in tip_txt):
                    config["timer"]["custom_lock_tips_text"] = (
                        "Close your eyes and breathe deeply to relax your ocular muscles."
                    )
            presets_cfg = config.get("presets")
            if isinstance(presets_cfg, dict):
                bio = presets_cfg.get("Biohacker")
                if isinstance(bio, dict) and bio.get("kelvin") == 1000 and bio.get("brightness") == 50:
                    bio["kelvin"] = 0
                    if "k" in bio:
                        bio["k"] = 0
                    self._write_to_disk(config)
            return config
        except Exception:
            return _deep_copy_default()

    def _write_to_disk(self, data_to_write=None):
        with self._lock:
            payload = data_to_write if data_to_write is not None else self.data
            try:
                temp_path = self.config_path.with_suffix(".tmp")
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                if temp_path.exists():
                    temp_path.replace(self.config_path)
            except Exception:
                pass

    def save(self, data=None):
        if data is not None:
            self.data = data
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.8, self._write_to_disk)
            self._save_timer.daemon = True
            self._save_timer.start()

    def get(self, *keys, default=None):
        curr = self.data
        for k in keys:
            if not isinstance(k, (str, int, float, bool)):
                return default
            if isinstance(curr, dict) and k in curr:
                curr = curr[k]
            else:
                return default
        return curr

    def set(self, *keys_and_val):
        if len(keys_and_val) < 2:
            return
        keys = keys_and_val[:-1]
        val = keys_and_val[-1]
        curr = self.data
        for k in keys[:-1]:
            if k not in curr or not isinstance(curr[k], dict):
                curr[k] = {}
            curr = curr[k]
        curr[keys[-1]] = val
        self.save()
