# 验证倒带执行流程、识别与输入时序。
from __future__ import annotations

import numpy as np
import pytest

from src.domain.drive_layout import SHAPE_FOOTPRINTS
from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls
from src.features.drive_assembly.rewind_execution import (
    RewindExecutionRequest,
    execute_rewind_request,
    parse_rewind_currency,
)
from src.scanner.window_capture import WindowRect


class _Backend:
    def __init__(self) -> None:
        self.clicks: list[tuple[int, int]] = []
        self.keys: list[str] = []
        self.pauses: list[float] = []
        self.events: list[tuple[str, object]] = []

    def click(self, position):
        self.clicks.append(position)
        self.events.append(("click", position))

    def pause(self, seconds):
        self.pauses.append(seconds)
        self.events.append(("pause", seconds))

    def press_key(self, key):
        self.keys.append(key)
        self.events.append(("key", key))


class _Ocr:
    def __init__(self, texts=("572",)) -> None:
        self._texts = texts

    def extract_text(self, _image):
        return list(self._texts)


class _SequenceOcr:
    def __init__(self, *results) -> None:
        self._results = list(results)

    def extract_text(self, _image):
        return list(self._results.pop(0))


def _capture():
    return np.zeros((1440, 2560, 3), dtype=np.uint8), WindowRect(100, 40, 2660, 1480)


def _synthetic_shape_selection() -> tuple[np.ndarray, dict[str, tuple[int, int]]]:
    image = np.full((1440, 2560, 3), 25, dtype=np.uint8)
    centers = tuple(
        map_rewind_controls(screen_size=(2560, 1440))["available_drive_shapes"].values()
    )
    shape_names = tuple(reversed(tuple(SHAPE_FOOTPRINTS)))
    expected: dict[str, tuple[int, int]] = {}
    for center, shape_name in zip(centers, shape_names):
        cells = SHAPE_FOOTPRINTS[shape_name]
        rows = max(row for row, _column in cells) + 1
        columns = max(column for _row, column in cells) + 1
        cell_size = 30
        origin_x = center[0] - columns * cell_size // 2
        origin_y = center[1] - rows * cell_size // 2
        for row, column in cells:
            image[
                origin_y + row * cell_size : origin_y + (row + 1) * cell_size + 1,
                origin_x + column * cell_size : origin_x + (column + 1) * cell_size + 1,
            ] = (205, 70, 230)
        expected[shape_name] = center
    return image, expected


def test_currency_parser_uses_largest_numeric_ocr_fragment() -> None:
    assert parse_rewind_currency(["coin", "572", "8"]) == 572
    assert parse_rewind_currency(["32,740"]) == 32740
    assert parse_rewind_currency(["32", "740"]) == 32740
    assert parse_rewind_currency(["none"]) is None


def test_random_rewind_spends_only_complete_tens_and_double_escapes_each_result() -> None:
    backend = _Backend()
    report = execute_rewind_request(
        RewindExecutionRequest(("blue",), "none"),
        backend=backend,
        capture=_capture,
        ocr_engine=_Ocr(("1,850",)),
    )

    assert report.currency == 1850
    assert report.planned_draws == 30
    assert report.ten_draws == 3
    assert report.single_draws == 0
    assert report.remaining_currency == 50
    assert report.quality_draws == (("blue", 30),)
    assert report.quality_currencies == (("blue", 1850),)
    assert report.quality_remaining_currency == (("blue", 50),)
    assert report.quality_ten_costs == (("blue", 600),)
    assert report.difficulty == "difficulty_low"
    assert backend.clicks == [
        (900, 1346),
        (1086, 651),
        (1865, 1071),
        (1865, 1071),
        (1865, 1071),
    ]
    assert backend.keys == ["esc", "esc"] * 3
    assert backend.pauses == [1.0, 1.0] + [1.0, 1.0, 0.5] * 3
    assert backend.events == [
        ("click", (900, 1346)),
        ("pause", 1.0),
        ("click", (1086, 651)),
        ("pause", 1.0),
        *[
            event
            for _ in range(3)
            for event in (
                ("click", (1865, 1071)),
                ("pause", 1.0),
                ("key", "esc"),
                ("pause", 1.0),
                ("key", "esc"),
                ("pause", 0.5),
            )
        ],
    ]


