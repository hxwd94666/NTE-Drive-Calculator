# 提供低算力的鼠标扫描网格占用与局部滚动跟踪。
"""Low-cost grid occupancy and local vertical tracking for mouse scans."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class MouseGridOccupancy:
    """Row-major occupancy for the currently scannable part of the grid."""

    occupied: tuple[bool, ...]
    contiguous_count: int
    has_gap: bool


def detect_grid_occupancy(
    image: np.ndarray,
    centers: tuple[tuple[int, int], ...],
    *,
    scale: float,
) -> MouseGridOccupancy:
    """Classify card slots using tiny strided grayscale samples.

    An empty inventory slot is the nearly uniform black list background.  Real
    cards of every rarity contain a frame and glyph, so both standard deviation
    and robust contrast remain well above the conservative thresholds.  The
    operation examines less than one percent of a 2K frame and performs no OCR.
    """

    half_width = max(24, int(round(70 * float(scale))))
    half_height = max(20, int(round(58 * float(scale))))
    height, width = image.shape[:2]
    states: list[bool] = []
    for center_x, center_y in centers:
        roi = image[
            max(0, center_y - half_height) : min(height, center_y + half_height),
            max(0, center_x - half_width) : min(width, center_x + half_width),
        ]
        if roi.size == 0:
            states.append(False)
            continue
        sample = roi[::4, ::4, :3]
        gray = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
        low, high = np.percentile(gray, (10, 90))
        states.append(bool(float(gray.std()) >= 8.0 and float(high - low) >= 20.0))

    contiguous = 0
    found_empty = False
    has_gap = False
    for occupied in states:
        if occupied and found_empty:
            has_gap = True
        elif occupied:
            contiguous += 1
        else:
            found_empty = True
    return MouseGridOccupancy(tuple(states), contiguous, has_gap)


def detect_selected_card_center_y(
    image: np.ndarray,
    *,
    expected_x: int,
    scale: float,
) -> float | None:
    """Locate the selected card's pink border in one narrow grid column."""

    height, width = image.shape[:2]
    half_width = max(45, int(round(125 * float(scale))))
    left = max(0, int(expected_x) - half_width)
    right = min(width, int(expected_x) + half_width)
    top = max(0, int(round(140 * float(scale))))
    bottom = min(height, int(round(1260 * float(scale))))
    roi = image[top:bottom, left:right, :3]
    if roi.size == 0:
        return None
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[:, :, 0] >= 140)
        & (hsv[:, :, 0] <= 179)
        & (hsv[:, :, 1] >= 100)
        & (hsv[:, :, 2] >= 120)
    ).astype(np.uint8)
    count, _labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    candidates: list[tuple[int, float]] = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        component_width = int(stats[index, cv2.CC_STAT_WIDTH])
        component_height = int(stats[index, cv2.CC_STAT_HEIGHT])
        if area >= max(20, int(round(120 * scale * scale))) and component_width >= 20 * scale:
            if component_height >= 20 * scale:
                candidates.append((area, top + float(centers[index][1])))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]

