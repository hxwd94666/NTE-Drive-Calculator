# 在隔离输入后端中验证角色级进度与中止报告，不连接游戏或创建输入设备。
from contextlib import contextmanager
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest

from src.features.drive_assembly import executor
from src.features.drive_assembly.input_backends import AssemblyExecutionFailed, AssemblyExecutionStopped

NTE_TEST_TIER = "core"


@pytest.fixture
def isolated_ocr(monkeypatch):
    @contextmanager
    def session(**_kwargs):
        def check_current(*, should_stop=None):
            if should_stop and should_stop():
                raise CancelledError()

        yield SimpleNamespace(check_current=check_current)

    monkeypatch.setattr(executor, "use_ocr_session", session)


def plans():
    traversal = {
        "plans": [
            {
                "role_name": name,
                "action_sequence": [{"name": "assemble_current_role_from_blueprint", "role_name": name}],
            }
            for name in ("A", "B")
        ]
    }
    assembly = {
        "role_plans": [
            {
                "role_name": name,
                "available": True,
                "actions": [{"name": "install_tape", "sequence": [{"name": "install", "position": (index, index)}]}],
            }
            for index, name in enumerate(("A", "B"), 1)
        ]
    }
    return traversal, assembly


def test_failure_keeps_previously_verified_role_and_current_unknown(isolated_ocr):
    calls = []

    def click(position):
        calls.append(position)
        if position == (2, 2):
            raise RuntimeError("input interrupted")

    events = []
    with pytest.raises(AssemblyExecutionFailed) as caught:
        executor.execute_role_traversal_assembly_plan(
            *plans(),
            backend=SimpleNamespace(click=click),
            pause_seconds=0,
            role_verifier=lambda *_args: {"ok": True},
            on_role_progress=events.append,
        )
    report = caught.value.report
    assert report.verified_roles == ["A"] and report.current_role == "B"
    assert [row.role_name for row in report.role_reports] == ["A"]
    assert any(event["state"] == "confirmed" for event in events) and events[-1]["state"] == "operating"
    assert calls == [(1, 1), (2, 2)]


def test_stop_between_roles_preserves_completed_report_without_next_input(isolated_ocr):
    stop = False
    calls = []

    def progress(event):
        nonlocal stop
        if event["role_name"] == "B":
            stop = True

    with pytest.raises(AssemblyExecutionStopped) as caught:
        executor.execute_role_traversal_assembly_plan(
            *plans(),
            backend=SimpleNamespace(click=calls.append),
            pause_seconds=0,
            should_stop=lambda: stop,
            role_verifier=lambda *_args: {"ok": True},
            on_role_progress=progress,
        )
    assert caught.value.report.stopped
    assert caught.value.report.verified_roles == ["A"]
    assert calls == [(1, 1)]


def test_missing_verifier_is_not_claimed_as_confirmation(isolated_ocr):
    events = []
    report = executor.execute_role_traversal_assembly_plan(
        *plans(), backend=SimpleNamespace(click=lambda _p: None), pause_seconds=0, on_role_progress=events.append
    )
    assert report.verified_roles == []
    assert [event["state"] for event in events] == ["operating", "unknown", "operating", "unknown"]


def test_stop_during_post_action_wait_keeps_known_action_count(isolated_ocr):
    stop = False

    def pause(_seconds):
        nonlocal stop
        stop = True

    backend = SimpleNamespace(click=lambda _position: None, pause=pause)
    with pytest.raises(AssemblyExecutionStopped) as caught:
        executor.execute_role_traversal_assembly_plan(
            *plans(), backend=backend, pause_seconds=0.1, should_stop=lambda: stop
        )
    assert caught.value.report.executed_actions == 1
    assert caught.value.report.role_reports == []
    assert caught.value.report.current_role == "A"
