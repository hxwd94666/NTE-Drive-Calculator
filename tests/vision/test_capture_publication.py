# 用真实目录、SQLite 和流水线验证截图发布先后、精确恢复与仅文件重试。
from concurrent.futures import CancelledError
import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from src.integrations.vision import capture_files as files
from src.integrations.vision.mouse_inventory_scan import MouseInventoryScanner
from src.models.equipment import Tape
from src.scanner.gamepad_controller import GamepadScanner
from src.services.inventory.vision_commit import (
    VisionCommitRequest, commit_vision_result, retry_vision_publication,
)
from src.services.recognition.resources import RecognitionResourceError
from src.services.scanning.pipeline import run_streaming_scan_parse
from tests.vision.test_recognition_resources import (
    baseline_user, freeze_image, mutate_stats, read_inventory, selected as selected,
)

NTE_TEST_TIER = "core"


def prepare(tmp_path, selected):
    root = tmp_path / "shots"
    root.mkdir()
    (root/"raw_drive_0001.png").write_bytes(b"old owned screenshot")
    (root/"reference.png").write_bytes(b"unknown user image")
    (root/"temp").mkdir()
    (root/"temp"/"raw_drive_0001.png").write_bytes(b"previous failed capture")
    directory = Path(files.create_capture_directory(root))
    (directory/"raw_drive_0001.png").write_bytes(b"new synthetic screenshot")
    stage = files.freeze_capture_stage(root, directory, 1)
    resources = freeze_image(selected)
    database = tmp_path / "user.sqlite3"
    previous = baseline_user(database)
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="夹具套装",
                main_stats="攻击力%", sub_stats={"暴击率%": 2.8})
    stats = {"vision_items": [item.model_dump()], "parse_scope": "full", "total_count": 1,
             "failed_count": 0, "added_paths": [str(directory/"raw_drive_0001.png")],
             "recognition_resources": resources, "capture_stage": stage}
    request = VisionCommitRequest.freeze(database, stats, [])
    return root, stage, request, previous


def assert_unmanaged(root):
    assert (root/"reference.png").read_bytes() == b"unknown user image"
    assert (root/"temp"/"raw_drive_0001.png").read_bytes() == b"previous failed capture"


@pytest.mark.parametrize("scanner_type", [MouseInventoryScanner, GamepadScanner])
def test_both_scanners_use_unique_owned_stage_without_deleting_previous_failure(tmp_path, scanner_type):
    root = tmp_path/"shots"
    root.mkdir()
    (root/"temp").mkdir()
    previous = root/"temp"/"raw_drive_0001.png"
    previous.write_bytes(b"previous failure")
    scanner = scanner_type.__new__(scanner_type)
    scanner.output_dir = str(root)
    scanner._prepare_temp_output()
    first = Path(scanner.capture_dir)
    (first/"raw_drive_0001.png").write_bytes(b"first capture")
    scanner._prepare_temp_output()
    assert Path(scanner.capture_dir) != first
    assert previous.read_bytes() == b"previous failure"
    assert (first/"raw_drive_0001.png").read_bytes() == b"first capture"


