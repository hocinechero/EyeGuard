import ctypes
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as cfg_mod
from core import crash_guard, gamma_engine, system_events, window_focus
from core.desktop_overlays import DesktopOverlayManager
from core.solar_engine import SolarEngine
from ui.tray_manager import TrayManager


class TestKelvinAndOptics(unittest.TestCase):
    def test_identity_at_6500k(self):
        self.assertEqual(gamma_engine.kelvin_to_rgb_extended(6500), (1.0, 1.0, 1.0))

    def test_deep_ruby_at_0k_and_ember_at_1000k(self):
        self.assertEqual(gamma_engine.kelvin_to_rgb_extended(0), (1.0, 0.0, 0.0))
        r, g, b = gamma_engine.kelvin_to_rgb_extended(1000)
        self.assertEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.266, places=6)
        self.assertEqual(b, 0.0)

    def test_extended_zero_kelvin_darkroom(self):
        self.assertEqual(gamma_engine.kelvin_to_rgb_extended(0), (1.0, 0.0, 0.0))
        for k in (250, 500, 750, 1000):
            r, g, b = gamma_engine.kelvin_to_rgb_extended(k)
            self.assertEqual(r, 1.0)
            self.assertGreater(g, 0.0)
            self.assertEqual(b, 0.0)
        self.assertEqual(gamma_engine.kelvin_to_rgb_extended(0, "cie"), (1.0, 0.0, 0.0))
        self.assertEqual(gamma_engine.kelvin_to_rgb_extended(505, "cie"), (1.0, 0.0, 0.0))

    def test_melanopic_blue_suppression_targets(self):
        # 5000K should deliver substantial blue cut (~0.52 signal vs old 0.808)
        r5, g5, b5 = gamma_engine.kelvin_to_rgb_extended(5000)
        self.assertEqual(r5, 1.0)
        self.assertLessEqual(b5, 0.55)
        self.assertGreater(g5, b5)

        r4, g4, b4 = gamma_engine.kelvin_to_rgb_extended(4000)
        self.assertLessEqual(b4, 0.36)

        r27, g27, b27 = gamma_engine.kelvin_to_rgb_extended(2700)
        self.assertLessEqual(b27, 0.08)

        r19, g19, b19 = gamma_engine.kelvin_to_rgb_extended(1900)
        self.assertEqual(b19, 0.0)
        self.assertGreater(g19, 0.4)

    def test_strict_monotonic_across_full_range(self):
        prev = (-1.0, -1.0, -1.0)
        for k in range(0, 6501, 100):
            r, g, b = gamma_engine.kelvin_to_rgb_extended(k)
            self.assertEqual(r, 1.0)
            self.assertGreaterEqual(g, prev[1])
            self.assertGreaterEqual(b, prev[2])
            prev = (r, g, b)

        prev_g = -1.0
        for k in (0, 250, 500, 750, 1000):
            r, g, b = gamma_engine.kelvin_to_rgb_extended(k)
            self.assertEqual(r, 1.0)
            self.assertEqual(b, 0.0)
            self.assertGreater(g, prev_g)
            prev_g = g
        # Continuity at 1000K and 1900K boundaries
        self.assertAlmostEqual(gamma_engine.kelvin_to_rgb_extended(1000)[1], 0.266, places=6)
        self.assertAlmostEqual(gamma_engine.kelvin_to_rgb_extended(1900)[1], 0.440, places=6)


