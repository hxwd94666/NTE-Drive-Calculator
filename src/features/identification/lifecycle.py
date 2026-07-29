# 管理鉴定任务冻结依赖、运行状态和剪贴板临时文件清理。
"""Lifecycle helpers shared by identification controller callbacks."""

from __future__ import annotations

from src.features.identification.dependencies import IdentificationDependencies
from src.features.identification.page import parse_identify_paths
from src.features.identification.temp_files import cleanup_identify_clipboard_files


def current_identification_dependencies(owner) -> IdentificationDependencies:
    return IdentificationDependencies.from_app_context(owner.app_context)


def task_identification_dependencies(owner) -> IdentificationDependencies:
    dependencies = getattr(owner, "_identify_dependencies", None)
    return dependencies or current_identification_dependencies(owner)


def identification_is_running(owner) -> bool:
    for name in ("_identify_parse_worker", "_identify_worker"):
        worker = getattr(owner, name, None)
        if worker is not None and callable(getattr(worker, "isRunning", None)):
            if worker.isRunning():
                return True
    return False


def cleanup_pending_identify_clipboard_files(owner) -> None:
    dependencies = task_identification_dependencies(owner)
    paths = list(
        getattr(owner, "_pending_identify_clipboard_cleanup", []) or []
    )
    owner._pending_identify_clipboard_cleanup = []
    if not paths:
        return
    cleanup_identify_clipboard_files(paths, dependencies.account_data_root)
    remaining = [
        path
        for path in parse_identify_paths(owner.ident_path_edit.text())
        if path not in paths and path.exists()
    ]
    owner.ident_path_edit.setText(";".join(str(path) for path in remaining))

