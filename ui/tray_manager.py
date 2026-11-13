import threading
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item
from typing import Callable, Optional


def get_asset_path(filename: str) -> str:
    """Resolves an asset file across PyInstaller onefile, onedir, or dev tree."""
    import sys, os
    # 1. sys._MEIPASS for PyInstaller onefile bundle
    if hasattr(sys, "_MEIPASS"):
        p = os.path.join(sys._MEIPASS, "assets", filename)
        if os.path.exists(p):
            return p
    # 2. Executable directory for PyInstaller onedir or installed application
    exe_dir = os.path.dirname(sys.executable)
    for sub in ("assets", os.path.join("_internal", "assets")):
        p = os.path.join(exe_dir, sub, filename)
        if os.path.exists(p):
            return p
    # 3. Source directory
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base_dir, "assets", filename)
    if os.path.exists(p):
        return p
    return ""


_TRAY_ASSET_CACHE = {}


def generate_eye_icon(color_rgb=(14, 165, 233), size=64):
    """
    Returns the official EyeGuard signature blue eye icon for the System Tray and Windows notifications.
    Loads tray_icon.png from assets (cached by size) or falls back to procedural vector rendering.
    """
    import os
    tray_path = get_asset_path("tray_icon.png")
    if tray_path and os.path.exists(tray_path):
        cache_key = (tray_path, size)
        cached = _TRAY_ASSET_CACHE.get(cache_key)
        if cached is not None:
            return cached
        try:
            with Image.open(tray_path) as raw_im:
                im = raw_im.convert("RGBA")
                if im.size != (size, size):
                    im = im.resize((size, size), Image.Resampling.LANCZOS)
                _TRAY_ASSET_CACHE[cache_key] = im
                return im
        except Exception:
            pass

    # Procedural EyeGuard vector eye rendering
    ss = 256
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    eye_w = ss * 0.92
    eye_h = eye_w * (30.0 / 48.0)
    eye_x0 = (ss - eye_w) / 2
    eye_y0 = (ss - eye_h) / 2

    def to_c(vx, vy):
        return (eye_x0 + vx / 48.0 * eye_w, eye_y0 + vy / 30.0 * eye_h)

    def cubic_bezier(p0, p1, p2, p3, steps=60):
        pts = []
        for i in range(steps + 1):
            t = i / steps
            u = 1.0 - t
            x = u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0]
            y = u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1]
            pts.append((x, y))
        return pts

    p_left = to_c(1.5, 15.0)
    p_top = to_c(24.0, 2.0)
    p_right = to_c(46.5, 15.0)
    p_bot = to_c(24.0, 28.0)

    top_pts = cubic_bezier(p_left, to_c(9.0, 2.2), to_c(18.0, 1.8), p_top)
    top_pts += cubic_bezier(p_top, to_c(30.0, 1.8), to_c(39.0, 2.2), p_right)[1:]
    bot_pts = cubic_bezier(p_right, to_c(39.0, 27.8), to_c(30.0, 28.2), p_bot)
    bot_pts += cubic_bezier(p_bot, to_c(18.0, 28.2), to_c(9.0, 27.8), p_left)[1:]
    poly = top_pts + bot_pts

    blue = color_rgb if (len(color_rgb) == 3 and color_rgb != (255, 255, 255)) else (14, 165, 233)
    primary = blue + (255,)

    # 1. Solid pure white sclera fill
    draw.polygon(poly, fill=(255, 255, 255, 255))

    # 2. Outer blue contour
    stroke_w = max(2, int(3.6 / 48.0 * eye_w))
    for i in range(len(poly)):
        pt1 = poly[i]
        pt2 = poly[(i + 1) % len(poly)]
        draw.line([pt1, pt2], fill=primary, width=stroke_w)
    for pt in poly[::2]:
        draw.ellipse([pt[0] - stroke_w/2, pt[1] - stroke_w/2, pt[0] + stroke_w/2, pt[1] + stroke_w/2], fill=primary)

    # 3. Vibrant Blue Iris Circle
    iris_cx, iris_cy = to_c(24.0, 15.0)
    iris_r = 9.8 / 48.0 * eye_w
    draw.ellipse([iris_cx - iris_r, iris_cy - iris_r, iris_cx + iris_r, iris_cy + iris_r], fill=primary)

    # 4. Dark Pupil Circle
    pupil_cx, pupil_cy = to_c(24.0, 14.6)
    pupil_r = 5.3 / 48.0 * eye_w
    draw.ellipse([pupil_cx - pupil_r, pupil_cy - pupil_r, pupil_cx + pupil_r, pupil_cy + pupil_r], fill=(22, 26, 32, 255))

    # 5. Brilliant White Glint Arc in upper-left quadrant
    refl_cx, refl_cy = to_c(24.0, 14.6)
    refl_r = 4.3 / 48.0 * eye_w
    refl_w = max(2, int(2.2 / 48.0 * eye_w))
    draw.arc([refl_cx - refl_r, refl_cy - refl_r, refl_cx + refl_r, refl_cy + refl_r], start=110, end=230, fill=(255, 255, 255, 255), width=refl_w)

    return img.resize((size, size), Image.Resampling.LANCZOS)


