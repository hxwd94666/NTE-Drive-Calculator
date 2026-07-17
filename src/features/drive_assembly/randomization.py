# 为鼠标拖拽和点击操作提供可控的随机化偏移。
"""Controlled randomization for mouse drag, click, and movement actions."""

from __future__ import annotations

from dataclasses import dataclass, field
import random


@dataclass
class RandomizationContext:
    """Controls randomization behaviour for mouse automation.

    All randomization is **disabled by default**.  Set ``enabled=True`` or
    pass ``RandomizationContext(enabled=True)`` to a backend to activate it.

    Each context owns a dedicated ``random.Random`` instance so that tests
    can seed it independently without affecting global state.
    """

    enabled: bool = False
    rng: random.Random = field(default_factory=random.Random)

    # Position offsets (pixels — per-axis, symmetrical ± range)
    click_offset_range: int = 3
    drag_start_offset_range: int = 2
    drag_end_offset_range: int = 3

    # Timing jitter as a fraction of the base value (0.0 – 1.0)
    timing_jitter_fraction: float = 0.15

    # Perpendicular path noise during drag movement (pixels)
    path_noise_pixels: int = 5

    # ------------------------------------------------------------------
    # seed helper
    # ------------------------------------------------------------------

    def seed(self, value: int) -> None:
        """Reset the internal RNG with *value* so tests are reproducible."""
        self.rng.seed(value)


# ------------------------------------------------------------------
# Position helpers
# ------------------------------------------------------------------


def jitter_position(
    ctx: RandomizationContext,
    position: tuple[int, int],
    max_offset: int,
) -> tuple[int, int]:
    """Return *position* shifted by at most ±*max_offset* on each axis.

    When *ctx* is disabled or *max_offset* ≤ 0 the original position is
    returned unchanged.
    """
    if not ctx.enabled or max_offset <= 0:
        return position
    dx = ctx.rng.randint(-max_offset, max_offset)
    dy = ctx.rng.randint(-max_offset, max_offset)
    return (position[0] + dx, position[1] + dy)


def jitter_scroll_endpoint(
    ctx: RandomizationContext,
    start: tuple[int, int],
    end: tuple[int, int],
    max_offset: int,
) -> tuple[int, int]:
    """Like :func:`jitter_position` but **preserves the scroll direction**.

    The jittered *end* coordinate will never cross *start* on either
    axis, which prevents accidental scroll reversals on very short drags.
    """
    if not ctx.enabled or max_offset <= 0:
        return end
    dx = ctx.rng.randint(-max_offset, max_offset)
    dy = ctx.rng.randint(-max_offset, max_offset)
    new_x = end[0] + dx
    new_y = end[1] + dy
    # Clamp so the signed axis deltas do not flip.
    if start[0] < end[0]:
        new_x = max(new_x, start[0] + 1)
    elif start[0] > end[0]:
        new_x = min(new_x, start[0] - 1)
    if start[1] < end[1]:
        new_y = max(new_y, start[1] + 1)
    elif start[1] > end[1]:
        new_y = min(new_y, start[1] - 1)
    return (new_x, new_y)


# ------------------------------------------------------------------
# Timing helpers
# ------------------------------------------------------------------


def jitter_timing(
    ctx: RandomizationContext,
    base_seconds: float,
) -> float:
    """Return *base_seconds* multiplied by ``1 ± timing_jitter_fraction``.

    When *ctx* is disabled the base value is returned unchanged.
    The result is never negative.
    """
    if not ctx.enabled or ctx.timing_jitter_fraction <= 0.0 or base_seconds <= 0.0:
        return base_seconds
    factor = 1.0 + ctx.rng.uniform(
        -ctx.timing_jitter_fraction,
        ctx.timing_jitter_fraction,
    )
    return max(0.0, base_seconds * factor)


def jitter_duration_ms(
    ctx: RandomizationContext,
    base_ms: int,
) -> int:
    """Convenience wrapper around :func:`jitter_timing` for millisecond values."""
    jittered = jitter_timing(ctx, float(base_ms) / 1000.0)
    return max(1, round(jittered * 1000.0))


# ------------------------------------------------------------------
# Path noise helpers
# ------------------------------------------------------------------


def path_noise_offset(ctx: RandomizationContext) -> tuple[int, int]:
    """Return a small random (dx, dy) for mid-path deviation.

    Returns ``(0, 0)`` when disabled.
    """
    if not ctx.enabled or ctx.path_noise_pixels <= 0:
        return (0, 0)
    max_px = ctx.path_noise_pixels
    return (
        ctx.rng.randint(-max_px, max_px),
        ctx.rng.randint(-max_px, max_px),
    )
