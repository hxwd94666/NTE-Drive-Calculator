# 验证倒带执行流程、识别与输入时序。
from __future__ import annotations

from concurrent.futures import CancelledError
from threading import Event, Thread
from unittest.mock import Mock, call

import numpy as np
import pytest

from src.domain.drive_layout import SHAPE_FOOTPRINTS
from src.features.drive_assembly.rewind import execution
from src.features.drive_assembly.input_backends import PyAutoGuiMouseBackend
from src.features.drive_assembly.randomization import RandomizationContext
from src.integrations.operation_guard import GuardedGuiInput
from src.integrations.vision import window_capture
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls
from src.features.drive_assembly.rewind.execution import (
    RewindExecutionRequest,
    execute_rewind_request,
    parse_rewind_currency,
)
from src.integrations.vision.window_capture import WindowRect


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


def test_rewind_execution_honors_an_explicit_stop_before_capturing() -> None:
    captured = False

    def capture():
        nonlocal captured
        captured = True
        return _capture()

    with pytest.raises(RuntimeError, match="倒带执行已停止"):
        execute_rewind_request(
            RewindExecutionRequest(),
            capture=capture,
            should_stop=lambda: True,
        )

    assert not captured


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




def test_apply_plan_rebuilds_eight_slots_and_confirm_returns_to_draw_page() -> None:
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
    assert backend.keys == ["esc"] * 4
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
    assert setup_pauses[22:] == [("pause", 1.0)]


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


class _OwnedBackend(_Backend):
    def __init__(self, *, falsey=False, input_error=None, setup_error=None,
                 release_error=None, close_error=None) -> None:
        super().__init__()
        self.falsey = falsey
        self.input_error = input_error
        self.setup_error = setup_error
        self.release_error = release_error
        self.close_error = close_error
        self.closed = False

    def __bool__(self):
        return not self.falsey

    def enable_randomization(self):
        if self.setup_error is not None:
            raise self.setup_error

    def click(self, position):
        if self.input_error is not None:
            raise self.input_error
        super().click(position)

    def force_mouse_release(self):
        self.events.append(("release", None))
        if self.release_error is not None:
            raise self.release_error

    def close(self):
        self.events.append(("close", None))
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@pytest.mark.parametrize("outcome", ["success", "cancel", "ocr_fatal", "input_error", "setup_error"])
def test_default_rewind_owner_closes_on_each_execution_outcome(monkeypatch, outcome) -> None:
    stop = Event()
    failure = OcrModelResourceError("synthetic fatal") if outcome == "ocr_fatal" else ValueError(outcome)
    backend = _OwnedBackend(
        input_error=failure if outcome == "input_error" else None,
        setup_error=failure if outcome == "setup_error" else None,
    )
    factory = Mock(return_value=backend)
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", factory)

    class OutcomeOcr(_Ocr):
        def extract_text(self, image):
            if outcome == "ocr_fatal":
                raise failure
            if outcome == "cancel":
                stop.set()
            return super().extract_text(image)

    arguments = dict(capture=_capture, ocr_engine=OutcomeOcr(("599",)), should_stop=stop.is_set)
    if outcome == "success":
        report = execute_rewind_request(RewindExecutionRequest(), **arguments)
        assert report.planned_draws == 0 and report.executed_actions == 1
    else:
        with pytest.raises(RuntimeError if outcome == "cancel" else type(failure)) as caught:
            execute_rewind_request(RewindExecutionRequest(), **arguments)
        if outcome == "cancel":
            assert str(caught.value) == "倒带执行已停止"
            assert isinstance(caught.value.__cause__, CancelledError)
        else:
            assert caught.value is failure
    factory.assert_called_once_with()
    assert backend.events[-2:] == [("release", None), ("close", None)]
    assert backend.closed and backend.keys == []
    assert len(backend.clicks) == (0 if outcome in {"input_error", "setup_error"} else 1)


