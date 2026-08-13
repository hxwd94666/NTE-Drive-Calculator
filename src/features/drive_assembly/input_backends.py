# 封装自动装配使用的鼠标、Windows SendInput 和虚拟手柄后端。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import pyautogui

from src.features.drive_assembly.randomization import (
    RandomizationContext,
    jitter_duration_ms,
    jitter_position,
    jitter_scroll_endpoint,
    jitter_timing,
    path_noise_offset,
    random_input_delay,
)

_DEFAULT_RANDOMIZATION_CTX = RandomizationContext()

DEFAULT_CLICK_HOLD_SECONDS = 0.035
ROLE_LIST_OPEN_RS_HOLD_SECONDS = 0.25
SENDINPUT_DRAG_HOLD_SECONDS = 0.30
SENDINPUT_DRAG_RELEASE_SECONDS = 0.20
SENDINPUT_DRAG_STEP_SECONDS = 0.012
EQUIPMENT_DRAG_HOLD_SECONDS = 0.45
EQUIPMENT_DRAG_RELEASE_SECONDS = 0.20

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120


class MouseBackend(Protocol):
    def click(self, position: tuple[int, int]) -> None:
        """Click a screen position."""

    def move_to(self, position: tuple[int, int]) -> None:
        """Move the pointer without pressing a mouse button."""

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        """Drag from one screen position to another."""

    def drag_scroll(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        """Perform a filter-panel scroll gesture."""

    def scroll(self, position: tuple[int, int], clicks: int) -> None:
        """Rotate the mouse wheel at a screen position."""

    def press_key(self, key_name: str) -> None:
        """Press a keyboard key."""

    def press_gamepad_button(self, button_name: str) -> None:
        """Press a virtual gamepad button."""

    def push_left_joystick(self, x: float, y: float) -> None:
        """Push the virtual left stick once."""

    def pause(self, seconds: float) -> None:
        """Pause between actions."""

    def screenshot(self) -> Any:
        """Capture the current screen for optional UI detection."""


@dataclass
class ActionExecutionReport:
    """Result for one executed action sequence."""

    role_name: str | None = None
    executed_actions: int = 0
    skipped_actions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AssemblyExecutionReport:
    """Result for executing a multi-role assembly plan."""

    role_reports: list[ActionExecutionReport] = field(default_factory=list)
    skipped_roles: list[str] = field(default_factory=list)
    navigation_actions: int = 0
    missing_roles: list[str] = field(default_factory=list)
    duplicate_roles: list[dict[str, Any]] = field(default_factory=list)
    unrecognized_roles: list[dict[str, Any]] = field(default_factory=list)
    verification_failures: list[dict[str, Any]] = field(default_factory=list)

    @property
    def executed_actions(self) -> int:
        return self.navigation_actions + sum(report.executed_actions for report in self.role_reports)


class AssemblyExecutionStopped(RuntimeError):
    """Raised when the user stops automatic assembly."""


def f12_stop_checker() -> Callable[[], bool]:
    """Return a Windows F12 stop checker suitable for long-running mouse automation."""

    try:
        import ctypes

        user32 = ctypes.windll.user32
    except Exception:
        return lambda: False

    return lambda: bool(user32.GetAsyncKeyState(0x7B) & 0x8001)


class PyAutoGuiMouseBackend:
    """Mouse backend powered by pyautogui."""

    def __init__(self, randomization: RandomizationContext | None = None):

        self._randomization = (
            randomization
            if randomization is not None
            else RandomizationContext()
        )
        self._pyautogui = pyautogui
        self._pyautogui.FAILSAFE = True
        self._send_input = _WindowsSendInputMouseDriver(randomization=self._randomization)
        self._gamepad = _VirtualGamepadDriver()
        self._sleeper = time.sleep
        self._mouse_delivery_diagnostics: list[dict[str, Any]] = []

    def click(self, position: tuple[int, int]) -> None:
        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        jpos = jitter_position(ctx, position, ctx.click_offset_range)
        jhold = jitter_timing(ctx, DEFAULT_CLICK_HOLD_SECONDS)
        input_delay = random_input_delay(ctx)
        sleeper = getattr(self, "_sleeper", time.sleep)
        if input_delay > 0.0:
            sleeper(input_delay)
        if self._send_input.available:
            self._send_input.click(jpos, hold_seconds=jhold)
            self._record_mouse_delivery(
                "sendinput_click",
                requested_position=position,
                dispatched_position=jpos,
                cursor_position=self._send_input_cursor_position(),
            )
            return
        self._pyautogui.mouseUp()
        self._pyautogui.moveTo(*jpos)
        self._pyautogui.mouseDown()
        sleeper(jhold)
        self._pyautogui.mouseUp()
        self._record_mouse_delivery(
            "pyautogui_click",
            requested_position=position,
            dispatched_position=jpos,
            cursor_position=self._pyautogui_cursor_position(),
        )

    def move_to(self, position: tuple[int, int]) -> None:
        """Send a pointer-only movement to wake the cloud cursor after gamepad input."""

        if self._send_input.available:
            self._send_input.move_to(position)
            self._record_mouse_delivery(
                "sendinput_move",
                requested_position=position,
                dispatched_position=position,
                cursor_position=self._send_input_cursor_position(),
            )
            return
        self._pyautogui.moveTo(*position)
        self._record_mouse_delivery(
            "pyautogui_move",
            requested_position=position,
            dispatched_position=position,
            cursor_position=self._pyautogui_cursor_position(),
        )

    def cloud_click(self, position: tuple[int, int], hold_seconds: float = 0.12) -> None:
        """Use PyAutoGUI's down/up route for cloud-stream controls that retain SendInput state."""

        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        jpos = jitter_position(ctx, position, ctx.click_offset_range)
        sleeper = getattr(self, "_sleeper", time.sleep)
        delay = random_input_delay(ctx)
        if delay > 0.0:
            sleeper(delay)
        self._pyautogui.mouseUp(button="left")
        self._record_mouse_delivery("cloud_before_down")
        self._pyautogui.moveTo(*jpos)
        sleeper(0.05)
        self._pyautogui.mouseDown(button="left")
        self._record_mouse_delivery("cloud_after_down")
        sleeper(max(0.06, float(hold_seconds)))
        self._pyautogui.mouseUp(button="left")
        self._record_mouse_delivery("cloud_after_up")

    def force_mouse_release(self) -> None:
        """Send both release routes so a cloud client never retains left-button state."""

        send_input_result: int | None = None
        if self._send_input.available:
            send_input_result = self._send_input.release_left()
        self._pyautogui.mouseUp(button="left")
        self._record_mouse_delivery("forced_release", send_input_result)

    def consume_mouse_delivery_diagnostics(self) -> list[dict[str, Any]]:
        """Return and clear recent local button-state observations for one action batch."""

        records = list(getattr(self, "_mouse_delivery_diagnostics", []))
        self._mouse_delivery_diagnostics = []
        return records

    def _record_mouse_delivery(
        self,
        stage: str,
        send_input_result: int | None = None,
        requested_position: tuple[int, int] | None = None,
        dispatched_position: tuple[int, int] | None = None,
        cursor_position: tuple[int, int] | None = None,
    ) -> None:
        records = getattr(self, "_mouse_delivery_diagnostics", None)
        if records is None:
            records = []
            self._mouse_delivery_diagnostics = records
        record: dict[str, Any] = {
            "stage": stage,
            "local_left_down": self._local_left_button_down(),
            "send_input_result": send_input_result,
        }
        if requested_position is not None:
            record["requested_position"] = requested_position
        if dispatched_position is not None:
            record["dispatched_position"] = dispatched_position
        if cursor_position is not None:
            record["cursor_position"] = cursor_position
        records.append(record)
        del records[:-12]

    def _send_input_cursor_position(self) -> tuple[int, int] | None:
        cursor = getattr(self._send_input, "cursor_position", None)
        if not callable(cursor):
            return None
        try:
            return cursor()
        except Exception:
            return None

    def _pyautogui_cursor_position(self) -> tuple[int, int] | None:
        position = getattr(self._pyautogui, "position", None)
        if not callable(position):
            return None
        try:
            point = position()
            return int(point.x), int(point.y)
        except Exception:
            return None

    @staticmethod
    def _local_left_button_down() -> bool | None:
        try:
            import ctypes

            return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
        except Exception:
            return None

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        # Equipment placement needs the game's original mouse drag behavior.
        # Filter-panel scrolling uses drag_scroll() and remains on SendInput.
        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        jittered_start = jitter_position(ctx, start, ctx.drag_start_offset_range)
        jittered_end = jitter_position(ctx, end, ctx.drag_end_offset_range)
        duration = max(0.0, jitter_duration_ms(ctx, duration_ms) / 1000.0)
        sleeper = getattr(self, "_sleeper", time.sleep)

        # Give the game time to recognize that the filtered equipment card was
        # grabbed. Do not use dragTo here: it presses and releases the button
        # itself, which resets the long press before the movement begins.
        self._pyautogui.mouseUp(button="left")
        self._pyautogui.moveTo(*jittered_start)
        sleeper(jitter_timing(ctx, 0.15))
        self._pyautogui.mouseDown(button="left")
        try:
            sleeper(jitter_timing(ctx, EQUIPMENT_DRAG_HOLD_SECONDS))
            self._pyautogui.moveTo(*jittered_end, duration=duration)
        finally:
            self._pyautogui.mouseUp(button="left")
        sleeper(jitter_timing(ctx, EQUIPMENT_DRAG_RELEASE_SECONDS))

    def drag_scroll(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        if self._send_input.available:
            self._send_input.drag(start, end, duration_ms)
            return
        self.drag(start, end, duration_ms)

    def scroll(self, position: tuple[int, int], clicks: int) -> None:
        if not clicks:
            return
        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        randomized_position = jitter_position(ctx, position, ctx.click_offset_range)
        input_delay = random_input_delay(ctx)
        if input_delay > 0.0:
            getattr(self, "_sleeper", time.sleep)(input_delay)
        if self._send_input.available:
            self._send_input.scroll(randomized_position, clicks)
            return
        self._pyautogui.mouseUp(button="left")
        self._pyautogui.moveTo(*randomized_position)
        self._pyautogui.scroll(int(clicks))

    def press_key(self, key_name: str) -> None:
        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        input_delay = random_input_delay(ctx)
        if input_delay > 0.0:
            getattr(self, "_sleeper", time.sleep)(input_delay)
        self._pyautogui.press(str(key_name))

    def press_gamepad_button(self, button_name: str) -> None:
        self._gamepad.press(button_name)

    def push_left_joystick(self, x: float, y: float) -> None:
        self._gamepad.push_left_joystick(x, y)

    def close(self) -> None:
        """Reset and release the lazily-created virtual controller."""

        self._gamepad.close()

    def pause(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def screenshot(self) -> Any:
        return self._pyautogui.screenshot()

    def enable_randomization(self, seed: int | None = None) -> None:
        """Turn on the randomization context for this backend.

        When *seed* is provided the internal RNG is reset so that
        subsequent jitter is deterministic and reproducible.
        """
        self._randomization.enabled = True
        if seed is not None:
            self._randomization.seed(seed)
        self._send_input._randomization = self._randomization


class _WindowsSendInputMouseDriver:
    """SendInput drag driver that mimics the scanner's long-press swipe pattern."""

    def __init__(
        self,
        user32: Any | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        randomization: RandomizationContext | None = None,
    ):
        self._randomization = randomization or _DEFAULT_RANDOMIZATION_CTX
        self._sleeper = sleeper
        self._ctypes = None
        self._wintypes = None
        self._input_cls = None
        self._mouse_input_cls = None
        self._user32 = None
        try:
            import ctypes
            import ctypes.wintypes

            self._ctypes = ctypes
            self._wintypes = ctypes.wintypes
            self._user32 = user32 or ctypes.windll.user32
            self._mouse_input_cls, self._input_cls = self._build_structs(ctypes, ctypes.wintypes)
        except Exception:
            self._user32 = None

    @property
    def available(self) -> bool:
        return self._user32 is not None and self._input_cls is not None and self._mouse_input_cls is not None

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        if not self.available:
            raise RuntimeError("SendInput is not available")
        ctx = getattr(self, "_randomization", _DEFAULT_RANDOMIZATION_CTX)
        jittered_start = jitter_position(ctx, start, ctx.drag_start_offset_range)
        jittered_end = jitter_scroll_endpoint(ctx, start, end, ctx.drag_end_offset_range)
        jittered_duration_ms = jitter_duration_ms(ctx, duration_ms)
        self._move_to(jittered_start)
        self._sleeper(jitter_timing(ctx, 0.15))
        self._send(MOUSEEVENTF_LEFTDOWN)
        self._sleeper(jitter_timing(ctx, SENDINPUT_DRAG_HOLD_SECONDS))
        steps = self._drag_steps(jittered_start, jittered_end, jittered_duration_ms)
        self._move_relative_in_steps(jittered_start, jittered_end, steps, ctx=ctx)
        self._sleeper(jitter_timing(ctx, SENDINPUT_DRAG_RELEASE_SECONDS))
        self._send(MOUSEEVENTF_LEFTUP)
        self._sleeper(jitter_timing(ctx, SENDINPUT_DRAG_RELEASE_SECONDS))

    def click(self, position: tuple[int, int], hold_seconds: float | None = None) -> None:
        """Click without invoking PyAutoGUI's corner fail-safe check."""

        if not self.available:
            raise RuntimeError("SendInput is not available")
        hold = hold_seconds if hold_seconds is not None else DEFAULT_CLICK_HOLD_SECONDS
        self._move_to(position)
        self._sleeper(0.05)
        self._send(MOUSEEVENTF_LEFTDOWN)
        self._sleeper(hold)
        self._send(MOUSEEVENTF_LEFTUP)

    def release_left(self) -> int | None:
        """Issue a standalone left-button release without moving the pointer."""

        if self.available:
            return self._send(MOUSEEVENTF_LEFTUP)
        return None

    def scroll(self, position: tuple[int, int], clicks: int) -> None:
        """Rotate the wheel at *position* using the same SendInput route as scanner swipes."""

        if not self.available:
            raise RuntimeError("SendInput is not available")
        if not clicks:
            return
        self._move_to(position)
        self._sleeper(0.05)
        self._send(MOUSEEVENTF_WHEEL, mouse_data=int(clicks) * WHEEL_DELTA)

    def _move_relative_in_steps(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        steps: int,
        ctx: RandomizationContext | None = None,
    ) -> None:
        previous_x, previous_y = start
        total_dx = end[0] - start[0]
        total_dy = end[1] - start[1]
        use_noise = ctx is not None and ctx.enabled and ctx.path_noise_pixels > 0
        noise_dx = 0
        noise_dy = 0
        for index in range(1, steps + 1):
            target_x = start[0] + round(total_dx * index / steps)
            target_y = start[1] + round(total_dy * index / steps)
            # Zero-sum path noise: add noise on odd steps, undo on even steps
            # so the endpoint is always exactly correct.
            if use_noise and index % 2 == 1 and index + 1 <= steps:
                noise_dx, noise_dy = path_noise_offset(ctx)
            elif use_noise and index % 2 == 0:
                target_x -= noise_dx
                target_y -= noise_dy
                noise_dx = 0
                noise_dy = 0
            dx = target_x - previous_x
            dy = target_y - previous_y
            if dx or dy:
                self._send(MOUSEEVENTF_MOVE, dx, dy)
            previous_x, previous_y = target_x, target_y
            step_sleep = jitter_timing(ctx, SENDINPUT_DRAG_STEP_SECONDS) if ctx is not None else SENDINPUT_DRAG_STEP_SECONDS
            self._sleeper(step_sleep)

    def _move_to(self, position: tuple[int, int]) -> None:
        ax, ay = self._abs_coord(position)
        self._send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, ax, ay)

    def move_to(self, position: tuple[int, int]) -> None:
        """Move without emitting a button event."""

        if not self.available:
            raise RuntimeError("SendInput is not available")
        self._move_to(position)

    def cursor_position(self) -> tuple[int, int] | None:
        """Return Windows' recorded pointer position after a SendInput action."""

        if self._user32 is None or self._wintypes is None:
            return None
        try:
            point = self._wintypes.POINT()
            if not self._user32.GetCursorPos(self._ctypes.byref(point)):
                return None
            return int(point.x), int(point.y)
        except Exception:
            return None

    def _abs_coord(self, position: tuple[int, int]) -> tuple[int, int]:
        width = max(1, int(self._user32.GetSystemMetrics(0)))
        height = max(1, int(self._user32.GetSystemMetrics(1)))
        x, y = position
        return int(x * 65535 / width), int(y * 65535 / height)

    def _drag_steps(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> int:
        distance = max(abs(end[0] - start[0]), abs(end[1] - start[1]))
        duration_steps = max(1, int(max(1, duration_ms) / 12))
        distance_steps = max(1, int(distance / 18))
        return max(50, min(90, max(duration_steps, distance_steps)))

    def _send(self, flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> int:
        mouse_input = self._mouse_input_cls(dx, dy, mouse_data, flags, 0, None)
        input_value = self._input_cls(INPUT_MOUSE, mouse_input)
        return int(self._user32.SendInput(1, self._ctypes.byref(input_value), self._ctypes.sizeof(input_value)))

    @staticmethod
    def _build_structs(ctypes_module: Any, wintypes_module: Any) -> tuple[Any, Any]:
        class MouseInput(ctypes_module.Structure):
            _fields_ = [
                ("dx", ctypes_module.c_long),
                ("dy", ctypes_module.c_long),
                ("mouseData", wintypes_module.DWORD),
                ("dwFlags", wintypes_module.DWORD),
                ("time", wintypes_module.DWORD),
                ("dwExtraInfo", ctypes_module.POINTER(ctypes_module.c_ulong)),
            ]

        class Input(ctypes_module.Structure):
            _fields_ = [
                ("type", wintypes_module.DWORD),
                ("mi", MouseInput),
            ]

        return MouseInput, Input


class _VirtualGamepadDriver:
    """Small virtual Xbox gamepad wrapper for role navigation."""

    BUTTON_NAMES = {
        "dpad_up": "XUSB_GAMEPAD_DPAD_UP",
        "dpad_down": "XUSB_GAMEPAD_DPAD_DOWN",
        "dpad_left": "XUSB_GAMEPAD_DPAD_LEFT",
        "dpad_right": "XUSB_GAMEPAD_DPAD_RIGHT",
        "a": "XUSB_GAMEPAD_A",
        "b": "XUSB_GAMEPAD_B",
        "x": "XUSB_GAMEPAD_X",
        "y": "XUSB_GAMEPAD_Y",
        "rs": "XUSB_GAMEPAD_RIGHT_THUMB",
    }

    def __init__(
        self,
        hold_seconds: float = 0.08,
        settle_seconds: float = 0.30,
        connect_settle_seconds: float = 0.40,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self._hold_seconds = hold_seconds
        self._settle_seconds = settle_seconds
        self._connect_settle_seconds = connect_settle_seconds
        self._sleeper = sleeper
        self._gamepad = None
        self._buttons = None

    def press(self, button_name: str) -> None:
        self._ensure_connected()
        key = str(button_name).strip().lower()
        attr_name = self.BUTTON_NAMES.get(key)
        if not attr_name:
            raise ValueError(f"unknown gamepad button: {button_name}")
        button = getattr(self._buttons, attr_name)
        self._gamepad.press_button(button=button)
        self._gamepad.update()
        hold_seconds = (
            ROLE_LIST_OPEN_RS_HOLD_SECONDS
            if key == "rs"
            else self._hold_seconds
        )
        self._sleeper(hold_seconds)
        self._gamepad.release_button(button=button)
        self._gamepad.update()
        self._sleeper(self._settle_seconds)

    def push_left_joystick(self, x: float, y: float) -> None:
        self._ensure_connected()
        self._gamepad.left_joystick_float(x_value_float=float(x), y_value_float=float(y))
        self._gamepad.update()
        self._sleeper(self._hold_seconds)
        self._gamepad.left_joystick_float(x_value_float=0.0, y_value_float=0.0)
        self._gamepad.update()
        self._sleeper(self._settle_seconds)

    def close(self) -> None:
        """Reset inputs, then release the ViGEm controller object.

        ``vgamepad`` exposes no explicit disconnect API; dropping the gamepad
        object after a reset/update releases its virtual-controller handle.
        """

        gamepad = self._gamepad
        self._gamepad = None
        self._buttons = None
        if gamepad is None:
            return
        try:
            gamepad.reset()
            gamepad.update()
        finally:
            del gamepad

    def _ensure_connected(self) -> None:
        if self._gamepad is not None:
            return
        import vgamepad as vg

        self._gamepad = vg.VX360Gamepad()
        self._buttons = vg.XUSB_BUTTON
        self._sleeper(self._connect_settle_seconds)
