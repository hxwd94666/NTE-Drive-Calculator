# 使用合成窗口、截图和系统输入验证倒带绑定、失焦及输入拒绝，不接触真实桌面。
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from src.domain.localization.catalog import make_catalog, parameter_names
from src.features.drive_assembly import input_backends
from src.features.drive_assembly.input_backends import (
    GuardedGuiInput, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MOVE, PyAutoGuiMouseBackend, _WindowsSendInputMouseDriver,
)
from src.features.drive_assembly.randomization import RandomizationContext
from src.features.drive_assembly.rewind.execution import RewindExecutionRequest, execute_rewind_request
from src.integrations.vision.game_input_target import (
    GameWindowState, Win32WindowSystem, create_game_input_target,
)
from src.integrations.vision.window_capture import WindowRect

NTE_TEST_TIER = "core"
EXECUTABLE = r"C:\fixture\HTGame.exe"
RECT = WindowRect(100, 40, 2660, 1480)


class SyntheticWindows:
    def __init__(self):
        self.state = GameWindowState(100, 20, 12345, EXECUTABLE, RECT)
        self.current = 900  # calculator
        self.activation_attempts = []
        self.additional = []

    def windows(self):
        return (100, *(value.hwnd for value in self.additional))

    def inspect(self, hwnd):
        if hwnd == 100:
            return self.state
        return next((value for value in self.additional if value.hwnd == hwnd), None)

    def foreground(self):
        return self.current

    def activate(self, hwnd):
        self.activation_attempts.append(hwnd)
        raise AssertionError("rewind must not steal foreground or restore another application window")


def target_fixture():
    windows = SyntheticWindows()
    image = np.zeros((1440, 2560, 3), dtype=np.uint8)
    grab = Mock(return_value=image)
    target = create_game_input_target(EXECUTABLE, system=windows, grab=grab)
    windows.current = 100  # owner 完成最小化后，后面的游戏自然接过前台。
    target.bind_foreground()
    return windows, target, grab


def test_valid_target_binds_without_activation_and_accepts_windows_path_case():
    windows = SyntheticWindows()
    grab = Mock(return_value=object())
    target = create_game_input_target(r'"c:\FIXTURE\htgame.EXE"', system=windows, grab=grab)
    assert windows.activation_attempts == [] and not grab.called
    windows.current = 100
    target.bind_foreground()
    target.bind_foreground()
    target.check()
    assert target.capture() == (grab.return_value, RECT)
    assert windows.activation_attempts == []
    grab.assert_called_once_with(RECT)


@pytest.mark.parametrize("path", ["", "HTGame.exe", r"C:\fixture\Calc.exe", r"relative\HTGame.exe"])
def test_invalid_configured_path_never_discovers_activates_or_captures(path):
    windows = Mock()
    with pytest.raises(RuntimeError, match="target_path_required"):
        create_game_input_target(path, system=windows)
    assert windows.mock_calls == []


@pytest.mark.parametrize("kind,code", [("missing", "target_window_missing"),
    ("other_path", "target_window_missing"), ("ambiguous", "target_window_ambiguous")])
def test_binding_requires_one_window_from_full_configured_path(kind, code):
    windows = SyntheticWindows()
    if kind == "missing":
        windows.state = None
    elif kind == "other_path":
        windows.state = replace(windows.state, executable=r"C:\other\HTGame.exe")
    else:
        windows.additional = [replace(windows.state, hwnd=200)]
    with pytest.raises(RuntimeError, match=code):
        create_game_input_target(EXECUTABLE, system=windows)
    assert windows.activation_attempts == []


