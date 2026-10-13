# 用真实离屏按钮、Qt owner、暂存文件及 SQLite 验证扫描续跑与仅文件重试。
from dataclasses import replace
from functools import partial
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import os
import time

import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget, QMessageBox, QPushButton

from src.app.workers import FullVisualScanParseWorkerThread
from src.features.allocation.execute_page import _build_run_button
from src.features.scanning import file_workflow, workflow
from src.features.scanning.controller import ScanningController
from src.features.scanning.dependencies import ScanningDependencies, scanning_dependencies_are_current
from src.features.scanning.file_lifecycle import ScanFileLifecycle
from src.features.scanning.manual_recovery import ManualRecoverySession
from src.features.scanning.vision_commit_controller import VisionCommitController
from src.integrations.vision import capture_files as files
from src.integrations.vision.recognition import batch_processor
from src.integrations.vision.recognition.duplicate_filter import RecoverableParseError
from src.models.equipment import Tape
from src.services.scanning.pipeline import ScanParseCoordinator
from tests.vision.test_recognition_resources import (
    baseline_user, freeze_image, mutate_stats, read_inventory, selected as selected,
)

NTE_TEST_TIER = "core"
FILLED = (("攻击力%", 3.0),)


def spin(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("scan fixture did not reach terminal")
        time.sleep(.002)
    app.processEvents()


class Owner(QWidget):
    _start_vision_processing = workflow._start_vision_processing
    _on_vision_progress = workflow._on_vision_progress
    _on_vision_done = workflow._on_vision_done
    _on_vision_cancel = workflow._on_vision_cancel
    _on_vision_canceled = workflow._on_vision_canceled
    _on_vision_error = workflow._on_vision_error
    _on_vision_commit_failed = workflow._on_vision_commit_failed
    _on_vision_application_failed = workflow._on_vision_application_failed
    _on_vision_committed = workflow._on_vision_committed
    _on_gamepad_scan_done = workflow._on_gamepad_scan_done
    _on_gamepad_parse_progress = workflow._on_gamepad_parse_progress
    _on_gamepad_parse_done = workflow._on_gamepad_parse_done
    _on_gamepad_post_actions_ready = workflow._on_gamepad_post_actions_ready
    _on_gamepad_pipeline_done = workflow._on_gamepad_pipeline_done
    _on_gamepad_error = workflow._on_gamepad_error
    request_stop = ScanningController.request_stop
    begin_close = ScanningController.begin_close

    def is_running(self):
        return self._vision_commit.is_running() or any(worker is not None and worker.isRunning()
            for worker in (self._scan_worker, self._gamepad_worker, self._vision_worker))

    def resume_capture_stage(self):
        # 按公开 Controller 动作接真实按钮；基线也用同一测试，不伪造缺失能力。
        return ScanningController.resume_capture_stage(self)

    def retry_capture_publication(self):
        return ScanningController.retry_capture_publication(self)


@pytest.fixture
def scene(tmp_path, selected, monkeypatch):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / "shots"
    root.mkdir()
    (root / "raw_drive_0001.png").write_bytes(b"old-root-A")
    directory = Path(files.create_capture_directory(root))
    image = directory / "raw_drive_0001.png"
    image.write_bytes(b"new-stage-B")
    stage = files.freeze_capture_stage(root, directory, 1)
    resources = freeze_image(selected)
    database = tmp_path / "user.sqlite3"
    previous = baseline_user(database)
    deps = ScanningDependencies("scan-fixture", 1, root, selected[0], database,
        selected[1], tmp_path, selected[0], selected[0] / "templates")
    owner = Owner()
    owner.dialog_parent = owner
    owner.app_context = SimpleNamespace(generation=1, account=SimpleNamespace(
        active_account_id=deps.account_id, user_database_path=database))
    owner._scan_dependencies = deps
    owner._manual_recovery = ManualRecoverySession()
    owner._capture_recovery = None
    owner._vision_worker = owner._scan_worker = owner._gamepad_worker = None
    owner._progress_dlg = None
    owner._pending_parse_only = True
    owner._pending_archive_paths = []
    owner._scan_operation_active = True
    owner._allocation_controller = Mock()
    owner._stop_scan_hotkeys = Mock()
    owner._start_scan_hotkeys = Mock()
    owner._hotkey_manager = SimpleNamespace(configuration=SimpleNamespace(stop="fixture-stop"))
    owner._update_inventory_status = Mock()
    owner._refresh_home = Mock()
    owner._start_allocation_worker = Mock()
    owner._do_exec = Mock()
    owner.showNormal = Mock()
    owner.showMinimized = Mock()
    owner.activateWindow = Mock()
    owner._scan_lifecycle = lambda: ScanFileLifecycle(root)
    owner._postprocess_vision_files = partial(file_workflow.postprocess_vision_files, owner)
    owner._vision_commit = VisionCommitController(partial(scanning_dependencies_are_current, owner), owner)
    owner._vision_commit.committed.connect(owner._on_vision_committed)
    owner._vision_commit.failed.connect(owner._on_vision_commit_failed)
    owner._vision_commit.application_failed.connect(owner._on_vision_application_failed)
    if hasattr(owner._vision_commit, "publication_retried"):
        owner._vision_commit.publication_retried.connect(partial(file_workflow.publication_retry_done, owner))
        owner._vision_commit.publication_retry_failed.connect(partial(file_workflow.publication_retry_failed, owner))
    layout = QVBoxLayout(owner)
    _build_run_button(owner, layout)
    owner.show()
    monkeypatch.setattr(workflow, "_scan_event", Mock())
    monkeypatch.setattr(workflow, "_begin_scan_operation", Mock())
    completed = Mock()
    monkeypatch.setattr(workflow, "show_scan_completion", completed)
    messages = {key: Mock(return_value=QMessageBox.Ok) for key in ("information", "warning", "critical", "question")}
    for key, call in messages.items():
        monkeypatch.setattr(QMessageBox, key, call)
    tape = Tape(uid="fixture-tape", quality="Gold", area=15, set_name="夹具套装", main_stats="攻击力%",
        sub_stats={"生命值%": 2.5, "防御力%": 2.5, "暴击伤害%": 5.6})
    stats = {"capture_stage": stage, "capture_cancel_token": Event(), "recognition_resources": resources,
        "capture_driver": "mouse",
        "parse_scope": "full", "total_count": 1, "vision_items": [tape.model_dump()],
        "added_paths": [str(image)], "failed_count": 0, "success_count": 1}
    result = SimpleNamespace(app=app, owner=owner, root=root, image=image, stage=stage,
        resources=resources, database=database, previous=previous, deps=deps, stats=stats,
        tape=tape, completed=completed, messages=messages, selected=selected)
    try:
        yield result
    finally:
        owner.request_stop()
        for worker in (owner._vision_worker, owner._gamepad_worker):
            if worker is not None:
                worker.wait(5000)
        spin(app, lambda: not owner._vision_commit.is_running())
        owner._vision_commit.close()
        owner.deleteLater()
        app.processEvents()


def pending_stats(scene):
    scanner = Mock()
    def capture(total, *, on_capture, commit_on_complete):
        assert not commit_on_complete
        on_capture(str(scene.image), 1, total)
        return total
    scanner.start_scan = capture
    processor = Mock(inventory=[])
    processor.process_image_file.side_effect = RecoverableParseError("fixture pending", scene.tape, 3)
    result = ScanParseCoordinator(scanner, processor, 1).run().to_stats()
    return dict(scene.stats, **result, vision_items=[])


def interact(scene, callback):
    errors = []
    timer = QTimer(scene.owner)
    deadline = time.monotonic() + 5
    def act():
        dialog = scene.app.activeModalWidget()
        if dialog is None and time.monotonic() < deadline:
            return
        timer.stop()
        try:
            assert dialog is not None
            callback(dialog)
        except Exception as error:
            errors.append(str(error))
            if dialog is not None:
                dialog.reject()
    timer.timeout.connect(act)
    timer.start(2)
    return errors


def close_pending(scene):
    errors = interact(scene, lambda dialog: (dialog.restore_filled_values(FILLED), dialog.reject()))
    scene.owner._on_vision_done(pending_stats(scene))
    assert not errors, errors
    assert scene.owner._manual_recovery.has_draft
    assert read_inventory(scene.database) == (scene.previous, [], 1)
    assert scene.image.exists()


def stub_ocr(scene, monkeypatch, *, pending=True):
    seen = []
    class OCR:
        def __init__(self, *args, **kwargs):
            self.runtime_resources = scene.resources.image_profile.ocr_resources
    monkeypatch.setattr(batch_processor, "OCREngine", OCR)
    def parse(processor, path, filename, **kwargs):
        seen.append((str(path), filename))
        assert Path(path).parent == scene.stage.directory
        assert Path(path).read_bytes() == b"new-stage-B"
        if pending:
            raise RecoverableParseError("fixture pending", scene.tape, 3)
        processor.inventory.append(scene.tape)
        return scene.tape, True
    monkeypatch.setattr(batch_processor.BatchProcessor, "process_image_file", parse)
    return seen


def test_gui_continue_exact_stage_restores_draft_and_commits_once(scene, monkeypatch):
    close_pending(scene)
    button = getattr(scene.owner, "btn_resume_capture", None)
    assert button is not None and not button.isHidden() and button.isEnabled()
    seen = stub_ocr(scene, monkeypatch)
    drafts = []
    def accept(dialog):
        drafts.append(dialog.filled_values())
        dialog.accept()
    errors = interact(scene, accept)
    button.click()
    spin(scene.app, lambda: scene.owner._vision_commit.last_result is not None)
    spin(scene.app, lambda: not scene.owner.is_running())
    assert not errors, errors
    assert drafts == [FILLED]
    assert seen == [(str(scene.image), scene.image.name)]
    current, rows, count = read_inventory(scene.database)
    assert current != scene.previous and len(rows) == 1 and count == 2
    assert (scene.root / scene.image.name).read_bytes() == b"new-stage-B"
    assert not scene.stage.directory.exists()
    assert scene.owner.btn_resume_capture.isHidden()
    assert scene.completed.call_count == 1
    scene.owner._start_allocation_worker.assert_not_called()


@pytest.mark.parametrize("change", ["missing", "same_stat_bytes", "resources", "generation", "cancel"])
def test_stale_stage_or_resources_never_commit_old_inventory(scene, monkeypatch, change):
    close_pending(scene)
    button = getattr(scene.owner, "btn_resume_capture", None)
    assert button is not None
    before = scene.image.stat()
    if change == "missing":
        scene.image.unlink()
    elif change == "same_stat_bytes":
        scene.image.write_bytes(b"new-stage-C")
        os.utime(scene.image, ns=(before.st_atime_ns, before.st_mtime_ns))
    elif change == "resources":
        mutate_stats(scene.selected[0])
    elif change == "generation":
        scene.owner.app_context.generation += 1
    else:
        scene.stats["capture_cancel_token"].set()
    seen = stub_ocr(scene, monkeypatch)
    button.click()
    spin(scene.app, lambda: not scene.owner.is_running())
    assert not seen
    assert read_inventory(scene.database) == (scene.previous, [], 1)
    assert (scene.root / "raw_drive_0001.png").read_bytes() == b"old-root-A"
    assert scene.owner._vision_commit.last_result is None
    assert scene.app.activeModalWidget() is None


class FullFixture(QObject):
    scan_done = Signal(int, int)
    progress = Signal(int, int, str)
    parse_done = Signal()
    post_actions_ready = Signal()
    processing_done = Signal(dict)
    error = Signal(str)
    finished = Signal()
    def __init__(self, **kwargs):
        super().__init__(kwargs["parent"])
        self.cancel_token = Event()
        self.scanner = None
    def start(self):
        pass
    def isRunning(self):
        return False
    def wait(self, timeout):
        return True
    def request_cancel(self):
        self.cancel_token.set()
    def is_cancelled(self):
        return self.cancel_token.is_set()


@pytest.mark.parametrize("action", ["stop", "close", "generation", "replacement"])
def test_full_capture_late_signal_does_not_open_modal_or_create_commit(scene, monkeypatch, action):
    monkeypatch.setattr(workflow, "FullVisualScanParseWorkerThread", FullFixture)
    monkeypatch.setattr(workflow, "request_input_entry", lambda *args: True)
    monkeypatch.setattr(workflow, "_current_scanning_dependencies", lambda owner: scene.deps)
    workflow._start_gamepad_scan(scene.owner, 1)
    worker = scene.owner._gamepad_worker
    stats = dict(scene.stats, capture_cancel_token=worker.cancel_token)
    if action == "stop":
        scene.owner.request_stop()
    elif action == "close":
        scene.owner.begin_close()
    elif action == "generation":
        scene.owner.app_context.generation += 1
    else:
        scene.owner._gamepad_worker = None
    for signal, args in ((worker.scan_done, (1, 1)), (worker.progress, (1, 1, "fixture")),
            (worker.parse_done, ()), (worker.post_actions_ready, ()),
            (worker.processing_done, (stats,)), (worker.error, ("fixture error",))):
        signal.emit(*args)
    scene.app.processEvents()
    assert scene.owner._vision_commit.last_result is None
    assert not scene.owner._vision_commit.is_running()
    assert read_inventory(scene.database) == (scene.previous, [], 1)
    assert scene.app.activeModalWidget() is None
    assert scene.owner._progress_dlg is None
    assert scene.completed.call_count == 0
    scene.owner.showNormal.assert_not_called()


def test_actual_full_worker_stop_revokes_completed_capture_event(scene):
    worker = FullVisualScanParseWorkerThread(1, screenshot_dir=scene.root,
        config_dir=scene.deps.config_dir, user_database_path=scene.database)
    worker.request_cancel()  # 仅构造和取消，绝不执行设备/采集 run。
    assert worker.is_cancelled() and worker.cancel_token.is_set()


def inject_pending(scene, monkeypatch):
    original = files.os.replace
    moves = [0]
    def fail_once(source, target):
        if Path(source).suffix == ".png":
            moves[0] += 1
            if moves[0] == 2:
                raise OSError("fixture publication failure")
        original(source, target)
    monkeypatch.setattr(files.os, "replace", fail_once)
    scene.owner._on_vision_done(scene.stats)
    spin(scene.app, lambda: scene.owner._vision_commit.last_result is not None)
    spin(scene.app, lambda: not scene.owner.is_running())
    saved = scene.owner._vision_commit.last_result
    assert saved.publication.status == "pending"
    monkeypatch.setattr(files.os, "replace", original)
    return saved


def test_gui_retry_is_file_only_and_duplicate_click_does_not_resave(scene, monkeypatch):
    saved = inject_pending(scene, monkeypatch)
    before = read_inventory(scene.database)
    assert before[0] == saved.snapshot_id and before[2] == 2
    button = getattr(scene.owner, "btn_retry_publication", None)
    assert button is not None and not button.isHidden() and button.isEnabled()
    retry_calls = []
    original = scene.owner._vision_commit._retry
    def retry(request, result, token):
        retry_calls.append((request, result))
        return original(request, result, token)
    scene.owner._vision_commit._retry = retry
    button.click()
    button.click()
    assert not scene.owner._vision_commit.retry_publication()
    spin(scene.app, lambda: not scene.owner.is_running())
    assert len(retry_calls) == 1 and retry_calls[0][1] is saved
    assert read_inventory(scene.database) == before
    assert scene.owner._vision_commit.last_result.publication.status == "published"
    assert (scene.root / "raw_drive_0001.png").read_bytes() == b"new-stage-B"
    assert scene.completed.call_count == 1
    scene.owner._start_allocation_worker.assert_not_called()
    assert scene.owner.btn_retry_publication.isHidden()
    assert scene.owner.btn_resume_capture.isHidden()


@pytest.mark.parametrize("change", ["cancel", "generation", "resources", "baseline", "new_snapshot", "restore_incomplete", "cleanup_pending"])
def test_pending_retry_rejects_invalid_owner_or_changed_inputs(scene, monkeypatch, change):
    saved = inject_pending(scene, monkeypatch)
    before = read_inventory(scene.database)
    button = getattr(scene.owner, "btn_retry_publication", None)
    assert button is not None
    if change == "cancel":
        scene.owner.request_stop()
    elif change == "generation":
        scene.owner.app_context.generation += 1
    elif change == "resources":
        mutate_stats(scene.selected[0])
    elif change == "baseline":
        (scene.root / "raw_drive_0001.png").write_bytes(b"updated-owned-baseline")
    elif change == "new_snapshot":
        baseline_user(scene.database)
        before = read_inventory(scene.database)
    else:
        status = "restore_incomplete" if change == "restore_incomplete" else "published"
        scene.owner._vision_commit.last_result = replace(saved, publication=replace(saved.publication,
            status=status, cleanup_pending=change == "cleanup_pending"))
    button.click()
    spin(scene.app, lambda: not scene.owner.is_running())
    assert read_inventory(scene.database) == before
    assert scene.image.read_bytes() == b"new-stage-B"
    assert scene.owner._vision_commit.last_result.publication.status != "published" or change == "cleanup_pending"
    assert scene.completed.call_count == 1


def test_partial_full_input_has_no_resume_and_no_inventory_commit(scene):
    stats = dict(scene.stats, total_count=2)
    scene.owner._on_vision_done(stats)
    spin(scene.app, lambda: not scene.owner.is_running())
    assert read_inventory(scene.database) == (scene.previous, [], 1)
    assert scene.owner.btn_resume_capture.isHidden()
    assert scene.owner._capture_recovery is None
    assert scene.image.read_bytes() == b"new-stage-B"


def test_stage_without_byte_identity_is_not_authoritative(scene):
    stage = replace(scene.stage, image_sha256=())
    with pytest.raises(files.CaptureFileError):
        files.prepare_capture_publication(stage)
    assert read_inventory(scene.database) == (scene.previous, [], 1)


def test_streaming_pending_record_uses_same_scan_number_as_stage_reparse(scene):
    stats = pending_stats(scene)
    assert stats["pending_manual_items"][0]["item"]["_scan_number"] == 1


@pytest.mark.parametrize("action", ["cancel", "close", "generation"])
def test_inflight_file_retry_is_bounded_and_never_replays_after_revocation(scene, monkeypatch, action):
    saved = inject_pending(scene, monkeypatch)
    before = read_inventory(scene.database)
    entered, release = Event(), Event()
    calls = []
    original = scene.owner._vision_commit._retry
    def retry(request, result, token):
        calls.append((request, result))
        entered.set()
        assert release.wait(5)
        return original(request, result, token)
    scene.owner._vision_commit._retry = retry
    shown_before = {name: value.call_count for name, value in scene.messages.items()}
    scene.owner.btn_retry_publication.click()
    try:
        spin(scene.app, entered.is_set)
        if action == "cancel":
            scene.owner.request_stop()
        elif action == "close":
            scene.owner.begin_close()
        else:
            scene.owner.app_context.generation += 1
            scene.owner.request_stop()
        assert scene.owner.is_running()
        assert not scene.owner._vision_commit.retry_publication()
        release.set()
        spin(scene.app, lambda: not scene.owner.is_running())
        assert len(calls) == 1 and calls[0][1] is saved
        assert read_inventory(scene.database) == before
        assert scene.image.read_bytes() == b"new-stage-B"
        assert {name: value.call_count for name, value in scene.messages.items()} == shown_before
        assert not scene.owner._vision_commit.retry_publication()
    finally:
        release.set()


@pytest.mark.parametrize("action", ["stop", "reset", "close", "user_cancel"])
def test_stage_reparse_terminal_signals_obey_owner_revocation(scene, monkeypatch, action):
    close_pending(scene)
    entered, release = Event(), Event()
    stub_ocr(scene, monkeypatch)
    original = batch_processor.BatchProcessor.process_image_file
    def blocked(processor, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(processor, *args, **kwargs)
    monkeypatch.setattr(batch_processor.BatchProcessor, "process_image_file", blocked)
    scene.owner._preferences_provider = lambda: {}
    scene.owner._reload_full_scan_preference_widgets = Mock()
    scene.owner._reload_allocation_filter_settings = Mock()
    messages_before = {name: value.call_count for name, value in scene.messages.items()}
    revision = scene.owner._manual_recovery.revision
    scene.owner.btn_resume_capture.click()
    worker = scene.owner._vision_worker
    try:
        spin(scene.app, entered.is_set)
        if action == "stop":
            scene.owner.request_stop()
        elif action == "reset":
            ScanningController.reset_account_state(scene.owner)
        elif action == "close":
            scene.owner.begin_close()
        else:
            cancel = scene.owner._progress_dlg.findChild(QPushButton)
            assert cancel is not None
            cancel.click()  # 真实进度窗口的用户取消入口。
        if action != "user_cancel":
            # 同账号下模拟已经排队的三种终态，再等待真实 run 发出 canceled。
            worker.canceled.emit(0)
            worker.error.emit("fixture late parse error")
            worker.processing_done.emit(scene.stats)
            scene.app.processEvents()
        release.set()
        spin(scene.app, lambda: not scene.owner.is_running())
        assert read_inventory(scene.database) == (scene.previous, [], 1)
        assert scene.owner._vision_commit.last_result is None
        assert scene.image.read_bytes() == b"new-stage-B"
        if action == "user_cancel":
            assert scene.owner._manual_recovery.revision == revision
            assert scene.owner.btn_run.isEnabled()
            assert scene.messages["information"].call_count == messages_before["information"] + 1
            assert scene.messages["critical"].call_count == messages_before["critical"]
        else:
            assert scene.owner._manual_recovery.revision != revision
            assert {name: value.call_count for name, value in scene.messages.items()} == messages_before
            if action != "close":
                assert scene.owner.btn_run.isEnabled()
    finally:
        release.set()
        worker.wait(5000)


@pytest.mark.parametrize("driver", ["mouse", "gamepad"])
def test_stage_resume_preserves_original_driver_through_saved_request(scene, monkeypatch, driver):
    scene.stats["capture_driver"] = driver
    close_pending(scene)
    stub_ocr(scene, monkeypatch)
    original = scene.owner._vision_commit.submit
    submitted = Mock(wraps=original)
    scene.owner._vision_commit.submit = submitted
    errors = interact(scene, lambda dialog: dialog.accept())
    scene.owner.btn_resume_capture.click()
    spin(scene.app, lambda: scene.owner._vision_commit.last_result is not None)
    spin(scene.app, lambda: not scene.owner.is_running())
    assert not errors, errors
    assert submitted.call_count == 1
    request = submitted.call_args.args[1]
    assert request.stats["capture_driver"] == driver
    assert scene.owner._vision_commit.last_result.stats["capture_driver"] == driver
    assert read_inventory(scene.database)[2] == 2
    scene.owner._start_scan_hotkeys.assert_not_called()


@pytest.mark.parametrize("driver", [None, "", "unverified-fixture"])
def test_unknown_stage_driver_is_not_filled_with_mouse(scene, driver):
    from src.features.scanning.vision_worker import VisionWorkerThread
    stats = dict(scene.stats, capture_driver=driver)
    file_workflow.remember_capture_recovery(scene.owner, stats, scene.deps)
    assert scene.owner._capture_recovery is None
    assert scene.owner.btn_resume_capture.isHidden()
    with pytest.raises(ValueError):
        VisionWorkerThread(scene.root, scene.owner, replace_output=True, parse_scope="full",
            config_dir=scene.deps.config_dir, capture_stage=scene.stage,
            recognition_resources=scene.resources, capture_driver=driver)
    assert read_inventory(scene.database) == (scene.previous, [], 1)
