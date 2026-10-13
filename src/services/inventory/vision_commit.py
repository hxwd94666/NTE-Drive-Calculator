# 冻结视觉提交输入，在后台先授权背包事务，再独占发布截图，保留独立失败状态。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event
from typing import Any

from src.integrations.vision.capture_files import (
    CapturePublication, CapturePublicationResult, CaptureStage,
    capture_output_lease, prepare_capture_publication, publish_capture_files,
)
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class VisionCommitRequest:
    database_path: Path
    stats: dict[str, Any]
    manual_items: tuple[dict[str, Any], ...]

    @classmethod
    def freeze(cls, database_path: Path, stats: dict[str, Any], manual_items: list[dict]) -> VisionCommitRequest:
        immutable = {id(stats[key]): stats[key] for key in ("recognition_resources", "capture_stage", "capture_cancel_token") if key in stats}
        private = deepcopy(stats, immutable)
        manual = tuple(deepcopy(manual_items))
        private["_completed_manual_items"] = list(manual)
        return cls(database_path, private, manual)


@dataclass(frozen=True)
class VisionCommitResult:
    snapshot_id: int | None
    stats: dict[str, Any]
    publication: CapturePublicationResult | None = None
    plan: CapturePublication | None = None
    post_commit_error: str = ""


def _published_stats(stats: dict[str, Any], stage: CaptureStage) -> dict[str, Any]:
    result = dict(stats)
    for key in ("added_paths", "duplicate_paths", "failed_paths"):
        result[key] = [str(stage.root / Path(path).name) for path in stats.get(key, [])]
    result["pending_manual_items"] = [dict(row, image_path=str(stage.root / Path(row["image_path"]).name))
                                      for row in stats.get("pending_manual_items", [])]
    return result


def _publish_saved(result: VisionCommitResult, token: Event) -> VisionCommitResult:
    try:
        publication = publish_capture_files(result.plan, result.snapshot_id, token.is_set)
        result = replace(result, publication=publication)
        if publication.status == "published":
            result = replace(result, stats=_published_stats(result.stats, result.plan.stage))
    except Exception as error:
        # 不预料的文件/投影错误也保留已保存 ID；未确认的文件状态禁止自动重试。
        result = replace(result, publication=result.publication or CapturePublicationResult("restore_incomplete"),
                         post_commit_error=str(error))
    return result


def commit_vision_result(request: VisionCommitRequest, token: Event) -> VisionCommitResult:
    stage = request.stats.get("capture_stage")
    if stage is None:
        snapshot_id = commit_completed_vision_inventory(request.database_path, request.stats,
            request.manual_items, cancel_check=token.is_set)
        return VisionCommitResult(snapshot_id, request.stats)
    if not isinstance(stage, CaptureStage):
        raise ValueError("全量截图缺少正式暂存身份。")
    result = None
    try:
        with capture_output_lease(stage.root):
            plan = prepare_capture_publication(stage)  # 容量/身份预检失败在 DB 写入之前。
            snapshot_id = commit_completed_vision_inventory(request.database_path, request.stats,
                request.manual_items, cancel_check=token.is_set)
            result = VisionCommitResult(snapshot_id, request.stats, plan=plan)
            if snapshot_id is not None:
                result = _publish_saved(result, token)
    except Exception as error:
        if result is None or result.snapshot_id is None:
            raise
        # 包括租约退出异常；结果已有保存事实，不能退回泛型 failed。
        result = replace(result, post_commit_error=str(error))
    return result


def retry_vision_publication(request: VisionCommitRequest, saved: VisionCommitResult,
                             token: Event) -> VisionCommitResult:
    """仅重试确切文件发布，不再次写背包或推进指针；残缺恢复必须先人工核对。"""
    if saved.snapshot_id is None or saved.plan is None or saved.publication is None or saved.publication.status != "pending":
        raise ValueError("本次结果不适合直接重试截图发布。")
    result = None
    try:
        with capture_output_lease(saved.plan.stage.root):
            with UserDataDao(request.database_path) as dao:
                if dao.current_inventory_snapshot_id() != saved.snapshot_id:
                    raise ValueError("当前背包已更新，请先核对原截图任务。")
            plan = prepare_capture_publication(saved.plan.stage)
            if plan.previous != saved.plan.previous:
                raise ValueError("原截图基线已改变，不覆盖新任务的文件。")
            result = _publish_saved(VisionCommitResult(saved.snapshot_id, request.stats, plan=plan), token)
    except Exception as error:
        if result is None:
            raise
        result = replace(result, post_commit_error=str(error))
    return result