@pytest.mark.parametrize("change,code", [
    ("focus", "target_focus_lost"), ("minimized", "target_focus_lost"),
    ("closed", "target_window_changed"), ("hwnd", "target_window_changed"),
    ("pid", "target_window_changed"), ("started", "target_window_changed"),
    ("path", "target_window_changed"), ("moved", "target_geometry_changed"),
    ("resized", "target_geometry_changed"),
])
def test_target_change_latches_and_never_resumes_when_original_window_returns(change, code):
    windows, target, grab = target_fixture()
    original = windows.state
    if change == "focus":
        windows.current = 900
    elif change == "minimized":
        windows.state = replace(original, minimized=True)
    elif change == "closed":
        windows.state = None
    elif change == "hwnd":
        windows.state = replace(original, hwnd=101)
    elif change == "pid":
        windows.state = replace(original, pid=21)
    elif change == "started":
        windows.state = replace(original, process_started=12346)
    elif change == "path":
        windows.state = replace(original, executable=r"C:\fixture\Calc.exe")
    elif change == "moved":
        windows.state = replace(original, rect=WindowRect(101, 40, 2661, 1480))
    else:
        windows.state = replace(original, rect=WindowRect(100, 40, 2660, 1500))
    with pytest.raises(RuntimeError, match=code):
        target.capture()
    grab.assert_not_called()
    windows.current, windows.state = 100, original
    for action in (target.check, target.bind_foreground, target.capture):
        with pytest.raises(RuntimeError, match=code):
            action()
    assert windows.activation_attempts == []


def test_game_still_in_background_after_settle_has_no_input_or_foreground_steal():
    windows = SyntheticWindows()
    grab = Mock()
    target = create_game_input_target(EXECUTABLE, system=windows, grab=grab)
    with pytest.raises(RuntimeError, match="target_activation_failed"):
        target.bind_foreground()
    windows.current = 100
    with pytest.raises(RuntimeError, match="target_activation_failed"):
        target.capture()
    grab.assert_not_called()
    assert windows.activation_attempts == []


def test_initially_minimized_game_is_not_automatically_restored():
    windows = SyntheticWindows()
    windows.state = replace(windows.state, minimized=True)
    target = create_game_input_target(EXECUTABLE, system=windows, grab=Mock())
    with pytest.raises(RuntimeError, match="target_activation_failed"):
        target.bind_foreground()
    assert windows.current == 900 and windows.activation_attempts == []


def test_focus_change_while_grabbing_discards_image_and_latches_failure():
    windows, target, grab = target_fixture()
    grab.side_effect = lambda _rect: setattr(windows, "current", 900)
    with pytest.raises(RuntimeError, match="target_focus_lost"):
        target.capture()
    windows.current = 100
    with pytest.raises(RuntimeError, match="target_focus_lost"):
        target.check()
    assert grab.call_count == 1


def guarded_backend(target):
    backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
    backend.operation_guard = lambda _capability: target.check()
    backend._randomization = RandomizationContext()
    driver = Mock()
    backend._pyautogui = GuardedGuiInput(driver, backend.operation_guard)
    backend._send_input = SimpleNamespace(available=False)
    backend._gamepad = Mock()
    backend._sleeper = Mock()
    return backend, driver


@pytest.mark.parametrize("during,remaining", [("ocr", "600"), ("ocr", "599"),
    ("result_wait", "600"), ("final_wait", "600")])
def test_full_execution_never_clicks_or_presses_calculator_after_ocr_or_pause(during, remaining):
    windows, target, _grab = target_fixture()
    backend, driver = guarded_backend(target)
    presses, clicks = [], []
    driver.mouseDown.side_effect = lambda: clicks.append(windows.current)
    driver.press.side_effect = lambda _key: presses.append(windows.current)
    pauses = []

    def pause(seconds):
        pauses.append(seconds)
        if ((during == "result_wait" and len(pauses) == 3)
                or (during == "final_wait" and len(pauses) == 5)):
            windows.current = 900

    backend.pause = pause

    class Ocr:
        def extract_text(self, _image):
            if during == "ocr":
                windows.current = 900
            return [remaining]

    def should_stop():
        target.check()
        return False

    with pytest.raises(RuntimeError, match="target_focus_lost"):
        execute_rewind_request(RewindExecutionRequest(), backend=backend,
            capture=target.capture, ocr_engine=Ocr(), should_stop=should_stop)
    assert 900 not in clicks and 900 not in presses
    assert len(clicks) == (1 if during == "ocr" else 3)
    assert len(presses) == (2 if during == "final_wait" else 0)
    backend.force_mouse_release()
    backend.close()
    backend._gamepad.close.assert_called_once()