def test_default_rewind_owner_keeps_real_missing_guard_denial_and_releases(monkeypatch) -> None:
    # 真实授权/收尾方法只接合成驱动，构造器与系统输入不参与本例。
    backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
    backend.operation_guard = None
    backend._randomization = RandomizationContext()
    backend._send_input = Mock(available=False)
    driver, gamepad, ordered = Mock(), Mock(), Mock()
    ordered.attach_mock(driver.mouseUp, "release")
    ordered.attach_mock(gamepad.close, "close")
    backend._pyautogui = GuardedGuiInput(driver, None)
    backend._gamepad = gamepad
    backend._record_mouse_delivery = Mock()
    backend._sleeper = Mock()
    factory, ocr = Mock(return_value=backend), Mock()
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", factory)
    with pytest.raises(PermissionError, match="未配置工作模式执行授权"):
        execute_rewind_request(
            RewindExecutionRequest(), capture=_capture, ocr_engine=ocr, should_stop=lambda: False,
        )
    factory.assert_called_once_with()
    assert backend.operation_guard is None
    assert ordered.mock_calls == [call.release(button="left"), call.close()]
    driver.moveTo.assert_not_called()
    driver.mouseDown.assert_not_called()
    driver.press.assert_not_called()
    backend._send_input.click.assert_not_called()
    ocr.extract_text.assert_not_called()


