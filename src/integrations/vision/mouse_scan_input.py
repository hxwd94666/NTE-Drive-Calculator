# 提供视觉库存扫描使用的随机化 Windows 鼠标输入。
"""Randomized Windows mouse input for visual inventory scanning."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Callable, Protocol


def _round_half_up(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def inventory_top_reset_swipe_points(
    *,
    left: int,
    top: int,
    width: int,
    height: int,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return the count-independent top-to-bottom list-reset swipe points.

    The incremental scanner uses the reverse bottom-to-top motion.  Full scans
    drag from the matching upper point to the lower point once before their
    existing navigation begins.
    """

    center_x = int(left) + max(1, int(width)) // 2
    start_y = int(top) + _round_half_up(max(1, int(height)) * 300 / 1440)
    end_y = int(top) + _round_half_up(max(1, int(height)) * 1300 / 1440)
    return (center_x, start_y), (center_x, end_y)


@dataclass(frozen=True)
class MouseInputRandomization:
    """Bounded timing and pixel jitter for one mouse scan session."""

    click_sigma_px_1080: float = 1.8
    click_limit_px_1080: float = 4.0
    move_seconds_range: tuple[float, float] = (0.025, 0.060)
    hold_seconds_range: tuple[float, float] = (0.018, 0.035)
    after_click_seconds_range: tuple[float, float] = (0.055, 0.085)
    between_items_seconds_range: tuple[float, float] = (0.015, 0.040)
    after_scroll_seconds_range: tuple[float, float] = (0.085, 0.140)

    @classmethod
    def fast_trial(cls) -> "MouseInputRandomization":
        """Temporary scan-only profile targeting roughly twice the throughput.

        It preserves randomized timing and leaves panel stabilization to the
        scanner's 30 FPS polling loop. Reverting the trial is a one-line
        change at the scanner's default input construction.
        """
        return cls(
            move_seconds_range=(0.012, 0.024),
            hold_seconds_range=(0.010, 0.020),
            after_click_seconds_range=(0.035, 0.055),
            between_items_seconds_range=(0.004, 0.010),
            after_scroll_seconds_range=(0.040, 0.070),
        )

    @classmethod
    def one_point_five_trial(cls) -> "MouseInputRandomization":
        """Balanced scan profile: 1.5x the fast profile's input budget.

        The larger post-click and wheel delays let a 30 FPS game present each
        selected card and consume wheel events before the next input arrives.
        """
        return cls(
            move_seconds_range=(0.015, 0.030),
            hold_seconds_range=(0.012, 0.024),
            after_click_seconds_range=(0.070, 0.085),
            between_items_seconds_range=(0.008, 0.018),
            after_scroll_seconds_range=(0.070, 0.095),
        )

    @classmethod
    def compatibility_low_load(cls) -> "MouseInputRandomization":
        """Lower-rate input for the exceptional compatibility mode."""

        return cls(
            move_seconds_range=(0.035, 0.075),
            hold_seconds_range=(0.024, 0.045),
            after_click_seconds_range=(0.095, 0.135),
            between_items_seconds_range=(0.025, 0.055),
            after_scroll_seconds_range=(0.125, 0.180),
        )

    @staticmethod
    def _uniform(bounds: tuple[float, float], rng: random.Random) -> float:
        return rng.uniform(float(bounds[0]), float(bounds[1]))

    def jitter_position(
        self,
        position: tuple[int, int],
        content_height: int,
        rng: random.Random,
    ) -> tuple[int, int]:
        scale = max(1.0 / 3.0, float(content_height) / 1080.0)
        sigma = self.click_sigma_px_1080 * scale
        limit = self.click_limit_px_1080 * scale

        def sample() -> int:
            value = max(-limit, min(limit, rng.gauss(0.0, sigma)))
            return _round_half_up(value) if value >= 0 else -_round_half_up(-value)

        return int(position[0]) + sample(), int(position[1]) + sample()

    def move_seconds(self, rng: random.Random) -> float:
        return self._uniform(self.move_seconds_range, rng)

    def hold_seconds(self, rng: random.Random) -> float:
        return self._uniform(self.hold_seconds_range, rng)

    def after_click_seconds(self, rng: random.Random) -> float:
        return self._uniform(self.after_click_seconds_range, rng)

    def between_items_seconds(self, rng: random.Random) -> float:
        return self._uniform(self.between_items_seconds_range, rng)

    def after_scroll_seconds(self, rng: random.Random) -> float:
        return self._uniform(self.after_scroll_seconds_range, rng)


