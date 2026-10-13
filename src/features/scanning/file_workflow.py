# 将扫描文件筛选、去重和归档统一委托给 ScanFileLifecycle。
"""File-lifecycle adapters used by the scanning controller."""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
from threading import Event
from src.integrations.vision.capture_files import CaptureStage
from src.integrations.vision.recognition.resources import RecognitionResources
from src.features.scanning.dependencies import scanning_dependencies_are_current
from concurrent.futures import CancelledError
from src.services.inventory.visual_scan_commit import check_vision_result_resources

from src.features.scanning.dependencies import ScanningDependencies
from src.features.scanning.file_lifecycle import (
    ScanFileLifecycle,
    is_scope_image,
)


def scan_lifecycle(owner) -> ScanFileLifecycle:
    dependencies = getattr(owner, "_scan_dependencies", None)
    dependencies = dependencies or ScanningDependencies.from_app_context(
        owner.app_context
    )
    return ScanFileLifecycle(dependencies.screenshot_dir)


def scope_image(
    owner,
    path: Path,
    parse_scope: str,
    skip_names=None,
):
    del owner
    return is_scope_image(path, parse_scope, skip_names)


def matching_scope_files(owner, parse_scope, skip_names=None):
    return owner._scan_lifecycle().matching_scope_files(
        parse_scope,
        skip_names,
    )


def unique_path(owner, directory: Path, name: str):
    return owner._scan_lifecycle().unique_path(directory, name)


def move_to_failed(owner, paths):
    return owner._scan_lifecycle().move_to_failed(paths)


def delete_paths(owner, paths):
    return owner._scan_lifecycle().delete_paths(paths)


def next_full_scan_index(owner):
    return owner._scan_lifecycle().next_full_scan_index()


def rename_incremental_successes(owner, paths):
    return owner._scan_lifecycle().rename_incremental_successes(paths)


def move_first_full_scan_to_tail(owner):
    return owner._scan_lifecycle().move_first_full_scan_to_tail()


def postprocess_vision_files(owner, stats, *, before_files=None):
    token = stats.get("capture_cancel_token")
    resources = stats.get("recognition_resources")
    profile = getattr(resources, "image_profile", None)
    capture = getattr(profile, "capture_template", None)

    def canceled():
        return (before_files is not None and not before_files()) or token is not None and token.is_set()

    def before_change():
        if canceled():
            raise CancelledError("扫描文件后处理已失效，原截图保留。")
        if capture is not None:
            capture.verify()
        if canceled():
            raise CancelledError("扫描文件后处理已失效，原截图保留。")

    check_vision_result_resources(stats, cancel_check=canceled)
    before_change()
    # 准备结果随该次 worker 返回，不从后来任务的 owner 临时字段取删除目标。
    return owner._scan_lifecycle().postprocess_vision_files(
        stats,
        delete_after_parse=stats.get("delete_after_parse", []),
        probe_duplicate_count=stats.get("probe_duplicate_count", 0),
        before_change=before_change,
    )


# 续跑状态只属于当前扫描 owner，不枚举历史暂存或跨账号持久化。
@dataclass(frozen=True)
class CaptureRecovery:
    dependencies: ScanningDependencies
    stage: CaptureStage
    resources: RecognitionResources
    token: Event
    capture_driver: str


def remember_capture_recovery(owner, stats, dependencies) -> None:
    stage, resources, token = (stats.get(key) for key in
        ("capture_stage", "recognition_resources", "capture_cancel_token"))
    driver = stats.get("capture_driver")
    if (driver in {"mouse", "gamepad"} and isinstance(stage, CaptureStage) and stage.files and stage.image_sha256
            and len(stage.files) == int(stats.get("total_count", 0) or 0)
            and isinstance(resources, RecognitionResources) and isinstance(token, Event)
            and not token.is_set() and not owner._manual_recovery.is_closed
            and scanning_dependencies_are_current(owner, dependencies)):
        owner._capture_recovery = CaptureRecovery(dependencies, stage, resources, token, driver)
    else:
        owner._capture_recovery = None
    update_capture_actions(owner)


def clear_capture_recovery(owner) -> None:
    owner._capture_recovery = None
    update_capture_actions(owner)