def test_focus_is_rechecked_after_random_delay_and_before_any_move_or_down(monkeypatch):
    windows, target, _grab = target_fixture()
    backend, driver = guarded_backend(target)
    monkeypatch.setattr(input_backends, "random_input_delay", lambda _ctx: 0.1)
    backend._sleeper = lambda _seconds: setattr(windows, "current", 900)
    with pytest.raises(RuntimeError, match="target_focus_lost"):
        backend.click((500, 500))
    driver.moveTo.assert_not_called()
    driver.mouseDown.assert_not_called()
    backend.force_mouse_release()  # release remains allowed after invalidation
    assert driver.mouseUp.call_count >= 1


def test_sendinput_checks_focus_between_move_and_down_but_still_releases():
    windows, target, _grab = target_fixture()
    sent = []
    driver = _WindowsSendInputMouseDriver(
        user32=SimpleNamespace(SendInput=lambda *_args: (sent.append(windows.current), 1)[1]),
        sleeper=lambda _seconds: setattr(windows, "current", 900),
        operation_guard=lambda _capability: target.check(), verify_delivery=True)
    driver._abs_coord = lambda position: position
    with pytest.raises(RuntimeError, match="target_focus_lost"):
        driver.click((500, 500))
    # 首次移动在游戏；失焦后仅释放，不产生新的按下事件。
    assert sent == [100, 900]


@pytest.mark.parametrize("flags", [MOUSEEVENTF_MOVE, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP])
def test_sendinput_rejection_is_terminal_without_another_delivery_route(flags):
    api = Mock()
    api.SendInput.return_value = 0
    driver = _WindowsSendInputMouseDriver(user32=api, sleeper=lambda _seconds: None,
        operation_guard=lambda _capability: None, verify_delivery=True)
    with pytest.raises(RuntimeError, match="input_delivery_rejected"):
        driver._send(flags)
    assert api.SendInput.call_count == 1


def test_valid_simulated_execution_preserves_draws_and_order():
    windows, target, _grab = target_fixture()
    backend, driver = guarded_backend(target)
    backend.pause = Mock()
    report = execute_rewind_request(RewindExecutionRequest(), backend=backend,
        capture=target.capture, ocr_engine=SimpleNamespace(extract_text=lambda _image: ["1250"]),
        should_stop=lambda: (target.check(), False)[1])
    assert report.planned_draws == 20 and report.remaining_currency == 50
    assert driver.mouseDown.call_count == 4 and driver.press.call_count == 4
    assert windows.activation_attempts == []
    backend.force_mouse_release()
    backend.close()


def test_win32_adapter_queries_exact_handle_width_identity_and_closes_query_handle(monkeypatch):
    # WinDLL 也替换为假函数，测试不枚举本机窗口、不请求真实前台。
    import ctypes
    from ctypes import wintypes
    from src.integrations.vision import game_input_target

    class Function:
        def __init__(self, call=lambda *_args: 1):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    class Library:
        def __getattr__(self, name):
            function = Function()
            setattr(self, name, function)
            return function

    user, kernel = Library(), Library()
    monkeypatch.setattr(game_input_target.ctypes, "WinDLL", lambda name, **_kw: user if name == "user32" else kernel)
    adapter = Win32WindowSystem()
    kernel.OpenProcess.call = lambda *_args: 0x123456789
    kernel.CloseHandle.call = Mock(return_value=1)

    def process_id(_hwnd, pointer):
        pointer._obj.value = 20
        return 1

    def image_name(_handle, _flags, buffer, _length):
        buffer.value = EXECUTABLE
        return 1

    def times(_handle, created, *_others):
        created._obj.dwLowDateTime = 12345
        return 1

    def client_rect(_hwnd, pointer):
        pointer._obj.right, pointer._obj.bottom = 2560, 1440
        return 1

    def origin(_hwnd, pointer):
        pointer._obj.x, pointer._obj.y = 100, 40
        return 1

    user.GetWindowThreadProcessId.call = process_id
    user.GetClientRect.call, user.ClientToScreen.call = client_rect, origin
    user.IsIconic.call = lambda _hwnd: False
    kernel.QueryFullProcessImageNameW.call, kernel.GetProcessTimes.call = image_name, times
    assert adapter.inspect(100) == GameWindowState(100, 20, 12345, EXECUTABLE, RECT)
    kernel.CloseHandle.call.assert_called_once_with(0x123456789)
    assert kernel.OpenProcess.restype is wintypes.HANDLE
    assert user.GetForegroundWindow.restype is wintypes.HWND
    assert kernel.QueryFullProcessImageNameW.argtypes[-1] == ctypes.POINTER(wintypes.DWORD)
    kernel.CloseHandle.call.reset_mock()
    kernel.QueryFullProcessImageNameW.call = lambda *_args: 0
    assert adapter.inspect(100) is None
    kernel.CloseHandle.call.assert_called_once_with(0x123456789)