class MouseScanInput(Protocol):
    def click(self, position: tuple[int, int], *, content_height: int) -> tuple[int, int]: ...

    def scroll(self, position: tuple[int, int], amount: int) -> None: ...

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        hold_seconds: float,
        duration_seconds: float,
    ) -> None: ...

    def release_left(self) -> None: ...


class PyAutoGuiMouseScanInput:
    """Concrete randomized mouse input used by the full visual scanner."""

    def __init__(
        self,
        *,
        randomization: MouseInputRandomization | None = None,
        rng: random.Random | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        disable_pyautogui_pause: bool = False,
    ) -> None:
        import pyautogui

        self._pyautogui = pyautogui
        self._pyautogui.FAILSAFE = True
        self._randomization = randomization or MouseInputRandomization()
        self._rng = rng or random.Random()
        self._sleep = sleep_fn
        self._disable_pyautogui_pause = bool(disable_pyautogui_pause)
        self._last_scroll_position: tuple[int, int] | None = None

    def _pause_kwargs(self) -> dict[str, bool]:
        return {"_pause": False} if self._disable_pyautogui_pause else {}

    def click(self, position: tuple[int, int], *, content_height: int) -> tuple[int, int]:
        self._last_scroll_position = None
        target = self._randomization.jitter_position(position, content_height, self._rng)
        self._pyautogui.mouseUp(button="left", **self._pause_kwargs())
        self._pyautogui.moveTo(
            *target,
            duration=self._randomization.move_seconds(self._rng),
            **self._pause_kwargs(),
        )
        self._pyautogui.mouseDown(button="left", **self._pause_kwargs())
        try:
            self._sleep(self._randomization.hold_seconds(self._rng))
        finally:
            self._pyautogui.mouseUp(button="left", **self._pause_kwargs())
        self._sleep(self._randomization.after_click_seconds(self._rng))
        return target

    def scroll(self, position: tuple[int, int], amount: int) -> None:
        import ctypes

        if position != self._last_scroll_position:
            self._pyautogui.moveTo(
                *position,
                duration=self._randomization.move_seconds(self._rng),
                **self._pause_kwargs(),
            )
            self._last_scroll_position = position
        # Preserve the reference implementation's Windows wheel delta while
        # moving to the scroll anchor only once for the nine-command sequence.
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(amount), 0)
        self._sleep(self._randomization.after_scroll_seconds(self._rng))

    def drag(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        *,
        hold_seconds: float,
        duration_seconds: float,
    ) -> None:
        """Perform one deliberate list-reset drag without click jitter."""

        self._last_scroll_position = None
        self._pyautogui.mouseUp(button="left", **self._pause_kwargs())
        self._pyautogui.moveTo(
            *start,
            duration=0.15,
            **self._pause_kwargs(),
        )
        self._pyautogui.mouseDown(button="left", **self._pause_kwargs())
        try:
            self._sleep(float(hold_seconds))
            self._pyautogui.moveTo(
                *end,
                duration=float(duration_seconds),
                **self._pause_kwargs(),
            )
            self._sleep(0.3)
        finally:
            self._pyautogui.mouseUp(button="left", **self._pause_kwargs())

    def between_items(self) -> None:
        self._sleep(self._randomization.between_items_seconds(self._rng))

    def release_left(self) -> None:
        self._last_scroll_position = None
        self._pyautogui.mouseUp(button="left", **self._pause_kwargs())
