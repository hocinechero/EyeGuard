import datetime
import math
from typing import Dict, List, Optional, Tuple


class SolarEngine:
    """
    Computes 3-stage circadian solar transition curves (Daytime -> Sunset -> Bedtime)
    to dynamically adapt color temperature and brightness without external network requests.
    """

    def __init__(
        self,
        sunrise_hour: int = 6,
        sunrise_min: int = 30,
        sunset_hour: int = 18,
        sunset_min: int = 30,
        bedtime_hour: int = 22,
        bedtime_min: int = 30,
        transition_duration_min: int = 45,
        sunrise: Optional[str] = None,
        sunset: Optional[str] = None,
        bedtime: Optional[str] = None,
        day_kelvin: int = 6500,
        night_kelvin: int = 3400,
        bedtime_kelvin: int = 1900,
        day_brightness: int = 100,
        night_brightness: int = 80,
        bedtime_brightness: int = 55,
    ):
        self.sunrise_hour = sunrise_hour
        self.sunrise_min = sunrise_min
        self.sunset_hour = sunset_hour
        self.sunset_min = sunset_min
        self.bedtime_hour = bedtime_hour
        self.bedtime_min = bedtime_min
        self.transition_minutes = transition_duration_min

        self.day_kelvin = int(day_kelvin)
        self.night_kelvin = int(night_kelvin)
        self.bedtime_kelvin = int(bedtime_kelvin)

        self.day_brightness = int(day_brightness)
        self.night_brightness = int(night_brightness)
        self.bedtime_brightness = int(bedtime_brightness)

        if sunrise and ":" in str(sunrise):
            try:
                p = str(sunrise).split(":")
                self.sunrise_hour, self.sunrise_min = int(p[0]), int(p[1])
            except Exception:
                pass
        if sunset and ":" in str(sunset):
            try:
                p = str(sunset).split(":")
                self.sunset_hour, self.sunset_min = int(p[0]), int(p[1])
            except Exception:
                pass
        if bedtime and ":" in str(bedtime):
            try:
                p = str(bedtime).split(":")
                self.bedtime_hour, self.bedtime_min = int(p[0]), int(p[1])
            except Exception:
                pass

        self.sunrise_str = f"{self.sunrise_hour:02d}:{self.sunrise_min:02d}"
        self.sunset_str = f"{self.sunset_hour:02d}:{self.sunset_min:02d}"
        self.bedtime_str = f"{self.bedtime_hour:02d}:{self.bedtime_min:02d}"

    def get_time_factor(self, now: Optional[datetime.datetime] = None) -> float:
        """
        Returns a factor in [0.0, 1.0]:
        1.0 = Full Daytime
        0.0 = Full Nighttime
        Values between 0.0 and 1.0 indicate smooth transition at sunrise/sunset.
        """
        if now is None:
            now = datetime.datetime.now()

        current_min = now.hour * 60 + now.minute
        sr_min = self.sunrise_hour * 60 + self.sunrise_min
        ss_min = self.sunset_hour * 60 + self.sunset_min
        half_tr = self.transition_minutes / 2.0

        # Morning transition
        if sr_min - half_tr <= current_min <= sr_min + half_tr:
            progress = (current_min - (sr_min - half_tr)) / self.transition_minutes
            return 0.5 - 0.5 * math.cos(progress * math.pi)

        # Evening transition
        if ss_min - half_tr <= current_min <= ss_min + half_tr:
            progress = (current_min - (ss_min - half_tr)) / self.transition_minutes
            return 0.5 + 0.5 * math.cos(progress * math.pi)

        # Full Day
        if (sr_min + half_tr) < current_min < (ss_min - half_tr):
            return 1.0

        # Full Night
        return 0.0

    def interpolate_values(
        self,
        day_k: Optional[int] = None,
        night_k: Optional[int] = None,
        day_b: Optional[int] = None,
        night_b: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Tuple[int, int, float]:
        dk = self.day_kelvin if day_k is None else int(day_k)
        nk = self.night_kelvin if night_k is None else int(night_k)
        db = self.day_brightness if day_b is None else int(day_b)
        nb = self.night_brightness if night_b is None else int(night_b)
        factor = self.get_time_factor(now)
        kelvin = int(round(nk + (dk - nk) * factor))
        brightness = int(round(nb + (db - nb) * factor))
        return kelvin, brightness, factor

    def get_kelvin_for_time(
        self,
        now: Optional[datetime.datetime] = None,
        day_k: Optional[int] = None,
        night_k: Optional[int] = None,
    ) -> int:
        """
        Computes the target color temperature (Kelvin) for the specified time
        along the smooth solar transition curve. When no explicit day_k/night_k
        overrides are passed, returns the 3-stage circadian curve target (Daytime / Sunset / Bedtime).
        """
        if day_k is None and night_k is None:
            return int(self.get_solar_state_for_time(now)["kelvin"])
        kelvin, _, _ = self.interpolate_values(
            day_k=day_k,
            night_k=night_k,
            day_b=self.day_brightness,
            night_b=self.night_brightness,
            now=now,
        )
        return int(kelvin)

    def get_3stage_values_for_minutes(self, current_min: float) -> Tuple[int, int, str]:
        """
        3-Stage Circadian Solar Curve:
          - Daytime (sunrise .. sunset): day_kelvin, day_brightness
          - Sunset/Evening (sunset .. bedtime): night_kelvin, night_brightness
          - Bedtime/Late Night (bedtime .. sunrise): bedtime_kelvin, bedtime_brightness
        Smooth cosine easing transitions across transition_minutes around each boundary.
        """
        sr_min = self.sunrise_hour * 60 + self.sunrise_min
        ss_min = self.sunset_hour * 60 + self.sunset_min
        bt_min = self.bedtime_hour * 60 + self.bedtime_min
        half_tr = max(15.0, float(self.transition_minutes) / 2.0)

        # Normalize bedtime if past midnight
        if bt_min <= ss_min:
            bt_min += 1440
        c_min = float(current_min)
        if c_min < sr_min - half_tr and bt_min > 1440:
            c_min += 1440

        # 1. Sunrise transition (Bedtime -> Daytime)
        if sr_min - half_tr <= current_min <= sr_min + half_tr:
            prog = (current_min - (sr_min - half_tr)) / (2.0 * half_tr)
            w = 0.5 - 0.5 * math.cos(prog * math.pi)
            k = int(round(self.bedtime_kelvin + (self.day_kelvin - self.bedtime_kelvin) * w))
            b = int(round(self.bedtime_brightness + (self.day_brightness - self.bedtime_brightness) * w))
            return k, b, "sunrise"

        # 2. Full Daytime
        if sr_min + half_tr < current_min < ss_min - half_tr:
            return self.day_kelvin, self.day_brightness, "daytime"

        # 3. Sunset transition (Daytime -> Evening/Sunset)
        if ss_min - half_tr <= current_min <= ss_min + half_tr:
            prog = (current_min - (ss_min - half_tr)) / (2.0 * half_tr)
            w = 0.5 - 0.5 * math.cos(prog * math.pi)
            k = int(round(self.day_kelvin + (self.night_kelvin - self.day_kelvin) * w))
            b = int(round(self.day_brightness + (self.night_brightness - self.day_brightness) * w))
            return k, b, "sunset"

        # 4. Evening (Sunset -> Bedtime)
        if ss_min + half_tr < c_min < bt_min - half_tr:
            return self.night_kelvin, self.night_brightness, "evening"

        # 5. Bedtime transition (Evening -> Bedtime)
        if bt_min - half_tr <= c_min <= bt_min + half_tr:
            prog = (c_min - (bt_min - half_tr)) / (2.0 * half_tr)
            w = 0.5 - 0.5 * math.cos(prog * math.pi)
            k = int(round(self.night_kelvin + (self.bedtime_kelvin - self.night_kelvin) * w))
            b = int(round(self.night_brightness + (self.bedtime_brightness - self.night_brightness) * w))
            return k, b, "bedtime_transition"

        # 6. Deep Bedtime
        return self.bedtime_kelvin, self.bedtime_brightness, "bedtime"

    def get_solar_state_for_time(
        self, now: Optional[datetime.datetime] = None
    ) -> Dict[str, object]:
        if now is None:
            now = datetime.datetime.now()
        current_min = now.hour * 60 + now.minute
        k, b, phase = self.get_3stage_values_for_minutes(current_min)
        return {
            "kelvin": k,
            "brightness": b,
            "phase": phase,
            "current_minutes": current_min,
            "current_time_str": f"{now.hour:02d}:{now.minute:02d}",
        }

    def get_24h_curve_points(self) -> List[Dict[str, int]]:
        """Returns 48 points (every 30 minutes across 24h) for rendering the interactive solar curve."""
        points = []
        for m in range(0, 1440, 30):
            k, b, _ = self.get_3stage_values_for_minutes(m)
            points.append({"min": m, "kelvin": k, "brightness": b})
        return points