def test_streaming_returns_real_stage_paths_and_does_not_publish_before_db(tmp_path, selected, monkeypatch):
    root, stage, request, previous = prepare(tmp_path, selected)
    scanner = Mock(output_dir=str(root), capture_dir=str(stage.directory))
    def capture(total, *, on_capture, commit_on_complete):
        assert not commit_on_complete
        on_capture(str(stage.directory/"raw_drive_0001.png"), 1, 1)
        return 1
    scanner.start_scan = capture
    processor = Mock(inventory=[])
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="夹具套装", main_stats="攻击力%", sub_stats={})
    processor.process_image_file.return_value = (item, True)
    monkeypatch.setattr("src.services.scanning.pipeline.equipment_screenshot_state", lambda p: "normal")
    result = run_streaming_scan_parse(scanner, processor, total_drives=1,
                                     parse_during_scan=False, allow_post_actions=False)
    assert result["added_paths"] == [str(stage.directory/"raw_drive_0001.png")]
    scanner._commit_temp_output.assert_not_called()
    mutate_stats(selected[0])
    with pytest.raises(RecognitionResourceError):
        commit_vision_result(request, Event())
    assert read_inventory(request.database_path) == (previous, [], 1)
    assert (root/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
    assert (stage.directory/"raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    assert_unmanaged(root)


def test_success_publishes_only_after_database_and_cleans_only_owned_backup(tmp_path, selected, monkeypatch):
    root, stage, request, previous = prepare(tmp_path, selected)
    original = files.os.replace
    seen = []
    def replace(source, target):
        if Path(source).suffix == ".png":
            current, rows, count = read_inventory(request.database_path)
            assert current != previous and count == 2 and rows
            seen.append((str(source), str(target)))
        original(source, target)
    monkeypatch.setattr(files.os, "replace", replace)
    result = commit_vision_result(request, Event())
    assert result.publication.status == "published" and not result.publication.cleanup_pending
    assert len(seen) == 2
    assert (root/"raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    assert result.stats["added_paths"] == [str(root/"raw_drive_0001.png")]
    assert not stage.directory.exists() and not result.plan.backup.exists()
    assert_unmanaged(root)


@pytest.mark.parametrize("restore_failure", [False, True])
def test_second_image_move_failure_preserves_saved_db_and_reports_precise_file_state(tmp_path, selected, monkeypatch, restore_failure):
    root, stage, request, previous = prepare(tmp_path, selected)
    original = files.os.replace
    image_moves = [0]
    def replace(source, target):
        if Path(source).suffix == ".png":
            image_moves[0] += 1
            if image_moves[0] == 2 or restore_failure and image_moves[0] == 3:
                raise OSError("injected image move failure")
        original(source, target)
    monkeypatch.setattr(files.os, "replace", replace)
    result = commit_vision_result(request, Event())
    current, rows, count = read_inventory(request.database_path)
    assert current == result.snapshot_id != previous and count == 2 and rows
    assert result.publication.status == ("restore_incomplete" if restore_failure else "pending")
    assert (stage.directory/"raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    if restore_failure:
        assert not (root/"raw_drive_0001.png").exists()
        assert (result.plan.backup/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
        assert result.publication.remaining_moves
    else:
        assert (root/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
        monkeypatch.setattr(files.os, "replace", original)
        retried = retry_vision_publication(request, result, Event())
        assert retried.publication.status == "published"
        assert read_inventory(request.database_path)[2] == 2  # 文件重试没有新建快照。
    assert_unmanaged(root)


def test_cancel_after_sql_keeps_saved_fact_and_returns_pending_files(tmp_path, selected, monkeypatch):
    from src.services.inventory import vision_commit as service
    root, stage, request, previous = prepare(tmp_path, selected)
    token = Event()
    original = service.commit_completed_vision_inventory
    def save(*args, **kwargs):
        value = original(*args, **kwargs)
        token.set()
        return value
    monkeypatch.setattr(service, "commit_completed_vision_inventory", save)
    result = commit_vision_result(request, token)
    assert result.snapshot_id != previous and result.publication.status == "pending"
    assert read_inventory(request.database_path)[2] == 2
    assert (root/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
    assert (stage.directory/"raw_drive_0001.png").exists()


@pytest.mark.parametrize("fault", ["cancel", "capacity", "changed_stage", "unknown_conflict"])
def test_preflight_failure_never_writes_database_or_old_files(tmp_path, selected, monkeypatch, fault):
    root, stage, request, previous = prepare(tmp_path, selected)
    token = Event()
    if fault == "cancel":
        token.set()
    elif fault == "capacity":
        monkeypatch.setattr(files.shutil, "disk_usage", lambda p: Mock(free=0))
    elif fault == "changed_stage":
        (stage.directory/"raw_drive_0001.png").write_bytes(b"changed")
    else:
        (root/"raw_drive_0001.png").unlink()
        (root/"RAW_DRIVE_0001.PNG").write_bytes(b"unknown conflict")
    with pytest.raises(CancelledError if fault == "cancel" else files.CaptureFileError):
        commit_vision_result(request, token)
    assert read_inventory(request.database_path) == (previous, [], 1)
    if fault == "unknown_conflict":
        assert (root/"RAW_DRIVE_0001.PNG").read_bytes() == b"unknown conflict"
    else:
        assert (root/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
    assert_unmanaged(root)


def test_directory_lease_blocks_second_handle_until_real_release(tmp_path):
    with files.capture_output_lease(tmp_path):
        with pytest.raises(files.CaptureFileError, match="正在"):
            with files.capture_output_lease(tmp_path):
                pytest.fail("second owner obtained same directory")
    with files.capture_output_lease(tmp_path):
        pass


def test_cleanup_failure_keeps_published_state_and_exact_backup(tmp_path, selected, monkeypatch):
    root, stage, request, _ = prepare(tmp_path, selected)
    original = Path.unlink
    def unlink(path, *args, **kwargs):
        if path.parent.name.startswith(".previous-"):
            raise OSError("injected backup cleanup failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    result = commit_vision_result(request, Event())
    assert result.publication.status == "published" and result.publication.cleanup_pending
    assert (root/"raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    assert (result.plan.backup/"raw_drive_0001.png").read_bytes() == b"old owned screenshot"
    assert_unmanaged(root)


def test_unexpected_stage_member_and_managed_directory_are_rejected(tmp_path, selected):
    root, stage, request, previous = prepare(tmp_path, selected)
    (stage.directory/"unmanaged.txt").write_bytes(b"keep")
    with pytest.raises(files.CaptureFileError):
        commit_vision_result(request, Event())
    assert read_inventory(request.database_path) == (previous, [], 1)
    assert (stage.directory/"unmanaged.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("phase", ["restore", "cleanup"])
@pytest.mark.parametrize("replacement", ["ordinary", "junction"])
def test_changed_backup_parent_never_moves_or_deletes_matching_external_leaf(tmp_path, selected, monkeypatch, phase, replacement):
    root, stage, request, _ = prepare(tmp_path, selected)
    backup = root / (".previous-" + stage.capture_id)
    retained = root / "retained-original-backup"
    external = tmp_path / "outside-output"
    external.mkdir()
    original = files.os.replace
    count, changed = [0], [False]
    def replace_parent():
        backup.rename(retained)
        os.link(retained / "raw_drive_0001.png", external / "raw_drive_0001.png")
        if replacement == "junction":
            import _winapi
            _winapi.CreateJunction(str(external), str(backup))
        else:
            backup.mkdir()
            os.link(external / "raw_drive_0001.png", backup / "raw_drive_0001.png")
        changed[0] = True
    def replace(source, target):
        if Path(source).suffix == ".png":
            count[0] += 1
            if phase == "restore" and count[0] == 2:
                replace_parent()
                raise OSError("injected failure with changed backup parent")
        original(source, target)
        if phase == "cleanup" and Path(target).name == ".nte-publication.json" and not changed[0]:
            if json.loads(Path(target).read_text(encoding="utf-8"))["status"] == "published":
                replace_parent()
    monkeypatch.setattr(files.os, "replace", replace)
    try:
        result = commit_vision_result(request, Event())
        assert changed[0]
        assert read_inventory(request.database_path)[0] == result.snapshot_id
        assert (external / "raw_drive_0001.png").read_bytes() == b"old owned screenshot"
        assert (retained / "raw_drive_0001.png").read_bytes() == b"old owned screenshot"
        if phase == "restore":
            assert result.publication.status == "restore_incomplete"
            assert not (root / "raw_drive_0001.png").exists()
            assert result.publication.remaining_moves
        else:
            assert result.publication.status == "published" and result.publication.cleanup_pending
            assert (root / "raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
        assert_unmanaged(root)
    finally:
        if replacement == "junction" and changed[0]:
            backup.rmdir()  # 精确删除本夹具链接本身，不递归处理指向目录。


@pytest.mark.parametrize("fault", ["publication", "projection", "lease_exit"])
def test_post_sql_exception_always_returns_saved_fact(tmp_path, selected, monkeypatch, fault):
    from src.services.inventory import vision_commit as service
    root, stage, request, previous = prepare(tmp_path, selected)
    def fail(*args, **kwargs):
        raise OSError("injected post-commit failure")
    if fault == "publication":
        monkeypatch.setattr(service, "publish_capture_files", fail)
    elif fault == "projection":
        monkeypatch.setattr(service, "_published_stats", fail)
    else:
        original = service.capture_output_lease
        @contextmanager
        def lease(path):
            with original(path):
                yield
            fail()
        monkeypatch.setattr(service, "capture_output_lease", lease)
    result = commit_vision_result(request, Event())
    assert result.snapshot_id != previous
    assert read_inventory(request.database_path)[0] == result.snapshot_id
    assert read_inventory(request.database_path)[2] == 2
    assert result.post_commit_error == "injected post-commit failure"
    assert result.publication.status == ("restore_incomplete" if fault == "publication" else "published")
    with pytest.raises(ValueError):
        retry_vision_publication(request, result, Event())
    assert_unmanaged(root)


def test_same_leaf_ids_do_not_authorize_replaced_stage_directory(tmp_path, selected):
    root, stage, request, previous = prepare(tmp_path, selected)
    retained = root / "retained-original-stage"
    stage.directory.rename(retained)
    stage.directory.mkdir()
    for name in ("raw_drive_0001.png", ".nte-capture.json"):
        os.link(retained / name, stage.directory / name)
        assert os.path.samefile(retained / name, stage.directory / name)
    retained_stat, replaced_stat = retained.stat(), stage.directory.stat()
    assert (retained_stat.st_dev, retained_stat.st_ino) == stage.directory_identity
    assert (replaced_stat.st_dev, replaced_stat.st_ino) != stage.directory_identity
    # 叶文件身份相同仍不授予替换目录提交权；异常类型与持久事实是业务边界。
    with pytest.raises(files.CaptureFileError):
        commit_vision_result(request, Event())
    assert read_inventory(request.database_path) == (previous, [], 1)
    assert (retained / "raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    assert (stage.directory / "raw_drive_0001.png").read_bytes() == b"new synthetic screenshot"
    assert (root / "raw_drive_0001.png").read_bytes() == b"old owned screenshot"
    assert_unmanaged(root)
