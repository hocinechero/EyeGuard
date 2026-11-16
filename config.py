import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

DEFAULT_CONFIG = {
    "version": "4.0.1",
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
    "custom_presets": {},
    "timer": {
        "enabled": True,
        "enable_pomodoro": True,
        "work_minutes": 45,
        "short_break_minutes": 5,
        "short_break_seconds": 300,
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
        "current_monitor_only": True,
        "spotlight_scope": "current",
        "spotlight_target_monitors": [],
        "spotlight_other_monitors_100": True,
        "spotlight_pause_on_fullscreen": False
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
        self.backup_dir = self.config_dir / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "config.json"
        self.backup_config_path = self.config_dir / "config.bak.json"
        self.presets_backup_path = self.backup_dir / "presets_auto_backup.json"
        self._recovered_from_backup = False
        self._save_timer = None
        self._lock = threading.Lock()
        self.data = self.load()

    @staticmethod
    def _atomic_write_json(target_path: Path, payload: dict) -> bool:
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = target_path.with_suffix(target_path.suffix + ".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            if temp_path.exists():
                temp_path.replace(target_path)
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _read_json_safe(path: Path):
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                parsed = json.load(f)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    @staticmethod
    def _normalize_preset_entry(vals, default_k=5000, default_b=85) -> dict:
        if not isinstance(vals, dict):
            vals = {}
        kv = max(0, min(6500, int(vals.get("kelvin", vals.get("k", default_k)))))
        bv = max(1, min(100, int(vals.get("brightness", vals.get("b", default_b)))))
        return {"kelvin": kv, "brightness": bv, "k": kv, "b": bv}

    @classmethod
    def _normalize_presets_map(cls, raw_map: dict, defaults_map: dict = None) -> dict:
        out = {}
        if defaults_map:
            for key, dvals in defaults_map.items():
                out[key] = cls._normalize_preset_entry(dvals)
        if isinstance(raw_map, dict):
            for key, vals in raw_map.items():
                if isinstance(vals, dict) and str(key).strip():
                    out[str(key).strip()] = cls._normalize_preset_entry(vals)
        return out

    def load(self):
        saved = self._read_json_safe(self.config_path)
        if saved is None:
            saved = self._read_json_safe(self.backup_config_path)
            if saved is not None:
                self._recovered_from_backup = True
        if saved is None:
            preset_bak = self._read_json_safe(self.presets_backup_path)
            if preset_bak is not None:
                self._recovered_from_backup = True
                saved = {}
                if isinstance(preset_bak.get("presets"), dict):
                    saved["presets"] = preset_bak["presets"]
                if isinstance(preset_bak.get("custom_presets"), dict):
                    saved["custom_presets"] = preset_bak["custom_presets"]
                if isinstance(preset_bak.get("focus_ruler"), dict):
                    saved["focus_ruler"] = preset_bak["focus_ruler"]
        if saved is None:
            fresh = _deep_copy_default()
            fresh["presets"] = self._normalize_presets_map(fresh["presets"], DEFAULT_CONFIG["presets"])
            self._write_to_disk(fresh)
            return fresh

        try:
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

            config["presets"] = self._normalize_presets_map(
                config.get("presets", {}), DEFAULT_CONFIG["presets"]
            )
            config["custom_presets"] = self._normalize_presets_map(
                config.get("custom_presets", {})
            )
            bio = config["presets"].get("Biohacker")
            dirty = self._recovered_from_backup
            if isinstance(bio, dict) and bio.get("kelvin") == 1000 and bio.get("brightness") == 50:
                config["presets"]["Biohacker"] = self._normalize_preset_entry({"kelvin": 0, "brightness": 50})
                dirty = True

            # Synchronize spotlight multi-monitor scope safely for upgraded configs
            fr = config.get("focus_ruler")
            if isinstance(fr, dict):
                saved_fr = saved.get("focus_ruler", {}) if isinstance(saved.get("focus_ruler"), dict) else {}
                if "spotlight_scope" not in saved_fr and "current_monitor_only" in saved_fr:
                    fr["spotlight_scope"] = "current" if saved_fr.get("current_monitor_only", True) else "all_monitors"
                scope = str(fr.get("spotlight_scope", "current")).strip().lower()
                if scope not in ("current", "all_monitors", "selected"):
                    scope = "current"
                fr["spotlight_scope"] = scope
                fr["current_monitor_only"] = (scope == "current")
                targets = fr.get("spotlight_target_monitors", [])
                fr["spotlight_target_monitors"] = [str(m) for m in targets if m] if isinstance(targets, list) else []

            if dirty:
                self._write_to_disk(config)
            return config
        except Exception:
            return _deep_copy_default()

    def _write_to_disk(self, data_to_write=None):
        with self._lock:
            payload = data_to_write if data_to_write is not None else self.data
            # Preserve the last known-good config.json in config.bak.json before overwriting
            if self.config_path.exists():
                existing = self._read_json_safe(self.config_path)
                if existing is not None:
                    self._atomic_write_json(self.backup_config_path, existing)
            self._atomic_write_json(self.config_path, payload)

    def flush(self):
        with self._lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
        self._write_to_disk()

    def write_presets_backup(self, reason: str = "auto") -> dict:
        self.flush()
        snap = {
            "version": DEFAULT_CONFIG["version"],
            "reason": str(reason),
            "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "presets": self._normalize_presets_map(self.get("presets", default={}), DEFAULT_CONFIG["presets"]),
            "custom_presets": self._normalize_presets_map(self.get("custom_presets", default={})),
            "focus_ruler": dict(self.get("focus_ruler", default={}) or {}),
        }
        ok = self._atomic_write_json(self.presets_backup_path, snap)
        return {
            "ok": ok,
            "path": str(self.presets_backup_path),
            "saved_at": snap["saved_at"],
            "preset_count": len(snap["presets"]),
            "custom_count": len(snap["custom_presets"]),
        }

    def export_presets_backup(self, dest_path=None) -> dict:
        if dest_path is None:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            target = self.backup_dir / f"EyeGuard_Presets_Backup_{stamp}.json"
        else:
            target = Path(dest_path)
        self.flush()
        snap = {
            "version": DEFAULT_CONFIG["version"],
            "reason": "manual_export",
            "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "presets": self._normalize_presets_map(self.get("presets", default={}), DEFAULT_CONFIG["presets"]),
            "custom_presets": self._normalize_presets_map(self.get("custom_presets", default={})),
            "focus_ruler": dict(self.get("focus_ruler", default={}) or {}),
        }
        ok = self._atomic_write_json(target, snap)
        self._atomic_write_json(self.presets_backup_path, snap)
        return {
            "ok": ok,
            "path": str(target),
            "saved_at": snap["saved_at"],
            "preset_count": len(snap["presets"]),
            "custom_count": len(snap["custom_presets"]),
        }

    def restore_presets_backup(self, source_path=None) -> dict:
        candidates = []
        if source_path:
            candidates.append(Path(source_path))
        else:
            candidates.extend([self.presets_backup_path, self.backup_config_path])

        recovered = None
        used_path = None
        for cand in candidates:
            parsed = self._read_json_safe(cand)
            if parsed and (isinstance(parsed.get("presets"), dict) or isinstance(parsed.get("custom_presets"), dict)):
                recovered = parsed
                used_path = cand
                break

        if not recovered:
            return {"ok": False, "error": "No valid preset backup found."}

        restored_presets = self._normalize_presets_map(
            recovered.get("presets", {}), DEFAULT_CONFIG["presets"]
        )
        restored_custom = self._normalize_presets_map(recovered.get("custom_presets", {}))
        self.data["presets"] = restored_presets
        self.data["custom_presets"] = restored_custom
        self.flush()
        return {
            "ok": True,
            "source": str(used_path),
            "saved_at": recovered.get("saved_at", ""),
            "presets": restored_presets,
            "custom_presets": restored_custom,
        }

    def get_presets_backup_info(self) -> dict:
        auto_snap = self._read_json_safe(self.presets_backup_path)
        return {
            "has_auto_backup": auto_snap is not None,
            "has_config_backup": self.backup_config_path.exists(),
            "backup_path": str(self.presets_backup_path),
            "backup_dir": str(self.backup_dir),
            "saved_at": auto_snap.get("saved_at", "") if auto_snap else "",
            "recovered_on_boot": self._recovered_from_backup,
            "preset_count": len(self.get("presets", default={}) or {}),
            "custom_count": len(self.get("custom_presets", default={}) or {}),
        }

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