def test_execution_errors_have_used_locale_keys_and_old_patch_fallback():
    root = Path(__file__).resolve().parents[2]
    base = make_catalog(json.loads((root / "assets/locales/zh-CN.json").read_text(encoding="utf-8")))
    patch_payload = json.loads((root / "docs/examples/locales/qps-ploc.json").read_text(encoding="utf-8"))
    extended = make_catalog(patch_payload, base=base)
    keys = [key for key in base.messages if key.startswith("rewind.execution.error.")]
    assert len(keys) == 9
    assert all(parameter_names(base.render(key)) == parameter_names(extended.render(key)) for key in keys)
    patch_payload["messages"] = {}
    fallback = make_catalog(patch_payload, base=base)
    assert all(fallback.render(key) == base.render(key) for key in keys)


def test_rejected_release_still_attempts_the_second_release_route():
    _windows, target, _grab = target_fixture()
    backend, gui = guarded_backend(target)
    send = Mock(available=True)
    send.release_left.side_effect = RuntimeError("input_delivery_rejected")
    backend._send_input = send
    with pytest.raises(RuntimeError, match="input_delivery_rejected"):
        backend.force_mouse_release()
    gui.mouseUp.assert_called_once_with(button="left")
    backend.close()


def test_system_rejection_stops_before_ocr_and_does_not_retry_on_calculator():
    from src.features.drive_assembly.rewind.execution import close_rewind_input_backend
    windows, target, _grab = target_fixture()
    backend, gui = guarded_backend(target)
    flags = []

    def reject(_count, pointer, _size):
        flags.append(int(pointer._obj.mi.dwFlags))
        windows.current = 900
        return 0

    driver = _WindowsSendInputMouseDriver(user32=SimpleNamespace(SendInput=reject),
        sleeper=lambda _seconds: None, operation_guard=backend.operation_guard, verify_delivery=True)
    driver._abs_coord = lambda position: position
    backend._send_input = driver
    ocr = Mock()
    with pytest.raises(RuntimeError, match="input_delivery_rejected") as caught:
        execute_rewind_request(RewindExecutionRequest(), backend=backend,
            capture=target.capture, ocr_engine=ocr,
            should_stop=lambda: (target.check(), False)[1])
    assert len(flags) == 1 and not flags[0] & MOUSEEVENTF_LEFTDOWN
    ocr.extract_text.assert_not_called()
    close_rewind_input_backend(backend, caught.value)
    assert flags[-1] == MOUSEEVENTF_LEFTUP and len(flags) == 2
    gui.moveTo.assert_not_called()
    gui.mouseDown.assert_not_called()
    gui.press.assert_not_called()
    gui.mouseUp.assert_called_once_with(button="left")
    backend._gamepad.close.assert_called_once()


def test_composition_passes_confirmed_game_path_to_target_factory(monkeypatch):
    from src.ui.composition import toolbox_composition
    factory = Mock(return_value=object())
    monkeypatch.setattr(toolbox_composition, "create_game_input_target", factory)
    context = SimpleNamespace()
    dependencies = toolbox_composition.build_toolbox_dependencies(context,
        operation_entry=lambda *_args: True, operation_unavailable=lambda *_args: None,
        operation_guard=lambda *_args: None, operation_generation=lambda: 0,
        material_importer=lambda: None, navigate_static_catalog=lambda: None,
        game_executable=lambda: EXECUTABLE)
    assert dependencies.rewind_target_factory() is factory.return_value
    factory.assert_called_once_with(EXECUTABLE)