def test_random_rewind_does_not_click_when_currency_is_below_one_ten() -> None:
    backend = _Backend()
    report = execute_rewind_request(
        RewindExecutionRequest(("gold",), "none"),
        backend=backend,
        capture=_capture,
        ocr_engine=_Ocr(("599",)),
    )

    assert report.planned_draws == 0
    assert report.ten_draws == 0
    assert report.single_draws == 0
    assert report.remaining_currency == 599
    assert backend.clicks == [(1870, 1346)]
    assert backend.keys == []
    assert backend.pauses == [1.0]


def test_blue_quality_always_uses_random_mode_even_when_customization_is_requested() -> None:
    backend = _Backend()
    report = execute_rewind_request(
        RewindExecutionRequest(
            ("blue",),
            "apply_plan",
            ("EquipmentGeometry_Hen2",) * 8,
        ),
        backend=backend,
        capture=_capture,
        ocr_engine=_Ocr(("1,200",)),
    )

    assert report.quality_ten_costs == (("blue", 600),)
    assert report.ten_draws == 2
    assert backend.clicks == [
        (900, 1346),
        (1086, 651),
        (1865, 1071),
        (1865, 1071),
    ]
    assert (1734, 648) not in backend.clicks


def test_screenshot_balance_plans_54_tens_and_leaves_340_currency() -> None:
    backend = _Backend()
    report = execute_rewind_request(
        RewindExecutionRequest(("gold",), "none"),
        backend=backend,
        capture=_capture,
        ocr_engine=_Ocr(("32,740",)),
    )

    assert report.currency == 32740
    assert report.planned_draws == 540
    assert report.ten_draws == 54
    assert report.single_draws == 0
    assert report.remaining_currency == 340
    assert report.quality_currencies == (("gold", 32740),)
    assert report.quality_remaining_currency == (("gold", 340),)
    assert backend.clicks.count((1865, 1071)) == 54
    assert backend.keys == ["esc", "esc"] * 54
    assert backend.pauses == [1.0, 1.0] + [1.0, 1.0, 0.5] * 54


def test_apply_plan_rebuilds_eight_slots_and_exits_customization() -> None:
    backend = _Backend()
    shapes = ("EquipmentGeometry_Hen2",) * 8
    selection_image, expected = _synthetic_shape_selection()
    captures = iter(
        (
            _capture(),
            (selection_image, WindowRect(100, 40, 2660, 1480)),
            _capture(),
        )
    )
    report = execute_rewind_request(
        RewindExecutionRequest(("gold",), "apply_plan", shapes),
        backend=backend,
        capture=lambda: next(captures),
        ocr_engine=_SequenceOcr(("2,400",), ("1,200",)),
    )

    assert report.difficulty == "difficulty_advanced"
    assert report.planned_draws == 20
    assert report.ten_draws == 2
    assert report.single_draws == 0
    assert report.quality_ten_costs == (("gold", 1200),)
    assert backend.keys == ["esc"] * 5
    h2_x, h2_y = expected["H_2"]
    assert backend.clicks.count((100 + h2_x, 40 + h2_y)) == 8
    assert (2190, 1186) in backend.clicks
    assert len(backend.clicks) == 1 + 22 + 2
    first_draw = backend.events.index(("click", (1865, 1071)))
    assert backend.clicks[:6] == [
        (1870, 1346),
        (1734, 648),
        (1380, 1153),
        (1563, 304),
        (1521, 396),
        (1000, 340),
    ]
    setup_pauses = [event for event in backend.events[:first_draw] if event[0] == "pause"]
    assert setup_pauses[:5] == [("pause", 1.0)] * 5
    assert setup_pauses[5:22] == [("pause", 0.2)] * 17
    assert setup_pauses[22:] == [("pause", 1.0), ("pause", 1.0)]


