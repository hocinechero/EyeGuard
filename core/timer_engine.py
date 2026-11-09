import ctypes
import enum
import sys
import threading
import time
from typing import Callable, Optional, Dict, Any
from core.idle_detector import IdleDetector

user32 = ctypes.windll.user32


def _is_workstation_locked() -> bool:
    """Detects if the Windows session is locked (LogonUI / non-interactive desktop)."""
    try:
        hdesk = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not hdesk:
            return True
        res = user32.SwitchDesktop(hdesk)
        user32.CloseDesktop(hdesk)
        return not bool(res)
    except Exception:
        return False


def _is_mouse_button_held() -> bool:
    """Detects if the left or right mouse button is actively pressed (e.g. dragging or selecting)."""
    try:
        return bool(
            (user32.GetAsyncKeyState(0x01) & 0x8000)
            or (user32.GetAsyncKeyState(0x02) & 0x8000)
        )
    except Exception:
        return False


class TimerState(str, enum.Enum):
    WORKING = "working"
    PAUSED = "paused"
    SHORT_BREAK = "short_break"
    LONG_BREAK = "long_break"
    BLINK_20_20 = "blink_20_20"


class BreakTimerEngine:
    """
    High-precision, non-destructive ergonomic break scheduler implementing:
    1. Medical 20-20-20 rule (20-second micro-break every 20 minutes of screen work).
    2. Pomodoro cycles (customizable work duration, short break, and long break).
    3. Automated Win32 64-bit user idle sensing (pause on short idle, full session reset on long idle).
    4. Workstation lock/unlock detection (automatic session reset on Windows unlock).
    5. Pre-break countdown notification callback (10s/20s/30s/60s before break).
    6. Enforced delay limits (allow_delay_timer + max_delay_times).
    7. Disentangled state machine: 20-20-20 micro-breaks never wipe Pomodoro progress.
    """

    def __init__(
        self,
        work_duration_sec: int = 25 * 60,
        short_break_sec: int = 5 * 60,
        long_break_sec: int = 15 * 60,
        cycles_before_long: int = 4,
        enable_20_20: bool = True,
        enable_pomodoro: bool = True,
        auto_pause: bool = True,
        idle_threshold_sec: int = 120,
        auto_start: bool = True,
        enabled: bool = True,
        break_mode: str = "fullscreen",
        blink_reminder: bool = False,
        sound_enabled: bool = True,
        dnd_fullscreen: bool = True,
        restart_on_idle: bool = True,
        restart_idle_minutes: int = 15,
        restart_on_unlock: bool = True,
        pre_break_prompt_sec: int = 10,
        allow_delay_timer: bool = True,
        max_delay_times: int = 3,
    ):
        self.enabled = bool(enabled)
        self.break_mode = (
            str(break_mode)
            if break_mode in ("gentle", "fullscreen", "strict")
            else "fullscreen"
        )
        self.blink_reminder = bool(blink_reminder)
        self.sound_enabled = bool(sound_enabled)
        self.dnd_fullscreen = bool(dnd_fullscreen)
        self.restart_on_idle = bool(restart_on_idle)
        self.restart_idle_minutes = max(1, int(restart_idle_minutes))
        self.restart_on_unlock = bool(restart_on_unlock)
        self.pre_break_prompt_sec = max(5, int(pre_break_prompt_sec))
        self.allow_delay_timer = bool(allow_delay_timer)
        self.max_delay_times = max(1, int(max_delay_times))

        self.delays_used = 0
        self._pre_prompt_fired = False
        self._pre_prompt_10s_fired = False
        self._pre_blink_prompt_fired = False
        self._was_workstation_locked = False

        self._pause_until_ts: Optional[float] = None
        self._blink_anim_counter_sec = 60
        self.last_blink_anim_ts = 0

        self.work_duration = int(work_duration_sec)
        self.short_break_duration = int(short_break_sec)
        self.long_break_duration = int(long_break_sec)
        self.cycles_before_long = int(cycles_before_long)
        self.enable_20_20 = bool(enable_20_20)
        self.enable_pomodoro = bool(enable_pomodoro)
        self.auto_pause = bool(auto_pause)
        self.idle_threshold_sec = int(idle_threshold_sec)

        self.idle_detector = IdleDetector(idle_threshold_sec)

        self.state = TimerState.WORKING if auto_start else TimerState.PAUSED
        self.cycle_count = 0

        # Disentangled Timers:
        self.work_remaining_sec = self.work_duration
        self.break_remaining_sec = 0

        self._blink_interval = 20 * 60  # 20 minutes
        self.blink_counter_sec = self._blink_interval
        self.blink_remaining_sec = 0
        self.blink_duration_sec = 20

        self._running = False
        self._paused_manually = not auto_start
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        # Callbacks
        self.on_tick: Optional[Callable] = None
        self.on_break_trigger: Optional[Callable[[TimerState, int], None]] = None
        self.on_break_end: Optional[Callable[[], None]] = None
        self.on_pre_break_prompt: Optional[Callable[[int], None]] = None
        self.on_blink_reminder: Optional[Callable[[], None]] = None

    @property
    def remaining_seconds(self) -> int:
        with self._lock:
            if self.state == TimerState.BLINK_20_20:
                return max(0, int(self.blink_remaining_sec))
            elif self.state in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK):
                return max(0, int(self.break_remaining_sec))
            else:
                return max(0, int(self.work_remaining_sec))

    @remaining_seconds.setter
    def remaining_seconds(self, value: int):
        with self._lock:
            val = max(0, int(value))
            if self.state == TimerState.BLINK_20_20:
                self.blink_remaining_sec = val
            elif self.state in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK):
                self.break_remaining_sec = val
            else:
                self.work_remaining_sec = val

    @property
    def _blink_counter(self) -> int:
        return int(self.blink_counter_sec)

    @_blink_counter.setter
    def _blink_counter(self, val: int):
        self.blink_counter_sec = int(val)

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, daemon=True, name="EyeGuard-TimerEngine"
            )
            self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
            self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def set_enabled(self, enabled: bool):
        with self._lock:
            self.enabled = bool(enabled)
            if not self.enabled:
                self._paused_manually = True
                self.state = TimerState.PAUSED
            else:
                self._paused_manually = False
                self._pause_until_ts = None
                self.state = TimerState.WORKING
        if enabled and not self._running:
            self.start()

    def pause(self):
        with self._lock:
            self._paused_manually = True
            self._pause_until_ts = None
            self.state = TimerState.PAUSED

    def pause_for_minutes(self, minutes: int):
        with self._lock:
            if minutes <= 0:
                self._paused_manually = False
                self._pause_until_ts = None
                self.state = TimerState.WORKING
            else:
                self._paused_manually = True
                self._pause_until_ts = time.time() + (int(minutes) * 60)
                self.state = TimerState.PAUSED

    def resume(self):
        with self._lock:
            if not self.enabled:
                return
            self._paused_manually = False
            self._pause_until_ts = None
            if self.state == TimerState.PAUSED:
                self.state = TimerState.WORKING

    def skip_break(self):
        callbacks = []
        with self._lock:
            if self.state == TimerState.BLINK_20_20:
                self.state = TimerState.WORKING
                self.blink_remaining_sec = 0
                self.blink_counter_sec = self._blink_interval
                self._pre_blink_prompt_fired = False
                if self.on_break_end:
                    callbacks.append(self.on_break_end)
            elif self.state in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK):
                self.state = TimerState.WORKING
                self.break_remaining_sec = 0
                self.work_remaining_sec = self.work_duration
                self.blink_counter_sec = self._blink_interval
                self.delays_used = 0
                self._pre_prompt_fired = False
                self._pre_prompt_10s_fired = False
                self._pre_blink_prompt_fired = False
                if self.on_break_end:
                    callbacks.append(self.on_break_end)

        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass

    def take_break_now(
        self, break_type: str = "short_break", duration_sec: Optional[int] = None
    ):
        callbacks = []
        with self._lock:
            if break_type in ("blink_20_20", TimerState.BLINK_20_20):
                dur = (
                    int(duration_sec)
                    if duration_sec is not None
                    else self.blink_duration_sec
                )
                self.state = TimerState.BLINK_20_20
                self.blink_remaining_sec = dur
                if self.on_break_trigger:
                    callbacks.append(
                        (self.on_break_trigger, (TimerState.BLINK_20_20, dur))
                    )
            elif break_type in ("long_break", TimerState.LONG_BREAK):
                dur = (
                    int(duration_sec)
                    if duration_sec is not None
                    else self.long_break_duration
                )
                self.state = TimerState.LONG_BREAK
                self.break_remaining_sec = dur
                if self.on_break_trigger:
                    callbacks.append(
                        (self.on_break_trigger, (TimerState.LONG_BREAK, dur))
                    )
            else:
                dur = (
                    int(duration_sec)
                    if duration_sec is not None
                    else self.short_break_duration
                )
                self.state = TimerState.SHORT_BREAK
                self.break_remaining_sec = dur
                if self.on_break_trigger:
                    callbacks.append(
                        (self.on_break_trigger, (TimerState.SHORT_BREAK, dur))
                    )

        for fn, args in callbacks:
            try:
                fn(*args)
            except Exception:
                pass

    def postpone_break(self, minutes: int = 3) -> bool:
        callbacks = []
        with self._lock:
            if not self.allow_delay_timer or self.delays_used >= self.max_delay_times:
                return False
            self.delays_used += 1
            self._pre_prompt_fired = False
            self._pre_prompt_10s_fired = False
            self._pre_blink_prompt_fired = False
            self.state = TimerState.WORKING
            self.work_remaining_sec = max(self.work_remaining_sec, minutes * 60)
            self.blink_counter_sec = max(self.blink_counter_sec, minutes * 60)
            if self.on_break_end:
                callbacks.append(self.on_break_end)

        for cb in callbacks:
            try:
                cb()
            except Exception:
                pass
        return True

    def reset_work(self):
        with self._lock:
            self.work_remaining_sec = self.work_duration
            self.blink_counter_sec = self._blink_interval
            self.delays_used = 0
            self._pre_prompt_fired = False
            self._pre_prompt_10s_fired = False
            self._pre_blink_prompt_fired = False
            self.state = TimerState.PAUSED
            self._paused_manually = True

    def configure(
        self,
        work_duration_sec: Optional[int] = None,
        short_break_sec: Optional[int] = None,
        long_break_sec: Optional[int] = None,
        cycles_before_long: Optional[int] = None,
        enable_20_20: Optional[bool] = None,
        enable_pomodoro: Optional[bool] = None,
        auto_pause: Optional[bool] = None,
        idle_threshold_sec: Optional[int] = None,
        enabled: Optional[bool] = None,
        break_mode: Optional[str] = None,
        blink_reminder: Optional[bool] = None,
        sound_enabled: Optional[bool] = None,
        dnd_fullscreen: Optional[bool] = None,
        restart_on_idle: Optional[bool] = None,
        restart_idle_minutes: Optional[int] = None,
        restart_on_unlock: Optional[bool] = None,
        pre_break_prompt_sec: Optional[int] = None,
        allow_delay_timer: Optional[bool] = None,
        max_delay_times: Optional[int] = None,
    ):
        with self._lock:
            if work_duration_sec is not None:
                self.work_duration = max(1, int(work_duration_sec))
                if self.state in (TimerState.WORKING, TimerState.PAUSED):
                    self.work_remaining_sec = self.work_duration
                    self._pre_prompt_fired = False
            if short_break_sec is not None:
                self.short_break_duration = max(1, int(short_break_sec))
            if long_break_sec is not None:
                self.long_break_duration = max(1, int(long_break_sec))
            if cycles_before_long is not None:
                self.cycles_before_long = max(0, int(cycles_before_long))
            if enable_20_20 is not None:
                self.enable_20_20 = bool(enable_20_20)
            if enable_pomodoro is not None:
                self.enable_pomodoro = bool(enable_pomodoro)
            if auto_pause is not None:
                self.auto_pause = bool(auto_pause)
            if idle_threshold_sec is not None:
                self.idle_threshold_sec = max(1, int(idle_threshold_sec))
                self.idle_detector.threshold = self.idle_threshold_sec
            if enabled is not None:
                self.set_enabled(bool(enabled))
            if break_mode in ("gentle", "fullscreen", "strict"):
                self.break_mode = str(break_mode)
            if blink_reminder is not None:
                self.blink_reminder = bool(blink_reminder)
            if sound_enabled is not None:
                self.sound_enabled = bool(sound_enabled)
            if dnd_fullscreen is not None:
                self.dnd_fullscreen = bool(dnd_fullscreen)
            if restart_on_idle is not None:
                self.restart_on_idle = bool(restart_on_idle)
            if restart_idle_minutes is not None:
                self.restart_idle_minutes = max(1, int(restart_idle_minutes))
            if restart_on_unlock is not None:
                self.restart_on_unlock = bool(restart_on_unlock)
            if pre_break_prompt_sec is not None:
                self.pre_break_prompt_sec = max(5, int(pre_break_prompt_sec))
            if allow_delay_timer is not None:
                self.allow_delay_timer = bool(allow_delay_timer)
            if max_delay_times is not None:
                self.max_delay_times = max(1, int(max_delay_times))

    def _build_status(self, is_user_idle: bool) -> Dict[str, Any]:
        rem = self.remaining_seconds
        m = rem // 60
        s = rem % 60
        t_str = "--:--" if not self.enabled else f"{m:02d}:{s:02d}"
        return {
            "enabled": self.enabled,
            "break_mode": self.break_mode,
            "blink_reminder": self.blink_reminder,
            "sound_enabled": self.sound_enabled,
            "dnd_fullscreen": self.dnd_fullscreen,
            "allow_delay_timer": self.allow_delay_timer,
            "max_delay_times": self.max_delay_times,
            "delays_used": self.delays_used,
            "delays_remaining": max(0, self.max_delay_times - self.delays_used)
            if self.allow_delay_timer
            else 0,
            "last_blink_anim_ts": self.last_blink_anim_ts,
            "state": self.state.value
            if isinstance(self.state, enum.Enum)
            else str(self.state),
            "state_enum": self.state,
            "remaining_seconds": rem,
            "work_duration": self.work_duration,
            "work_remaining_sec": self.work_remaining_sec,
            "break_remaining_sec": self.break_remaining_sec,
            "blink_counter_sec": self.blink_counter_sec,
            "blink_remaining_sec": self.blink_remaining_sec,
            "cycle_count": self.cycle_count,
            "cycles_before_long": self.cycles_before_long,
            "is_idle": bool(is_user_idle),
            "is_paused": self.state == TimerState.PAUSED or self._paused_manually,
            "time_str": t_str,
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            is_user_idle = self.auto_pause and self.idle_detector.is_idle()
            return self._build_status(is_user_idle)

    def _invoke_tick(self, status: Dict[str, Any]):
        if not self.on_tick:
            return
        try:
            self.on_tick(status)
        except TypeError:
            try:
                self.on_tick(
                    status["remaining_seconds"],
                    status["state_enum"],
                    status["is_idle"],
                )
            except Exception:
                pass
        except Exception:
            pass

    def _invoke_pre_break_prompt(self, remaining_sec: int, break_type: str = "short_break"):
        if not self.on_pre_break_prompt:
            return
        try:
            self.on_pre_break_prompt(remaining_sec, break_type)
        except TypeError:
            try:
                self.on_pre_break_prompt(remaining_sec)
            except Exception:
                pass
        except Exception:
            pass

    def tick(self, delta_sec: float = 1.0) -> Dict[str, Any]:
        delta = max(0.0, float(delta_sec))
        callbacks_to_fire = []

        with self._lock:
            idle_sec = self.idle_detector.get_idle_seconds()
            is_user_idle = self.auto_pause and (idle_sec >= self.idle_threshold_sec)

            # Check Windows workstation lock / unlock transition
            is_unit_test = (
                (not getattr(sys, "frozen", False))
                and ("unittest" in sys.modules)
                and any(
                    "test" in str(a).lower() or "unittest" in str(a).lower()
                    for a in sys.argv
                )
            )
            if not is_unit_test and self.restart_on_unlock:
                locked_now = _is_workstation_locked()
                if self._was_workstation_locked and not locked_now:
                    # User just unlocked Windows: reset work timer
                    self.work_remaining_sec = self.work_duration
                    self.blink_counter_sec = self._blink_interval
                    self.delays_used = 0
                    self._pre_prompt_fired = False
                    self._pre_prompt_10s_fired = False
                    self._pre_blink_prompt_fired = False
                self._was_workstation_locked = locked_now

            # Check full timer restart after prolonged mouse inactivity
            if (
                self.restart_on_idle
                and is_user_idle
                and idle_sec >= (self.restart_idle_minutes * 60)
            ):
                self.work_remaining_sec = self.work_duration
                self.blink_counter_sec = self._blink_interval
                self.delays_used = 0
                self._pre_prompt_fired = False
                self._pre_prompt_10s_fired = False
                self._pre_blink_prompt_fired = False

            if self._paused_manually and self._pause_until_ts is not None:
                if time.time() >= self._pause_until_ts:
                    self._paused_manually = False
                    self._pause_until_ts = None
                    if self.enabled:
                        self.state = TimerState.WORKING

            if not self.enabled or self._paused_manually:
                status = self._build_status(is_user_idle)
                if self.on_tick:
                    callbacks_to_fire.append((self._invoke_tick, (status,)))

            elif self.state == TimerState.WORKING:
                if is_user_idle:
                    status = self._build_status(is_user_idle)
                    if self.on_tick:
                        callbacks_to_fire.append((self._invoke_tick, (status,)))
                else:
                    if self.enable_pomodoro:
                        self.work_remaining_sec -= delta

                    if self.enable_20_20:
                        self.blink_counter_sec -= delta

                    if self.blink_reminder:
                        self._blink_anim_counter_sec -= delta
                        if self._blink_anim_counter_sec <= 0:
                            self._blink_anim_counter_sec = 60
                            self.last_blink_anim_ts = int(time.time() * 1000)
                            if self.on_blink_reminder:
                                callbacks_to_fire.append((self.on_blink_reminder, ()))

                    upcoming_pomo_type = (
                        "long_break"
                        if (
                            self.cycles_before_long > 0
                            and (self.cycle_count + 1) >= self.cycles_before_long
                        )
                        else "short_break"
                    )

                    # Pre-break prompt notification (for fullscreen / strict Pomodoro breaks)
                    if (
                        self.enable_pomodoro
                        and self.break_mode in ("fullscreen", "strict")
                        and 0 < self.work_remaining_sec <= self.pre_break_prompt_sec
                        and not self._pre_prompt_fired
                    ):
                        self._pre_prompt_fired = True
                        if self.work_remaining_sec <= 10:
                            self._pre_prompt_10s_fired = True
                        if self.on_pre_break_prompt:
                            callbacks_to_fire.append(
                                (
                                    self._invoke_pre_break_prompt,
                                    (int(self.work_remaining_sec), upcoming_pomo_type),
                                )
                            )
                    elif (
                        self.enable_pomodoro
                        and self.break_mode in ("fullscreen", "strict")
                        and self.pre_break_prompt_sec > 10
                        and 0 < self.work_remaining_sec <= 10
                        and not self._pre_prompt_10s_fired
                    ):
                        self._pre_prompt_10s_fired = True
                        if self.on_pre_break_prompt:
                            callbacks_to_fire.append(
                                (
                                    self._invoke_pre_break_prompt,
                                    (int(self.work_remaining_sec), upcoming_pomo_type),
                                )
                            )

                    # Pre-break 10s prompt notification for 20-20-20 short eye break
                    blink_prompt_lead = min(10, self.pre_break_prompt_sec)
                    if (
                        self.enable_20_20
                        and self.break_mode in ("fullscreen", "strict")
                        and 0 < self.blink_counter_sec <= blink_prompt_lead
                        and not self._pre_blink_prompt_fired
                        and (not self.enable_pomodoro or self.work_remaining_sec > blink_prompt_lead)
                    ):
                        self._pre_blink_prompt_fired = True
                        if self.on_pre_break_prompt:
                            callbacks_to_fire.append(
                                (
                                    self._invoke_pre_break_prompt,
                                    (int(self.blink_counter_sec), "blink_20_20"),
                                )
                            )

                    # Do-Not-Disturb during fullscreen apps or active mouse dragging (when not in unit tests)
                    dnd_hold = False
                    mouse_drag_hold = False
                    if not is_unit_test:
                        mouse_drag_hold = _is_mouse_button_held()
                        if self.dnd_fullscreen:
                            try:
                                from core.window_focus import is_foreground_fullscreen
                                dnd_hold = is_foreground_fullscreen()
                            except Exception:
                                dnd_hold = False

                    if dnd_hold and self.work_remaining_sec <= 0:
                        self.work_remaining_sec = 5
                    elif mouse_drag_hold and (self.work_remaining_sec <= 0 or self.blink_counter_sec <= 0):
                        if self.work_remaining_sec <= 0:
                            self.work_remaining_sec = 1
                        if self.blink_counter_sec <= 0:
                            self.blink_counter_sec = 1

                    # Check Pomodoro work session completion first
                    elif self.enable_pomodoro and self.work_remaining_sec <= 0:
                        self.work_remaining_sec = 0
                        self._pre_prompt_fired = False
                        self._pre_prompt_10s_fired = False
                        self._pre_blink_prompt_fired = False
                        self.cycle_count += 1
                        if (
                            self.cycles_before_long > 0
                            and self.cycle_count >= self.cycles_before_long
                        ):
                            self.cycle_count = 0
                            self.state = TimerState.LONG_BREAK
                            self.break_remaining_sec = self.long_break_duration
                            if self.on_break_trigger:
                                callbacks_to_fire.append(
                                    (
                                        self.on_break_trigger,
                                        (TimerState.LONG_BREAK, self.long_break_duration),
                                    )
                                )
                        else:
                            self.state = TimerState.SHORT_BREAK
                            self.break_remaining_sec = self.short_break_duration
                            if self.on_break_trigger:
                                callbacks_to_fire.append(
                                    (
                                        self.on_break_trigger,
                                        (TimerState.SHORT_BREAK, self.short_break_duration),
                                    )
                                )

                    # Otherwise, check 20-20-20 micro-break trigger
                    elif self.enable_20_20 and self.blink_counter_sec <= 0 and not dnd_hold:
                        self.state = TimerState.BLINK_20_20
                        self.blink_remaining_sec = self.blink_duration_sec
                        self.blink_counter_sec = self._blink_interval
                        self._pre_blink_prompt_fired = False
                        if self.on_break_trigger:
                            callbacks_to_fire.append(
                                (
                                    self.on_break_trigger,
                                    (TimerState.BLINK_20_20, self.blink_duration_sec),
                                )
                            )

                    status = self._build_status(is_user_idle)
                    if self.on_tick:
                        callbacks_to_fire.append((self._invoke_tick, (status,)))

            elif self.state == TimerState.BLINK_20_20:
                self.blink_remaining_sec -= delta
                if self.blink_remaining_sec <= 0:
                    self.blink_remaining_sec = 0
                    self.state = TimerState.WORKING
                    if self.on_break_end:
                        callbacks_to_fire.append((self.on_break_end, ()))

                status = self._build_status(is_user_idle)
                if self.on_tick:
                    callbacks_to_fire.append((self._invoke_tick, (status,)))

            elif self.state in (TimerState.SHORT_BREAK, TimerState.LONG_BREAK):
                self.break_remaining_sec -= delta
                if self.break_remaining_sec <= 0:
                    self.break_remaining_sec = 0
                    self.state = TimerState.WORKING
                    self.work_remaining_sec = self.work_duration
                    self.blink_counter_sec = self._blink_interval
                    self.delays_used = 0
                    self._pre_prompt_fired = False
                    if self.on_break_end:
                        callbacks_to_fire.append((self.on_break_end, ()))

                status = self._build_status(is_user_idle)
                if self.on_tick:
                    callbacks_to_fire.append((self._invoke_tick, (status,)))

            else:
                status = self._build_status(is_user_idle)
                if self.on_tick:
                    callbacks_to_fire.append((self._invoke_tick, (status,)))

        for fn, args in callbacks_to_fire:
            try:
                fn(*args)
            except Exception:
                pass

        return status

    def _run_loop(self):
        while self._running:
            if self._stop_event.wait(timeout=1.0):
                break
            if not self._running:
                break
            self.tick(delta_sec=1.0)