class TrayManager:
    """
    Manages the Windows System Tray notification area icon, dynamic states,
    and right-click context menu for desktop comfort and eye strain prevention.
    """

    def __init__(
        self,
        on_open_dashboard: Callable[[], None],
        on_apply_preset: Callable[[str], None],
        on_toggle_ruler: Callable[[], None],
        on_trigger_break: Callable[[], None],
        on_exit: Callable[[], None],
        on_toggle_spotlight: Optional[Callable[[], None]] = None,
        on_pause_timer: Optional[Callable[[int], None]] = None,
        on_disable_temp: Optional[Callable[[int], None]] = None,
        on_toggle_magic: Optional[Callable[[], None]] = None,
    ):
        self.on_open = on_open_dashboard
        self.on_preset = on_apply_preset
        self.on_ruler = on_toggle_ruler
        self.on_break = on_trigger_break
        self.on_exit = on_exit
        self.on_spotlight = on_toggle_spotlight
        self.on_pause_timer = on_pause_timer
        self.on_disable_temp = on_disable_temp
        self.on_toggle_magic = on_toggle_magic

        self.icon = None
        self._thread = None
        self._using_static_asset = False
        self._last_icon_rgb = None

    def start(self):
        presets_menu = pystray.Menu(
            item("Health (5000K)", lambda: self.on_preset("Health")),
            item("Office (5500K)", lambda: self.on_preset("Office")),
            item("Reading (4000K)", lambda: self.on_preset("Reading")),
            item("Bedtime (2700K)", lambda: self.on_preset("Bedtime")),
            item("Cinema (5800K)", lambda: self.on_preset("Cinema")),
            item("Gaming (6500K)", lambda: self.on_preset("Gaming")),
            item("Biohacker (1000K)", lambda: self.on_preset("Biohacker")),
            item("Natural (6500K)", lambda: self.on_preset("Natural")),
        )

        menu_items = [
            item("EyeGuard — Control Panel", self._on_double_click, default=True),
            pystray.Menu.SEPARATOR,
            item("Display Presets", presets_menu),
            item("Health Mode", lambda: self.on_preset("Health")),
            item("Office Mode", lambda: self.on_preset("Office")),
            item("Reading Mode", lambda: self.on_preset("Reading")),
            item("Bedtime Mode", lambda: self.on_preset("Bedtime")),
            item("Natural Mode", lambda: self.on_preset("Natural")),
            pystray.Menu.SEPARATOR,
            item("Reading Focus (Ruler)", self.on_ruler),
        ]

        if self.on_spotlight:
            menu_items.append(
                item("Spotlight Focus", lambda: self.on_spotlight())
            )
        if self.on_toggle_magic:
            menu_items.append(
                item("Dim Inactive Windows (Focus)", lambda: self.on_toggle_magic())
            )

        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                item("Take a Break Now", self.on_break),
            ]
        )

        if self.on_pause_timer:
            menu_items.append(
                item(
                    "Pause Timer (30 min)",
                    lambda: self.on_pause_timer(30),
                )
            )
        if self.on_disable_temp:
            menu_items.append(
                item(
                    "Disable Filter for 1 Hour",
                    lambda: self.on_disable_temp(60),
                )
            )

        menu_items.extend(
            [
                pystray.Menu.SEPARATOR,
                item("Open Control Panel", self.on_open),
                item("Exit EyeGuard", self.on_exit),
            ]
        )

        menu = pystray.Menu(*menu_items)
        self._using_static_asset = bool(get_asset_path("tray_icon.png"))
        self._last_icon_rgb = (14, 165, 233)
        initial_img = generate_eye_icon((14, 165, 233))
        self.icon = pystray.Icon(
            "EyeGuard", initial_img, "EyeGuard — by @hocine_chero", menu
        )

        self._thread = threading.Thread(target=self.icon.run, daemon=True)
        self._thread.start()

    def update_icon_color(self, r: int, g: int, b: int):
        if self.icon is None:
            return
        rgb = (int(r), int(g), int(b))
        if self._using_static_asset or self._last_icon_rgb == rgb:
            return
        try:
            new_img = generate_eye_icon(rgb)
            self.icon.icon = new_img
            self._last_icon_rgb = rgb
        except Exception:
            pass

    def notify(self, title: str, message: str):
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass

    def _on_double_click(self, icon=None, item=None):
        if self.on_open:
            self.on_open()