def update_capture_actions(owner) -> None:
    recovery = getattr(owner, "_capture_recovery", None)
    active = bool(recovery is not None and not owner._manual_recovery.is_closed
        and not recovery.token.is_set()
        and scanning_dependencies_are_current(owner, recovery.dependencies))
    busy = owner.is_running() if callable(getattr(owner, "is_running", None)) else False
    resume = getattr(owner, "btn_resume_capture", None)
    if resume is not None:
        resume.setVisible(active)
        resume.setEnabled(active and not busy)
    retry = getattr(owner, "btn_retry_publication", None)
    if retry is not None:
        available = owner._vision_commit.can_retry_publication()
        retry.setVisible(available)
        retry.setEnabled(available and not busy)


def resume_capture_stage(owner) -> None:
    recovery = getattr(owner, "_capture_recovery", None)
    if (recovery is None or owner.is_running() or owner._manual_recovery.is_closed
            or recovery.token.is_set()
            or not scanning_dependencies_are_current(owner, recovery.dependencies)):
        update_capture_actions(owner)
        return
    owner._pending_parse_only = True  # 继续只解析和保存；不重放扫描后游戏动作或自动分配。
    owner.btn_run.setEnabled(False)
    owner.btn_run.setText("正在继续本次暂存解析...")
    owner._start_vision_processing(replace_output=True, parse_scope="full",
        capture_dependencies=recovery.dependencies, capture_cancel_token=recovery.token,
        capture_stage=recovery.stage, recognition_resources=recovery.resources,
        capture_driver=recovery.capture_driver)
    update_capture_actions(owner)


def retry_capture_publication(owner) -> None:
    if owner.is_running() or not owner._vision_commit.retry_publication():
        update_capture_actions(owner)
        return
    owner.btn_run.setEnabled(False)
    owner.btn_run.setText("正在重试本次截图整理...")
    update_capture_actions(owner)


def publication_retry_done(owner, dependencies, result) -> None:
    if not scanning_dependencies_are_current(owner, dependencies):
        return
    owner.btn_run.setEnabled(True)
    owner.btn_run.setText("⚡  开始计算")
    if result.publication is not None and result.publication.status == "published":
        owner._capture_recovery = None
    update_capture_actions(owner)  # 只投影文件状态；不调用完整入库完成链。
    if not owner._vision_commit.may_continue(dependencies, result):
        return
    from PySide6.QtWidgets import QMessageBox
    if result.publication is not None and result.publication.status == "published":
        message = "本次截图已整理；背包快照未重复保存。"
        if result.publication.cleanup_pending:
            message += "部分旧备份尚未清理，请保留恢复记录。"
        QMessageBox.information(owner.dialog_parent, "截图整理完成", message)
    else:
        QMessageBox.warning(owner.dialog_parent, "截图整理未完成", "已保存背包不变；本次暂存和恢复记录保留，请核对文件状态。")


def publication_retry_failed(owner, dependencies, error) -> None:
    if not scanning_dependencies_are_current(owner, dependencies):
        return
    owner.btn_run.setEnabled(True)
    owner.btn_run.setText("⚡  开始计算")
    update_capture_actions(owner)
    saved = owner._vision_commit.last_result
    if saved is None or not owner._vision_commit.may_continue(dependencies, saved):
        return
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.warning(owner.dialog_parent, "截图重试未完成", f"{error}\n已保存背包不变，本次暂存保留。")


def connect_full_capture(owner, worker, dependencies) -> None:
    def is_current():
        return (owner._gamepad_worker is worker and not owner._manual_recovery.is_closed
                and scanning_dependencies_are_current(owner, dependencies))

    def may_deliver():
        return is_current() and not worker.is_cancelled()

    for signal, callback in ((worker.scan_done, owner._on_gamepad_scan_done),
            (worker.progress, owner._on_gamepad_parse_progress),
            (worker.parse_done, owner._on_gamepad_parse_done),
            (worker.post_actions_ready, owner._on_gamepad_post_actions_ready),
            (worker.processing_done, owner._on_gamepad_pipeline_done),
            (worker.error, owner._on_gamepad_error)):
        signal.connect(lambda *args, callback=callback: callback(*args) if may_deliver() else None)

    def finished():
        if not is_current():
            return
        if worker.is_cancelled():
            # 取消后不复活排队完成/错误信号；这里只恢复控件并等待显式新动作。
            owner._on_gamepad_parse_done()
            owner._stop_scan_hotkeys()
            owner._gamepad_pipeline_finished = True
            owner.btn_run.setEnabled(True)
            owner.btn_run.setText("⚡  开始计算")
        update_capture_actions(owner)
    worker.finished.connect(finished)
