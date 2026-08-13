# 使用候选卡片内的彩色轮廓和官方占格拓扑识别倒带自选驱动。
"""OpenCV recognition for the twelve rewind custom-pool shape cards."""

from __future__ import annotations

import math
from collections.abc import Sequence

import cv2
import numpy as np

from src.domain.drive_layout import SHAPE_FOOTPRINTS


_SHAPE_NAMES = tuple(SHAPE_FOOTPRINTS)
_REFERENCE_SIZE = (2560, 1440)
_REFERENCE_ROI_RADIUS = 75
_NORMALIZED_CELL_SIZE = 40
_MIN_ASSIGNMENT_SCORE = 0.42


def detect_rewind_shape_positions(
    image: object,
    candidate_centers: Sequence[tuple[int, int]],
) -> dict[str, tuple[int, int]]:
    """Return the visible click position for every official rewind shape.

    Candidate card slots remain stable, while their order and blue/purple/gold
    artwork are not treated as identity.  Each card is segmented with several
    saturation/value masks, scored against the authoritative cell footprints,
    then assigned globally so every official shape is used exactly once.
    """

    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] < 3:
        raise RuntimeError("倒带自选页截图格式无效")
    if len(candidate_centers) != len(_SHAPE_NAMES):
        raise RuntimeError(
            f"倒带自选页候选槽位应为 {len(_SHAPE_NAMES)} 个，实际为 {len(candidate_centers)} 个"
        )

    height, width = array.shape[:2]
    scale = min(width / _REFERENCE_SIZE[0], height / _REFERENCE_SIZE[1])
    radius = max(36, round(_REFERENCE_ROI_RADIUS * scale))
    score_rows = [
        _score_candidate(array, center, radius, scale)
        for center in candidate_centers
    ]
    assignments, total_score = _best_unique_assignment(score_rows)
    weak = [
        (index, _SHAPE_NAMES[shape_index], score_rows[index][shape_index])
        for index, shape_index in enumerate(assignments)
        if score_rows[index][shape_index] < _MIN_ASSIGNMENT_SCORE
    ]
    if weak:
        details = ", ".join(
            f"槽位{index + 1}:{name}={score:.2f}" for index, name, score in weak
        )
        raise RuntimeError(f"倒带自选页形状识别置信度不足（{details}；总分 {total_score:.2f}）")

    return {
        _SHAPE_NAMES[shape_index]: (
            int(round(candidate_centers[index][0])),
            int(round(candidate_centers[index][1])),
        )
        for index, shape_index in enumerate(assignments)
    }


def _score_candidate(
    image: np.ndarray,
    center: tuple[int, int],
    radius: int,
    scale: float,
) -> tuple[float, ...]:
    height, width = image.shape[:2]
    cx, cy = center
    x1, y1 = max(0, cx - radius), max(0, cy - radius)
    x2, y2 = min(width, cx + radius), min(height, cy + radius)
    roi = image[y1:y2, x1:x2, :3]
    if roi.size == 0:
        return tuple(float("-inf") for _ in _SHAPE_NAMES)

    silhouettes = _candidate_silhouettes(roi, scale)
    if not silhouettes:
        return tuple(float("-inf") for _ in _SHAPE_NAMES)
    return tuple(
        max(_footprint_score(silhouette, SHAPE_FOOTPRINTS[name]) for silhouette in silhouettes)
        for name in _SHAPE_NAMES
    )


def _candidate_silhouettes(roi: np.ndarray, scale: float) -> list[np.ndarray]:
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    bgr: np.ndarray = roi.astype(np.int16)
    channel_max = bgr.max(axis=2)
    channel_min = bgr.min(axis=2)
    masks = (
        ((hsv[:, :, 1] >= 55) & (hsv[:, :, 2] >= 135)),
        ((hsv[:, :, 1] >= 35) & (hsv[:, :, 2] >= 170)),
        (((channel_max - channel_min) >= 30) & (channel_max >= 120)),
    )
    min_area = max(120, round(450 * scale * scale))
    max_area = max(min_area + 1, round(11000 * scale * scale))
    min_side = max(10, round(20 * scale))
    roi_center = (roi.shape[1] / 2.0, roi.shape[0] / 2.0)
    max_center_distance = max(24.0, roi.shape[0] * 0.46)
    silhouettes: list[np.ndarray] = []

    for raw_mask in masks:
        mask = (raw_mask.astype(np.uint8) * 255)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
        components: list[tuple[float, int, int, int, int, int, int]] = []
        for label in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[label])
            distance = math.hypot(
                x + width / 2.0 - roi_center[0],
                y + height / 2.0 - roi_center[1],
            )
            if (
                min_area <= area <= max_area
                and width >= min_side
                and height >= min_side
                and distance <= max_center_distance
            ):
                components.append((distance, -area, x, y, width, height, label))
        for _distance, _negative_area, x, y, width, height, label in sorted(components)[:2]:
            crop = (labels[y : y + height, x : x + width] == label).astype(np.uint8)
            silhouettes.append(crop)
    return silhouettes


def _footprint_score(
    silhouette: np.ndarray,
    footprint: tuple[tuple[int, int], ...],
) -> float:
    rows = max(row for row, _column in footprint) + 1
    columns = max(column for _row, column in footprint) + 1
    normalized = cv2.resize(
        silhouette,
        (columns * _NORMALIZED_CELL_SIZE, rows * _NORMALIZED_CELL_SIZE),
        interpolation=cv2.INTER_AREA,
    ) >= 0.40
    template = np.zeros_like(normalized, dtype=bool)
    for row, column in footprint:
        template[
            row * _NORMALIZED_CELL_SIZE : (row + 1) * _NORMALIZED_CELL_SIZE,
            column * _NORMALIZED_CELL_SIZE : (column + 1) * _NORMALIZED_CELL_SIZE,
        ] = True
    union = np.logical_or(normalized, template).sum()
    if union == 0:
        return float("-inf")
    intersection_over_union = float(np.logical_and(normalized, template).sum() / union)
    observed_ratio = silhouette.shape[1] / max(1, silhouette.shape[0])
    expected_ratio = columns / rows
    aspect_penalty = abs(math.log(observed_ratio / expected_ratio))
    return intersection_over_union - 0.60 * aspect_penalty


def _best_unique_assignment(score_rows: Sequence[Sequence[float]]) -> tuple[tuple[int, ...], float]:
    """Solve the small 12×12 maximum-weight assignment with bitmask DP."""

    states: dict[int, tuple[float, tuple[int, ...]]] = {0: (0.0, ())}
    for row in score_rows:
        next_states: dict[int, tuple[float, tuple[int, ...]]] = {}
        for used, (current_score, assignment) in states.items():
            for shape_index, score in enumerate(row):
                bit = 1 << shape_index
                if used & bit or not math.isfinite(score):
                    continue
                candidate = (current_score + score, assignment + (shape_index,))
                previous = next_states.get(used | bit)
                if previous is None or candidate[0] > previous[0]:
                    next_states[used | bit] = candidate
        states = next_states
    complete = states.get((1 << len(_SHAPE_NAMES)) - 1)
    if complete is None:
        raise RuntimeError("倒带自选页未形成完整的 12 种驱动形状匹配")
    score, assignment = complete
    return assignment, score