class TestHybridDisplayEngine(unittest.TestCase):
    def _make_mocked_engine(self, lut_accepts=True, readback_clamps=False, low_lut_floor=None):
        linear = gamma_engine.GAMMA_RAMP()
        for i in range(256):
            v = (i << 8) | i
            linear.Red[i] = v
            linear.Green[i] = v
            linear.Blue[i] = v

        mock_gdi = MagicMock()
        mock_gdi.CreateDCW.return_value = 101
        mock_gdi.DeleteDC.return_value = 1
        mock_gdi.SetDeviceGammaRamp.return_value = 1 if lut_accepts else 0
        last_ramp = {"ramp": None}

        def fake_set(hdc, lp_ramp):
            if not lut_accepts:
                return 0
            ramp = ctypes.cast(lp_ramp, ctypes.POINTER(gamma_engine.GAMMA_RAMP)).contents
            copy = gamma_engine.GAMMA_RAMP()
            ctypes.memmove(ctypes.byref(copy), ctypes.byref(ramp), ctypes.sizeof(gamma_engine.GAMMA_RAMP))
            last_ramp["ramp"] = copy
            return 1

        def fake_get(hdc, lp_ramp):
            out = ctypes.cast(lp_ramp, ctypes.POINTER(gamma_engine.GAMMA_RAMP)).contents
            if readback_clamps:
                ctypes.memmove(ctypes.byref(out), ctypes.byref(linear), ctypes.sizeof(gamma_engine.GAMMA_RAMP))
            elif last_ramp["ramp"] is not None:
                ctypes.memmove(ctypes.byref(out), ctypes.byref(last_ramp["ramp"]), ctypes.sizeof(gamma_engine.GAMMA_RAMP))
                if low_lut_floor is not None:
                    floor_w = int(65535 * float(low_lut_floor))
                    if max(out.Red[255], out.Green[255], out.Blue[255]) < floor_w:
                        out.Red[255] = max(out.Red[255], floor_w)
                        out.Green[255] = max(out.Green[255], floor_w)
                        out.Blue[255] = max(out.Blue[255], floor_w)
            else:
                ctypes.memmove(ctypes.byref(out), ctypes.byref(linear), ctypes.sizeof(gamma_engine.GAMMA_RAMP))
            return 1

        mock_gdi.SetDeviceGammaRamp.side_effect = fake_set
        mock_gdi.GetDeviceGammaRamp.side_effect = fake_get

        mock_user = MagicMock()
        mock_user.GetDC.return_value = 202
        mock_user.ReleaseDC.return_value = 1
        mock_user.PeekMessageW.return_value = 0

        mock_mag = MagicMock()
        mock_mag.MagInitialize.return_value = 1
        mock_mag.MagUninitialize.return_value = 1
        last_mat = {"mat": None}

        def fake_mag_effect(lp_mat):
            mat = ctypes.cast(lp_mat, ctypes.POINTER(gamma_engine.MAGCOLOREFFECT)).contents
            copy = gamma_engine.MAGCOLOREFFECT()
            ctypes.memmove(ctypes.byref(copy), ctypes.byref(mat), ctypes.sizeof(gamma_engine.MAGCOLOREFFECT))
            last_mat["mat"] = copy
            return 1

        mock_mag.MagSetFullscreenColorEffect.side_effect = fake_mag_effect

        p1 = patch.object(gamma_engine, "gdi32", mock_gdi)
        p2 = patch.object(gamma_engine, "user32", mock_user)
        p3 = patch.object(gamma_engine, "mag32", mock_mag)
        p4 = patch.object(gamma_engine, "_HAS_MAGNIFICATION", True)
        p5 = patch.object(
            gamma_engine.EyeGuardGammaEngine,
            "get_monitors",
            return_value=[{"device": r"\\.\DISPLAY1", "rect": (0, 0, 1920, 1080), "primary": True}],
        )
        for p in (p1, p2, p3, p4, p5):
            p.start()
            self.addCleanup(p.stop)

        eng = gamma_engine.EyeGuardGammaEngine()
        self.addCleanup(eng.shutdown)
        return eng, last_ramp, last_mat, mock_gdi, mock_mag

    def test_pure_hardware_lut_normal_mode_above_20pct(self):
        eng, last_ramp, last_mat, mock_gdi, mock_mag = self._make_mocked_engine()
        eng.set_display(4500, 80, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hardware_lut")
        self.assertFalse(mock_mag.MagSetFullscreenColorEffect.called)
        self.assertIsNotNone(last_ramp["ramp"])
        # Blue channel at [255] must be significantly suppressed at 4500K & 80% brightness
        self.assertLess(last_ramp["ramp"].Blue[255], int(65535 * 0.45 * 0.80))

    def test_1pct_brightness_full_range_hardware_lut(self):
        eng, last_ramp, _, _, mock_mag = self._make_mocked_engine()
        eng.set_display(5000, 1, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertAlmostEqual(eng._current_brightness, 0.01, places=4)
        self.assertEqual(eng.get_active_backend(), "hardware_lut")
        self.assertFalse(mock_mag.MagSetFullscreenColorEffect.called)
        self.assertAlmostEqual(
            last_ramp["ramp"].Red[255] / 65535.0,
            eng.MIN_HARDWARE_FLOOR,
            delta=0.001,
        )
        # 5% must dim cleanly above MIN_HARDWARE_FLOOR (no 1%..4% flatline at 0.01)
        self.assertGreater(0.05 ** eng.PERCEPTUAL_DIM_EXPONENT, eng.MIN_HARDWARE_FLOOR)

    def test_1pct_brightness_hybrid_stacking(self):
        eng, last_ramp, last_mat, _, _ = self._make_mocked_engine(low_lut_floor=0.20)
        eng.set_display(5000, 1, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertAlmostEqual(eng._current_brightness, 0.01, places=4)
        self.assertEqual(eng.get_active_backend(), "hybrid_lut_dwm")
        # Clamped drivers stay at 0.20 LUT floor (~13107 max R), DWM stacks MIN_HARDWARE_FLOOR/0.20 = 0.0125
        expected_rem = eng.MIN_HARDWARE_FLOOR / 0.20
        self.assertAlmostEqual(last_ramp["ramp"].Red[255] / 65535.0, 0.20, delta=0.01)
        self.assertAlmostEqual(last_mat["mat"].transform[0][0], expected_rem, delta=0.005)
        self.assertAlmostEqual(last_mat["mat"].transform[1][1], expected_rem, delta=0.005)
        self.assertAlmostEqual(last_mat["mat"].transform[2][2], expected_rem, delta=0.005)

    def test_zero_kelvin_and_redundant_ramp_cache_skip(self):
        eng, last_ramp, _, mock_gdi, _ = self._make_mocked_engine()
        eng.set_display(0, 50, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hardware_lut")
        self.assertGreater(last_ramp["ramp"].Red[255], 0)
        self.assertEqual(last_ramp["ramp"].Green[255], 0)
        self.assertEqual(last_ramp["ramp"].Blue[255], 0)
        calls_after_first = mock_gdi.SetDeviceGammaRamp.call_count
        eng.set_display(0, 50, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(mock_gdi.SetDeviceGammaRamp.call_count, calls_after_first)

    def test_driver_lut_clamp_falls_back_to_dwm(self):
        eng, _, last_mat, _, mock_mag = self._make_mocked_engine(lut_accepts=True, readback_clamps=True)
        eng.set_display(3700, 50, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "software_dwm")
        self.assertTrue(mock_mag.MagSetFullscreenColorEffect.called)
        expected_eff = 0.50 ** eng.PERCEPTUAL_DIM_EXPONENT
        self.assertAlmostEqual(last_mat["mat"].transform[0][0], expected_eff, delta=0.01)

    def test_grayscale_and_invert_wired_in_dwm_matrix(self):
        eng, _, last_mat, _, _ = self._make_mocked_engine()
        eng.set_display(6500, 80, grayscale=True, invert=True, smooth=False, color_mode="normal")
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "software_dwm")
        m = last_mat["mat"].transform
        # White input (1,1,1) maps to ~0 black, black input (0,0,0) maps to perceptual 80% white
        expected_eff = 0.80 ** eng.PERCEPTUAL_DIM_EXPONENT
        self.assertAlmostEqual(m[4][0], expected_eff, delta=0.01)
        self.assertLess(m[0][0], -0.20)
        out_white = m[0][0] + m[1][0] + m[2][0] + m[4][0]
        self.assertAlmostEqual(out_white, 0.0, delta=0.02)

    def test_saturation_zero_uses_hybrid_lut_plus_dwm_and_perceptual_dimming(self):
        eng, last_ramp, last_mat, _, mock_mag = self._make_mocked_engine()
        eng.set_saturation(0)
        eng.set_display(0, 47, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hybrid_lut_dwm")
        expected_eff = 0.47 ** eng.PERCEPTUAL_DIM_EXPONENT
        # LUT applies 0K pure ruby + perceptual dimming + cursor tinting
        self.assertAlmostEqual(last_ramp["ramp"].Red[255] / 65535.0, expected_eff, delta=0.01)
        self.assertEqual(last_ramp["ramp"].Green[255], 0)
        self.assertEqual(last_ramp["ramp"].Blue[255], 0)
        # DWM applies neutral grayscale extraction (rem_b=1.0, sat=0.0)
        self.assertTrue(mock_mag.MagSetFullscreenColorEffect.called)
        m = last_mat["mat"].transform
        self.assertAlmostEqual(m[0][0], 0.299, delta=0.01)
        self.assertAlmostEqual(m[1][0], 0.587, delta=0.01)
        self.assertAlmostEqual(m[2][0], 0.114, delta=0.01)

    def test_custom_mode_without_prior_setter_and_clean_reset_uninit_dwm(self):
        eng, _, last_mat, _, mock_mag = self._make_mocked_engine()
        eng.set_display(4500, 75, smooth=False, color_mode="custom")
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "software_dwm")
        self.assertTrue(mock_mag.MagInitialize.called)

        eng.reset()
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.color_mode, "normal")
        self.assertEqual(eng.get_active_backend(), "identity")
        self.assertTrue(mock_mag.MagUninitialize.called)

    def test_deep_crimson_pure_hardware_lut_and_reset(self):
        eng, last_ramp, _, _, mock_mag = self._make_mocked_engine()
        eng.set_display(4000, 50, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        eng.set_deep_crimson(True)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hardware_lut")
        self.assertFalse(mock_mag.MagSetFullscreenColorEffect.called)
        for i in range(256):
            self.assertEqual(last_ramp["ramp"].Green[i], 0)
            self.assertEqual(last_ramp["ramp"].Blue[i], 0)
        eff_b = 0.50 ** eng.PERCEPTUAL_DIM_EXPONENT
        exp_128 = int(round(((128 / 255.0) ** 1.55) * 0.78 * 65535.0 * eff_b))
        exp_255 = int(round(0.78 * 65535.0 * eff_b))
        self.assertEqual(last_ramp["ramp"].Red[128], exp_128)
        self.assertEqual(last_ramp["ramp"].Red[255], exp_255)
        self.assertLess(last_ramp["ramp"].Red[128], last_ramp["ramp"].Red[255] // 2)

        # is_clean_identity must stay off at 6500K / 100% when deep_crimson=True
        eng.set_display(6500, 100, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hardware_lut")
        self.assertFalse(mock_mag.MagSetFullscreenColorEffect.called)
        self.assertEqual(last_ramp["ramp"].Green[255], 0)
        self.assertEqual(last_ramp["ramp"].Blue[255], 0)
        self.assertEqual(last_ramp["ramp"].Red[255], int(round(0.78 * 65535.0)))

        # reset() clears deep_crimson back to identity
        eng.reset()
        self.assertTrue(eng.wait_idle(2.0))
        self.assertFalse(eng.deep_crimson)
        self.assertEqual(eng.get_active_backend(), "identity")

    def test_deep_crimson_with_invert_uses_dwm_matrix(self):
        eng, _, last_mat, _, mock_mag = self._make_mocked_engine()
        eng.set_deep_crimson(True)
        eng.set_display(6500, 80, invert=True, smooth=False, color_mode="normal")
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "software_dwm")
        self.assertTrue(mock_mag.MagSetFullscreenColorEffect.called)
        m = last_mat["mat"].transform
        for r in range(5):
            self.assertEqual(m[r][1], 0.0)
            self.assertEqual(m[r][2], 0.0)
        expected_eff = 0.80 ** eng.PERCEPTUAL_DIM_EXPONENT
        self.assertAlmostEqual(m[0][0], -0.78 * expected_eff, delta=0.01)
        self.assertAlmostEqual(m[4][0], 0.78 * expected_eff, delta=0.01)
        out_white_r = m[0][0] + m[1][0] + m[2][0] + m[4][0]
        self.assertAlmostEqual(out_white_r, 0.0, delta=0.02)

    def test_deep_crimson_hybrid_lut_dwm_does_not_double_attenuate(self):
        eng, last_ramp, last_mat, _, mock_mag = self._make_mocked_engine()
        eng.set_saturation(50)
        eng.set_deep_crimson(True)
        eng.set_display(0, 50, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertEqual(eng.get_active_backend(), "hybrid_lut_dwm")
        self.assertTrue(mock_mag.MagSetFullscreenColorEffect.called)
        eff_b = 0.50 ** eng.PERCEPTUAL_DIM_EXPONENT
        self.assertEqual(last_ramp["ramp"].Red[255], int(round(0.78 * 65535.0 * eff_b)))
        self.assertEqual(last_ramp["ramp"].Green[255], 0)
        self.assertEqual(last_ramp["ramp"].Blue[255], 0)
        m = last_mat["mat"].transform
        # DWM must not re-apply 0.78 on top of LUT's 0.78; white input R gain stays 1.0
        out_white_r = m[0][0] + m[1][0] + m[2][0] + m[4][0]
        self.assertAlmostEqual(out_white_r, 1.0, delta=0.02)

    def test_crash_guard_cmd_frozen_uses_flag(self):
        eng, _, _, _, _ = self._make_mocked_engine()
        with patch.object(sys, "frozen", True, create=True):
            cmd = eng._build_crash_guard_cmd()
            self.assertEqual(cmd[1], "--crash-guard")
            self.assertEqual(cmd[2], str(os.getpid()))


class TestCrashGuardSafety(unittest.TestCase):
    def test_emergency_restore_does_not_blast_hw_brightness_by_default(self):
        mock_user = MagicMock()
        mock_gdi = MagicMock()
        mock_mag = MagicMock()
        mock_dxva2 = MagicMock()
        with patch.object(crash_guard, "user32", mock_user), \
             patch.object(crash_guard, "gdi32", mock_gdi), \
             patch("ctypes.windll.magnification", mock_mag, create=True), \
             patch("ctypes.windll.dxva2", mock_dxva2, create=True):
            crash_guard.emergency_restore_display(reset_hw_brightness=False)
            self.assertFalse(mock_dxva2.SetMonitorBrightness.called)
            self.assertTrue(mock_gdi.SetDeviceGammaRamp.called)


class TestOverlaysRulerFocusAndTray(unittest.TestCase):
    def test_is_foreground_fullscreen_accepts_zero_or_one_arg(self):
        with patch.object(window_focus.user32, "GetForegroundWindow", return_value=0):
            self.assertFalse(window_focus.is_foreground_fullscreen())
            self.assertFalse(window_focus.is_foreground_fullscreen(0))

    def test_toolhelp_process_check(self):
        self.assertFalse(window_focus.is_any_process_running({"definitely_not_running_eyeguard_test_999.exe"}))

    def test_overlay_manager_start_stop_clean(self):
        mgr = DesktopOverlayManager()
        mgr.start()
        self.assertIsNotNone(mgr._thread)
        self.assertTrue(mgr._thread.is_alive())
        mgr.stop()
        self.assertFalse(mgr._thread.is_alive())

    def test_tray_icon_update_noop_when_cached_or_unchanged(self):
        tm = TrayManager(lambda: None, lambda n: None, lambda: None, lambda: None, lambda: None)
        fake_icon = MagicMock()
        tm.icon = fake_icon
        tm._using_static_asset = True
        tm.update_icon_color(200, 150, 100)
        # Should be zero-cost no-op when tray_icon.png is active
        self.assertEqual(fake_icon.mock_calls, [])


class TestConfigCircadianAndBindings(unittest.TestCase):
    def test_config_clamps_min_brightness_to_1_when_wide_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cm = cfg_mod.ConfigManager(config_dir=tmpdir)
            cm.data["screen"]["wider_brightness_range"] = True
            cm.data["screen"]["brightness"] = 1
            cm._write_to_disk()
            loaded = cm.load()
            self.assertEqual(loaded["screen"]["brightness"], 1)

            cm.data["screen"]["wider_brightness_range"] = False
            cm.data["screen"]["brightness"] = 10
            cm._write_to_disk()
            loaded_narrow = cm.load()
            self.assertEqual(loaded_narrow["screen"]["brightness"], 25)

    def test_solar_phase_dark_period_detection_matches_app_keys(self):
        solar = SolarEngine(sunrise="06:30", sunset="18:30", bedtime="22:30")
        evening_dt = datetime.datetime(2026, 1, 1, 20, 0)
        day_dt = datetime.datetime(2026, 1, 1, 12, 0)
        st_even = solar.get_solar_state_for_time(evening_dt)
        st_day = solar.get_solar_state_for_time(day_dt)
        self.assertIn("phase", st_even)
        dark_phases = ("sunset", "evening", "bedtime_transition", "bedtime", "night")
        self.assertIn(st_even["phase"], dark_phases)
        self.assertNotIn(st_day["phase"], dark_phases)

    def test_circadian_js_python_signature_match(self):
        import app as app_mod
        import inspect
        sig = list(inspect.signature(app_mod.JSBridgeApi.update_circadian_schedule).parameters.keys())
        self.assertEqual(
            sig,
            ["self", "sunrise", "sunset", "day_k", "night_k", "bedtime", "bedtime_k", "day_b", "night_b", "bedtime_b"],
        )
        html_text = (ROOT_DIR / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn(
            "sunrise, sunset, dayK, nightK, bedtime, bedK, dayB, nightB, bedB",
            html_text,
        )
        self.assertNotIn("val-saturation-hero", html_text)
        self.assertNotIn("hide_overlay(", (ROOT_DIR / "app.py").read_text(encoding="utf-8"))

    def test_percentage_vs_fraction_and_saturation_edge_cases(self):
        eng, last_ramp, _, _, _ = TestHybridDisplayEngine._make_mocked_engine(self)
        eng.set_display(5000, 0.5, smooth=False)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertAlmostEqual(eng._current_brightness, 0.5, places=4)

        eng.set_saturation(1)
        self.assertTrue(eng.wait_idle(2.0))
        self.assertAlmostEqual(eng.saturation, 0.01, places=4)

    def test_app_reset_temp_disable_and_link_hw_brightness(self):
        import app as app_mod

        with tempfile.TemporaryDirectory() as tmpdir:
            real_cm = cfg_mod.ConfigManager(config_dir=tmpdir)
            real_cm.set("screen", "keep_background_green", True)
            real_cm.set("screen", "link_hardware_brightness", True)

            with patch.object(app_mod, "ConfigManager", return_value=real_cm), \
                 patch.object(app_mod, "EyeGuardGammaEngine") as MockGamma, \
                 patch.object(app_mod, "DesktopOverlayManager"), \
                 patch.object(app_mod, "Win32FocusRuler"), \
                 patch.object(app_mod, "MagicWindowEngine"), \
                 patch.object(app_mod, "HotkeyEngine"), \
                 patch.object(app_mod, "TrayManager"), \
                 patch("core.system_events.SystemEventsMonitor"):
                mock_eng = MockGamma.return_value
                mock_eng.get_monitors.return_value = []
                mock_eng.get_hardware_brightness.return_value = 35
                mock_eng.get_active_backend.return_value = "hardware_lut"

                eg = app_mod.EyeGuardApp()
                self.addCleanup(eg.hard_exit)
                self.assertEqual(eg.current_hw_b, 35)

                eg.set_brightness(1)
                self.assertEqual(eg.current_b, 1)
                mock_eng.set_hardware_brightness.assert_called_with(1)

                eg.set_deep_crimson(True)
                self.assertTrue(eg.is_deep_crimson)
                mock_eng.set_deep_crimson.assert_called_with(True)

                paused = eg.disable_filter_temporarily(60)
                self.assertTrue(paused["disabled"])
                self.assertFalse(mock_eng.green_background)
                self.assertFalse(mock_eng.deep_crimson)

                resumed = eg.disable_filter_temporarily(0)
                self.assertFalse(resumed["disabled"])
                self.assertTrue(mock_eng.green_background)
                self.assertTrue(mock_eng.deep_crimson)

                res = eg.reset_natural()
                self.assertEqual(res, {"kelvin": 6500, "brightness": 100, "mode": "normal", "deep_crimson": False})
                self.assertFalse(eg.is_deep_crimson)
                self.assertFalse(mock_eng.deep_crimson)
                eg.desktop_overlays.hide_toast.assert_called()


if __name__ == "__main__":
    unittest.main()