def test_enabled_customization_keeps_existing_pool_without_rebuilding() -> None:
    backend = _Backend()

    class ExistingCustomPoolOcr:
        def __init__(self) -> None:
            self._results = [("2,500",), ("1,200",)]

        def extract_text(self, _image):
            assert backend.clicks[:2] == [(1363, 1346), (1734, 648)]
            return list(self._results.pop(0))

    report = execute_rewind_request(
        RewindExecutionRequest(("purple",), "enabled"),
        backend=backend,
        capture=_capture,
        ocr_engine=ExistingCustomPoolOcr(),
    )

    assert report.difficulty == "difficulty_medium"
    assert report.currency == 2500
    assert report.quality_ten_costs == (("purple", 1200),)
    assert report.planned_draws == 20
    assert report.ten_draws == 2
    assert report.remaining_currency == 100
    assert backend.clicks == [(1363, 1346), (1734, 648), (1865, 1071), (1865, 1071)]
    assert backend.keys == ["esc", "esc"] * 2
    assert backend.pauses == [1.0, 1.0] + [1.0, 1.0, 0.5] * 2


def test_apply_plan_requires_exactly_eight_shapes() -> None:
    with pytest.raises(ValueError, match="8 个驱动候选"):
        execute_rewind_request(
            RewindExecutionRequest(("gold",), "apply_plan", ("EquipmentGeometry_Hen2",)),
            backend=_Backend(),
            capture=_capture,
            ocr_engine=_Ocr(),
        )


def test_multi_quality_selects_each_difficulty_before_reading_its_own_balance() -> None:
    backend = _Backend()

    class DifficultyBalances:
        def __init__(self) -> None:
            self._calls = 0

        def extract_text(self, _image):
            expected_difficulty = ((1870, 1346), (900, 1346))[self._calls]
            assert backend.clicks[-1] == expected_difficulty
            assert backend.pauses[-1] == 1.0
            result = (("1,800",), ("1,200",))[self._calls]
            self._calls += 1
            return list(result)

    report = execute_rewind_request(
        RewindExecutionRequest(("blue", "gold"), "none"),
        backend=backend,
        capture=_capture,
        ocr_engine=DifficultyBalances(),
    )

    assert report.currency == 3000
    assert report.quality_currencies == (("gold", 1800), ("blue", 1200))
    assert report.quality_draws == (("gold", 30), ("blue", 20))
    assert report.quality_remaining_currency == (("gold", 0), ("blue", 0))
    assert report.planned_draws == 50
    assert report.ten_draws == 5
    assert report.single_draws == 0
    assert report.difficulty == "difficulty_advanced"
    assert backend.clicks.count((1865, 1071)) == 5
    assert backend.clicks.index((1870, 1346)) < backend.clicks.index((900, 1346))


def test_custom_pool_reads_a_separate_ten_cost_for_each_quality() -> None:
    backend = _Backend()
    report = execute_rewind_request(
        RewindExecutionRequest(("purple", "gold"), "enabled"),
        backend=backend,
        capture=_capture,
        ocr_engine=_SequenceOcr(
            ("2,500",),
            ("1,200",),
            ("3,700",),
            ("1,800",),
        ),
    )

    assert report.quality_currencies == (("gold", 2500), ("purple", 3700))
    assert report.quality_ten_costs == (("gold", 1200), ("purple", 1800))
    assert report.quality_draws == (("gold", 20), ("purple", 20))
    assert report.quality_remaining_currency == (("gold", 100), ("purple", 100))
    assert report.ten_draws == 4


def test_rewind_controls_scale_to_the_1920_by_1152_game_canvas() -> None:
    from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls

    controls = map_rewind_controls(screen_size=(1920, 1152))

    assert controls["currency_counter_region"] == (1253, 71, 1429, 134)
    assert controls["custom_ten_cost_region"] == (1230, 848, 1358, 900)
    assert controls["random_selection"] == (740, 458)
    assert controls["rewind_ten"] == (1324, 773)
    assert controls["difficulty_low"] == (600, 980)
    assert controls["difficulty_medium"] == (947, 980)
    assert controls["difficulty_advanced"] == (1328, 980)