@pytest.mark.parametrize("falsey", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_rewind_borrows_even_falsey_backend_without_replacing_or_closing(monkeypatch, falsey, fails) -> None:
    failure = ValueError("borrowed input failure")
    backend = _OwnedBackend(falsey=falsey, input_error=failure if fails else None)
    factory = Mock(side_effect=AssertionError("borrowed backend was replaced"))
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", factory)
    arguments = dict(backend=backend, capture=_capture, ocr_engine=_Ocr(("599",)), should_stop=lambda: False)
    if fails:
        with pytest.raises(ValueError) as caught:
            execute_rewind_request(RewindExecutionRequest(), **arguments)
        assert caught.value is failure
    else:
        assert execute_rewind_request(RewindExecutionRequest(), **arguments).executed_actions == 1
        assert len(backend.clicks) == 1
    factory.assert_not_called()
    assert not backend.closed
    assert all(event[0] not in {"release", "close"} for event in backend.events)


@pytest.mark.parametrize("cleanup_stage", ["release", "close", "both"])
@pytest.mark.parametrize("has_primary_error", [False, True])
def test_rewind_cleanup_faults_still_close_and_keep_primary_failure(monkeypatch, cleanup_stage, has_primary_error) -> None:
    primary = ValueError("synthetic input failure")
    release_error, close_error = RuntimeError("release failed"), RuntimeError("close failed")
    backend = _OwnedBackend(
        input_error=primary if has_primary_error else None,
        release_error=release_error if cleanup_stage != "close" else None,
        close_error=close_error if cleanup_stage != "release" else None,
    )
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", Mock(return_value=backend))
    with pytest.raises(ValueError if has_primary_error else RuntimeError) as caught:
        execute_rewind_request(
            RewindExecutionRequest(), capture=_capture, ocr_engine=_Ocr(("599",)), should_stop=lambda: False,
        )
    if has_primary_error:
        assert caught.value is primary
        detail = " ".join(caught.value.__notes__)
    else:
        detail = str(caught.value)
        assert caught.value.__cause__ is (close_error if cleanup_stage == "close" else release_error)
    assert "倒带输入收尾失败" in detail
    assert ("release: RuntimeError: release failed" in detail) == (cleanup_stage != "close")
    assert ("close: RuntimeError: close failed" in detail) == (cleanup_stage != "release")
    assert backend.events[-2:] == [("release", None), ("close", None)]


@pytest.mark.parametrize("stage", ["initial_capture", "ocr_scope"])
def test_rewind_failure_before_backend_creation_has_no_input_owner(monkeypatch, stage) -> None:
    failure = RuntimeError("synthetic early failure")
    factory = Mock(side_effect=AssertionError("early failure created backend"))
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", factory)
    capture = Mock(side_effect=failure) if stage == "initial_capture" else _capture
    if stage == "ocr_scope":
        monkeypatch.setattr(execution, "use_ocr_session", Mock(side_effect=failure))
    with pytest.raises(RuntimeError) as caught:
        execute_rewind_request(RewindExecutionRequest(), capture=capture, should_stop=lambda: False)
    assert caught.value is failure
    factory.assert_not_called()


def test_rewind_cancel_waits_for_native_ocr_return_before_closing_owner(monkeypatch) -> None:
    entered, return_native, returned, stopped, finished = (Event() for _ in range(5))
    errors: list[BaseException] = []

    class BlockingOcr:
        close_calls = 0

        def extract_text(self, _image):
            entered.set()
            if not return_native.wait(5):
                raise TimeoutError("synthetic native barrier")
            returned.set()
            return ["600"]

        def close(self):
            self.close_calls += 1

    class PhysicalOwner(_OwnedBackend):
        def force_mouse_release(self):
            assert returned.is_set(), "release preceded native physical return"
            super().force_mouse_release()

        def close(self):
            assert returned.is_set(), "close preceded native physical return"
            super().close()

    backend, engine = PhysicalOwner(), BlockingOcr()
    monkeypatch.setattr(execution, "PyAutoGuiMouseBackend", Mock(return_value=backend))

    def run():
        try:
            execute_rewind_request(
                RewindExecutionRequest(), capture=_capture, ocr_engine=engine, should_stop=stopped.is_set,
            )
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()

    worker = Thread(target=run, name="synthetic-rewind-owner")
    worker.start()
    try:
        assert entered.wait(3)
        stopped.set()
        assert not returned.is_set() and not finished.is_set() and worker.is_alive()
        assert not backend.closed and all(event[0] not in {"release", "close"} for event in backend.events)
    finally:
        return_native.set()
        worker.join(5)
    assert not worker.is_alive() and finished.is_set()
    assert len(errors) == 1 and str(errors[0]) == "倒带执行已停止"
    assert isinstance(errors[0].__cause__, CancelledError)
    assert backend.closed and backend.events[-2:] == [("release", None), ("close", None)]
    assert len(backend.clicks) == 1 and backend.keys == []
    assert engine.close_calls == 0


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_capture_mss_ownership_on_synthetic_grab_success_and_failure(monkeypatch, borrowed, fails) -> None:
    failure, image = RuntimeError("synthetic grab failure"), object()
    rect = WindowRect(10, 20, 110, 120)

    class FakeMss:
        def __init__(self):
            self.events = []

        def __bool__(self):
            return False  # 借入的假值对象同样不是创建新资源的理由。

        def __enter__(self):
            self.events.append("enter")
            return self

        def __exit__(self, kind, _error, _traceback):
            self.events.append(("exit", kind))
            self.close()

        def close(self):
            self.events.append("close")

        def grab(self, monitor):
            self.events.append(("grab", monitor))
            if fails:
                raise failure
            return image

    sct = FakeMss()
    factory = Mock(return_value=sct)
    monkeypatch.setattr(window_capture, "get_foreground_client_rect", lambda: rect)
    monkeypatch.setattr(window_capture.mss, "MSS", factory)
    if fails:
        with pytest.raises(RuntimeError) as caught:
            window_capture.capture_foreground_window(sct if borrowed else None)
        assert caught.value is failure
    else:
        assert window_capture.capture_foreground_window(sct if borrowed else None) == (image, rect)
    grab = ("grab", rect.to_mss_monitor())
    if borrowed:
        factory.assert_not_called()
        assert sct.events == [grab]
    else:
        factory.assert_called_once_with()
        assert sct.events == ["enter", grab, ("exit", RuntimeError if fails else None), "close"]
