# 验证扫描文件清理的冻结身份、暂存恢复保护和增量基准，不接触设备。
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.integrations.vision.capture_files import (
    capture_output_lease, create_capture_directory, freeze_capture_stage,
)

from src.features.scanning.file_lifecycle import (
    ScanFileLifecycle,
    build_screenshot_cleanup_plan,
    execute_screenshot_cleanup,
    managed_screenshot_usage,
)




class IncrementalBaselineTests(unittest.TestCase):
    def test_screenshot_cleanup_policy_preserves_incremental_baseline_and_cleans_temp_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_root = Path(tmp)
            screenshot_dir = account_root / "scanned_images"
            screenshot_dir.mkdir()
            baseline = screenshot_dir / "raw_drive_0001.png"
            extra = screenshot_dir / "raw_drive_0002.png"
            nested = screenshot_dir / "failed" / "raw_drive_bad.png"
            temp = account_root / "identify_clipboard_123.png"
            other_account_file = account_root / "manual.png"
            nested.parent.mkdir()
            baseline.write_bytes(b"baseline")
            extra.write_bytes(b"extra")
            nested.write_bytes(b"nested")
            temp.write_bytes(b"temp")
            other_account_file.write_bytes(b"manual")

            usage = managed_screenshot_usage(screenshot_dir, account_root)
            plan = build_screenshot_cleanup_plan(screenshot_dir, account_root)
            result = execute_screenshot_cleanup(plan)

            self.assertEqual(4, usage.count)
            self.assertEqual(3, plan.total_count)
            self.assertEqual(2, plan.scan_delete_count)
            self.assertEqual(1, plan.temp_delete_count)
            self.assertFalse(plan.baseline_missing)
            self.assertEqual(3, result.deleted)
            self.assertTrue(baseline.exists())
            self.assertTrue(other_account_file.exists())
            self.assertFalse(extra.exists())
            self.assertFalse(nested.exists())
            self.assertFalse(temp.exists())

    def test_screenshot_cleanup_policy_warns_when_scan_images_exist_without_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            account_root = Path(tmp)
            screenshot_dir = account_root / "scanned_images"
            screenshot_dir.mkdir()
            (screenshot_dir / "raw_drive_0002.png").write_bytes(b"extra")

            plan = build_screenshot_cleanup_plan(screenshot_dir, account_root)

            self.assertTrue(plan.baseline_missing)
            self.assertIn("丢失用于对比的截图", plan.confirmation_text())

    def test_corrupt_raw_drive_0001_marks_incremental_baseline_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            (screenshot_dir / "raw_drive_probe_0001.png").write_bytes(b"not an image")
            (screenshot_dir / "raw_drive_0001.png").write_bytes(b"not an image")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
            )
            result = lifecycle.prepare_incremental_parse("incremental_auto", None)

        self.assertTrue(result.baseline_missing)

    def test_failed_incremental_probe_does_not_replace_raw_drive_0001(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            baseline = screenshot_dir / "raw_drive_0001.png"
            probe = screenshot_dir / "raw_drive_probe_0001.png"
            baseline.write_bytes(b"baseline")
            probe.write_bytes(b"probe")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
            )
            post = lifecycle.postprocess_vision_files(
                {
                    "parse_scope": "incremental_auto",
                    "added_paths": [],
                    "duplicate_paths": [],
                    "failed_paths": [str(probe)],
                }
            )

            self.assertTrue(baseline.exists())
            self.assertEqual(b"baseline", baseline.read_bytes())
            self.assertFalse(probe.exists())
            self.assertTrue((screenshot_dir / "failed" / "raw_drive_probe_0001.png").exists())
            self.assertEqual(1, post["moved_failed"])
            self.assertEqual(0, post["renamed"])


