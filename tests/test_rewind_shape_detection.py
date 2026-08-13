# 验证倒带驱动形状的视觉识别。
from __future__ import annotations

import cv2
import numpy as np
import pytest

from src.domain.drive_layout import SHAPE_FOOTPRINTS
from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls
from src.features.drive_assembly.rewind_shape_detection import (
    detect_rewind_shape_positions,
)


def _selection_image(color: tuple[int, int, int]) -> tuple[
    np.ndarray, tuple[tuple[int, int], ...], dict[str, tuple[int, int]]
]:
    image = np.full((1440, 2560, 3), 24, dtype=np.uint8)
    centers = tuple(
        map_rewind_controls(screen_size=(2560, 1440))["available_drive_shapes"].values()
    )
    names = tuple(reversed(tuple(SHAPE_FOOTPRINTS)))
    expected: dict[str, tuple[int, int]] = {}
    for center, name in zip(centers, names):
        cells = SHAPE_FOOTPRINTS[name]
        rows = max(row for row, _column in cells) + 1
        columns = max(column for _row, column in cells) + 1
        cell_size = 30
        origin_x = center[0] - columns * cell_size // 2
        origin_y = center[1] - rows * cell_size // 2
        for row, column in cells:
            start = (origin_x + column * cell_size, origin_y + row * cell_size)
            end = (start[0] + cell_size, start[1] + cell_size)
            cv2.rectangle(image, start, end, color, -1)
        expected[name] = center
    return image, centers, expected


@pytest.mark.parametrize(
    "color",
    (
        (255, 160, 40),  # 蓝色品质（BGR）
        (205, 70, 230),  # 紫色品质（BGR）
        (50, 180, 255),  # 金色品质（BGR）
    ),
)
def test_recognizes_the_same_shuffled_shapes_for_every_quality_color(
    color: tuple[int, int, int],
) -> None:
    image, centers, expected = _selection_image(color)

    detected = detect_rewind_shape_positions(image, centers)

    assert detected == expected


def test_rejects_a_selection_page_without_visible_shape_candidates() -> None:
    image = np.zeros((1440, 2560, 3), dtype=np.uint8)
    centers = tuple(
        map_rewind_controls(screen_size=(2560, 1440))["available_drive_shapes"].values()
    )

    with pytest.raises(RuntimeError, match="完整的 12 种驱动形状匹配"):
        detect_rewind_shape_positions(image, centers)


@pytest.mark.parametrize(
    "screen_size",
    ((1920, 1080), (1920, 1152), (2560, 1440), (3840, 2160), (1600, 900)),
)
def test_recognition_scales_with_the_captured_game_canvas(
    screen_size: tuple[int, int],
) -> None:
    source, _source_centers, _expected = _selection_image((255, 160, 40))
    image = cv2.resize(source, screen_size)
    centers = tuple(
        map_rewind_controls(screen_size=screen_size)["available_drive_shapes"].values()
    )
    names = tuple(reversed(tuple(SHAPE_FOOTPRINTS)))

    detected = detect_rewind_shape_positions(image, centers)

    assert detected == {name: center for center, name in zip(centers, names)}
