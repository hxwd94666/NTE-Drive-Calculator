# 将扫描文件筛选、去重和归档统一委托给 ScanFileLifecycle。
"""File-lifecycle adapters used by the scanning controller."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from src.i18n import tr
from src.features.scanning.dependencies import ScanningDependencies
from src.features.scanning.file_lifecycle import (
    ScanFileLifecycle,
    is_scope_image,
)
from src.scanner.batch_processor import BatchProcessor


def scan_lifecycle(owner) -> ScanFileLifecycle:
    dependencies = getattr(owner, "_scan_dependencies", None)
    dependencies = dependencies or ScanningDependencies.from_app_context(
        owner.app_context
    )
    return ScanFileLifecycle(
        dependencies.screenshot_dir,
        dependencies.config_dir,
        BatchProcessor,
    )


def scope_image(
    owner,
    path: Path,
    parse_scope: str,
    skip_names=None,
):
    del owner
    return is_scope_image(path, parse_scope, skip_names)


def prepare_incremental_parse(owner, parse_scope):
    owner._pending_delete_after_parse = []
    owner._pending_probe_duplicate_count = 0
    result = owner._scan_lifecycle().prepare_incremental_parse(parse_scope)
    if result.baseline_missing:
        QMessageBox.warning(
            owner.dialog_parent,
            tr("需要重新全量扫描"),
            tr("由于版本更新解析逻辑变动，需要重新进行全量扫描"),
        )
        return None
    owner._pending_delete_after_parse = list(result.delete_after_parse)
    owner._pending_probe_duplicate_count = result.probe_duplicate_count
    return set(result.skip_names)


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


def postprocess_vision_files(owner, stats):
    result = owner._scan_lifecycle().postprocess_vision_files(
        stats,
        delete_after_parse=(
            getattr(owner, "_pending_delete_after_parse", []) or []
        ),
        probe_duplicate_count=getattr(
            owner,
            "_pending_probe_duplicate_count",
            0,
        ),
    )
    owner._pending_delete_after_parse = []
    return result