def test_cleanup_lease_and_recovery_preservation(tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    baseline = shots / "raw_drive_0001.png"
    baseline.write_bytes(b"baseline")
    extra = shots / "raw_drive_0002.png"
    extra.write_bytes(b"old screenshot")
    stage_dir = Path(create_capture_directory(shots))
    stage_image = stage_dir / "raw_drive_0001.png"
    stage_image.write_bytes(b"pending capture")
    stage = freeze_capture_stage(shots, stage_dir, 1)
    backup = shots / (".previous-" + stage.capture_id) / "raw_drive_0001.png"
    backup.parent.mkdir()
    backup.write_bytes(b"retained recovery")
    unknown = shots / "other-owner" / "manual.png"
    unknown.parent.mkdir()
    unknown.write_bytes(b"unrelated nested image")
    plan = build_screenshot_cleanup_plan(shots, tmp_path)
    with capture_output_lease(shots):
        result = execute_screenshot_cleanup(plan)
        assert result.deleted == 0 and result.failed_files == [extra]
        assert extra.read_bytes() == b"old screenshot"
    result = execute_screenshot_cleanup(plan)
    assert result.deleted == 1 and not result.failed_files
    assert not extra.exists()
    assert baseline.read_bytes() == b"baseline"
    assert stage_image.read_bytes() == b"pending capture"
    assert backup.read_bytes() == b"retained recovery"
    assert unknown.read_bytes() == b"unrelated nested image"


@pytest.mark.parametrize("change", ["member", "failed_directory", "screenshot_root", "clipboard"])
def test_cleanup_rejects_changed_plan_before_any_deletion(tmp_path, change):
    shots = tmp_path / "shots"
    failed = shots / "failed"
    failed.mkdir(parents=True)
    first = shots / "raw_drive_0002.png"
    nested = failed / "raw_drive_bad.png"
    clipboard = tmp_path / "identify_clipboard_fixture.png"
    for path in (first, nested, clipboard):
        path.write_bytes(b"old member")
    plan = build_screenshot_cleanup_plan(shots, tmp_path)
    if change in {"member", "clipboard"}:
        replacement = tmp_path / "replacement.tmp"
        replacement.write_bytes(b"new task member")
        replacement.replace(first if change == "member" else clipboard)
    else:
        directory = failed if change == "failed_directory" else shots
        directory.rename(directory.with_name(directory.name + "-old"))
        directory.mkdir()
        target = nested if change == "failed_directory" else first
        target.write_bytes(b"new task member")
    result = execute_screenshot_cleanup(plan)
    assert result.deleted == 0 and result.failed_files
    assert first.exists() and clipboard.exists()
    if change != "screenshot_root":
        assert nested.exists()


def test_cleanup_rechecks_before_each_member_and_reports_partial_result(tmp_path, monkeypatch):
    shots = tmp_path / "shots"
    shots.mkdir()
    first, second = (shots / f"raw_drive_{i:04d}.png" for i in (2, 3))
    for path in (first, second):
        path.write_bytes(b"old member")
    plan = build_screenshot_cleanup_plan(shots, tmp_path)
    unlink = Path.unlink
    def replace_next(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if path == first:
            replacement = tmp_path / "replacement.tmp"
            replacement.write_bytes(b"new task member")
            replacement.replace(second)
    monkeypatch.setattr(Path, "unlink", replace_next)
    result = execute_screenshot_cleanup(plan)
    assert result.deleted == 1 and result.failed_files == [second]
    assert second.read_bytes() == b"new task member"


def test_cleanup_clipboard_only_without_screenshot_directory(tmp_path):
    clipboard = tmp_path / "identify_clipboard_fixture.png"
    clipboard.write_bytes(b"temporary image")
    plan = build_screenshot_cleanup_plan(tmp_path / "absent-shots", tmp_path)
    result = execute_screenshot_cleanup(plan)
    assert result.deleted == 1 and not result.failed_files
    assert not clipboard.exists() and not (tmp_path / "absent-shots").exists()


@pytest.mark.parametrize("transition", ["cancel", "unchanged", "account", "aba", "after_execute"])
def test_cleanup_confirmation_preserves_frozen_account(tmp_path, monkeypatch, transition):
    from src.app.context import AccountContext, AppContext
    from src.ui import main_window_data_mixin as entry

    accounts = []
    for identity in ("first", "second"):
        root = tmp_path / identity
        shots = root / "shots"
        shots.mkdir(parents=True)
        (shots / "raw_drive_0001.png").write_bytes(b"baseline")
        (shots / "raw_drive_0002.png").write_bytes(b"extra")
        accounts.append(AccountContext(identity, identity, root, root / "unused.sqlite3",
                                       root / "config", shots, root / "logs"))
    first, second = accounts
    context = AppContext(SimpleNamespace(), first,
                         settings_factory=lambda *_args, **_kwargs: None)
    refreshed = []
    host = SimpleNamespace(app_context=context, _refresh_ss=lambda: refreshed.append(
        (context.account, context.generation)))

    def confirm(*_args):
        # 正式 AppContext 推进代次；回到同一 AccountContext 也不能重新授权旧计划。
        if transition in {"account", "aba"}:
            context.switch_account(second)
            if transition == "aba":
                context.switch_account(first)
        return entry.QMessageBox.No if transition == "cancel" else entry.QMessageBox.Yes

    def execute(plan):
        result = execute_screenshot_cleanup(plan)
        if transition == "after_execute":
            context.switch_account(second)
        return result

    monkeypatch.setattr(entry.QMessageBox, "question", confirm)
    monkeypatch.setattr(entry, "execute_screenshot_cleanup", execute)
    entry.MainWindowDataMixin._clear_ss(host)

    assert (first.screenshot_dir / "raw_drive_0002.png").exists() == (
        transition not in {"unchanged", "after_execute"})
    assert (second.screenshot_dir / "raw_drive_0002.png").read_bytes() == b"extra"
    assert refreshed == ([(first, 0)] if transition == "unchanged" else [])
    assert (first.screenshot_dir / "raw_drive_0001.png").read_bytes() == b"baseline"


