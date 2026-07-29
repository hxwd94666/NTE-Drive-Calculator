# 编排多角色极速装配的预检、任务持久化、顺序执行和快照复查。
"""Bulk nte-core equipment apply workflow independent from Qt and MainWindow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.observability.context import OperationContext
from src.observability.operation import operation_scope
from src.services.equipment_apply_service import EquipmentApplyService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


ProgressCallback = Callable[[dict[str, Any]], None] | None


def report_bulk_apply_progress(
    callback: ProgressCallback,
    *,
    current: int,
    total: int,
    message: str,
) -> None:
    if not callable(callback):
        return
    try:
        callback(
            {
                "current": max(0, int(current)),
                "total": max(1, int(total)),
                "message": str(message),
            }
        )
    except Exception:
        logger.debug("极速装配进度回调失败", exc_info=True)


def _snapshot_timeout(user_dao: UserDataDao) -> float:
    try:
        settle_seconds = float(user_dao.get_sync_settings()["inventory_settle_seconds"])
    except (KeyError, TypeError, ValueError):
        settle_seconds = 5.0
    return max(1.0, settle_seconds) + 5.0


class BulkEquipmentApplyService:
    """Run one persisted, snapshot-pinned multi-role equipment apply job."""

    def __init__(
        self,
        database_path: str | Path,
        sync_service,
        *,
        dao_factory=UserDataDao,
        apply_service_factory=EquipmentApplyService,
        operation_context: OperationContext | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sync_service = sync_service
        self.dao_factory = dao_factory
        self.apply_service_factory = apply_service_factory
        self.operation_context = operation_context or OperationContext.create(
            "equipment_apply"
        )

    def run(
        self,
        role_names: list[str],
        *,
        identity_overrides: dict[str, dict] | None = None,
        job_id: int | None = None,
        progress_callback: ProgressCallback = None,
    ) -> dict[str, Any]:
        with operation_scope(
            self.operation_context,
            started_event="equipment_apply.bulk_started",
            succeeded_event="equipment_apply.bulk_succeeded",
            failed_event="equipment_apply.bulk_failed",
            message="执行极速装配",
            requested_role_count=len(role_names),
            resume_job_id=job_id,
        ) as span:
            result = self._run(
                role_names,
                identity_overrides=identity_overrides,
                job_id=job_id,
                progress_callback=progress_callback,
            )
            span.annotate(
                job_id=result.get("job_id"),
                applied_count=len(result.get("applied") or []),
                unresolved_identity_count=len(result.get("identity_requests") or []),
                preflight_error_count=len(result.get("preflight_errors") or []),
                completed=bool(result.get("completed")),
            )
            return result

    def _run(
        self,
        role_names: list[str],
        *,
        identity_overrides: dict[str, dict] | None = None,
        job_id: int | None = None,
        progress_callback: ProgressCallback = None,
    ) -> dict[str, Any]:
        identity_overrides = identity_overrides or {}
        applied: list[dict[str, Any]] = []
        identity_requests: list[dict[str, Any]] = []
        pinned_snapshot_id: int | None = None
        with self.dao_factory(self.database_path) as user_dao:
            apply_service = self.apply_service_factory(
                user_dao,
                self.sync_service,
            )
            if job_id is not None:
                prepared, pinned_snapshot_id, early = self._prepare_resume(
                    user_dao,
                    apply_service,
                    job_id,
                )
            else:
                (
                    prepared,
                    identity_requests,
                    job_id,
                    early,
                ) = self._prepare_new(
                    user_dao,
                    apply_service,
                    role_names,
                    identity_overrides,
                )
            if early is not None:
                return early

            stable_snapshot_id = pinned_snapshot_id or apply_service.require_stable_snapshot()
            failure = self._execute_prepared(
                user_dao,
                apply_service,
                prepared,
                stable_snapshot_id,
                applied,
                identity_requests,
                int(job_id),
                progress_callback,
            )
            if failure is not None:
                return failure

            postcheck = self._postcheck_and_repair(
                user_dao,
                apply_service,
                prepared,
                applied,
                stable_snapshot_id,
                progress_callback,
            )
            completed = user_dao.complete_equipment_apply_job_if_done(int(job_id))
        return {
            "job_id": job_id,
            "applied": applied,
            "identity_requests": identity_requests,
            **postcheck,
            "completed": completed,
        }

    def _prepare_resume(
        self,
        user_dao,
        apply_service,
        job_id: int,
    ) -> tuple[list[dict], int | None, dict | None]:
        job = user_dao.get_equipment_apply_job(job_id)
        if job is None:
            raise RuntimeError(f"装配任务 {job_id} 不存在")
        user_dao.reset_failed_equipment_apply_job_items(job_id)
        prepared = []
        for row in job["items"]:
            if row["status"] not in {"pending", "running", "failed"}:
                continue
            plan = user_dao.get_loadout_plan(row["plan_id"]) or {}
            assignments = plan.get("assignments", [])
            prepared.append(
                {
                    "job_item_id": row["job_item_id"],
                    "role_name": row["role_name"],
                    "character_id": row["character_id"],
                    "character_uid": row["character_uid"],
                    "plan_id": row["plan_id"],
                    "module_count": sum(1 for assignment in assignments if assignment["kind"] == "module"),
                    "core_count": sum(1 for assignment in assignments if assignment["kind"] == "core"),
                }
            )
        if not prepared:
            return (
                [],
                None,
                {
                    "job_id": job_id,
                    "applied": [],
                    "completed": job["status"] == "completed",
                },
            )
        pinned_snapshot_id = apply_service.require_stable_snapshot()
        preflight_errors = self._plan_preflight_errors(
            apply_service,
            prepared,
            pinned_snapshot_id,
        )
        if preflight_errors:
            return (
                prepared,
                pinned_snapshot_id,
                {
                    "job_id": job_id,
                    "applied": [],
                    "preflight_errors": preflight_errors,
                },
            )
        return prepared, pinned_snapshot_id, None

    def _prepare_new(
        self,
        user_dao,
        apply_service,
        role_names: list[str],
        identity_overrides: dict[str, dict],
    ) -> tuple[list[dict], list[dict], int | None, dict | None]:
        snapshot_id = user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            raise RuntimeError("用户数据库中还没有稳定背包快照")
        prepared: list[dict] = []
        identity_requests: list[dict] = []
        preflight_errors: list[dict] = []
        for role_name in role_names:
            plan = user_dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError(
                    f"装配前检查 [{role_name}] 失败，尚未发送任何装配指令："
                    "没有来自官方背包快照的已保存方案，请重新计算并保存。"
                )
            source_snapshot_id = plan.get("source_snapshot_id")
            source_summary = (
                user_dao.inventory_snapshot_summary(int(source_snapshot_id)) if source_snapshot_id is not None else None
            )
            if source_summary is None or source_summary.get("source") != "nte_core":
                raise RuntimeError(
                    f"装配前检查 [{role_name}] 失败，视觉扫描库存没有本地组件可用的原生 UID。"
                    "请改用自动装配；极速装配仅支持抓包稳定快照。"
                )
            try:
                apply_service.validate_plan_for_fast_apply(
                    plan["plan_id"],
                    stable_snapshot_id=snapshot_id,
                )
            except Exception as exc:
                preflight_errors.append({"role_name": role_name, "error": str(exc)})
                continue
            override = identity_overrides.get(role_name)
            character_id = int(plan["character_id"])
            try:
                targets = self._resolve_character_targets(
                    apply_service,
                    character_id,
                    snapshot_id,
                    override,
                )
                character_id = int(targets[0]["character_id"])
                character_uid = targets[0]["character_uid"]
            except Exception as exc:
                identity_requests.append(
                    {
                        "role_name": role_name,
                        "candidate_character_ids": [character_id],
                        "reason": str(exc),
                    }
                )
                continue
            prepared.append(
                {
                    "role_name": role_name,
                    "character_id": character_id,
                    "character_uid": character_uid,
                    "plan_id": plan["plan_id"],
                    "fallback_targets": targets[1:],
                    "module_count": sum(1 for row in plan["assignments"] if row["kind"] == "module"),
                    "core_count": sum(1 for row in plan["assignments"] if row["kind"] == "core"),
                }
            )
        if preflight_errors:
            return (
                prepared,
                identity_requests,
                None,
                {"applied": [], "preflight_errors": preflight_errors},
            )
        if not prepared:
            return (
                [],
                identity_requests,
                None,
                {
                    "applied": [],
                    "identity_requests": identity_requests,
                    "completed": True,
                },
            )
        try:
            apply_service.validate_bulk_plans_for_fast_apply(
                prepared,
                stable_snapshot_id=snapshot_id,
            )
        except Exception as exc:
            return (
                prepared,
                identity_requests,
                None,
                {
                    "applied": [],
                    "identity_requests": identity_requests,
                    "preflight_errors": [{"role_name": "全角色方案", "error": str(exc)}],
                },
            )
        job_id = user_dao.create_equipment_apply_job(
            snapshot_id,
            prepared,
        )
        job_items = user_dao.get_equipment_apply_job(job_id)["items"]
        for entry, role in zip(job_items, prepared):
            role["job_item_id"] = entry["job_item_id"]
        return prepared, identity_requests, job_id, None

    @staticmethod
    def _resolve_character_targets(
        apply_service,
        character_id: int,
        snapshot_id: int,
        override: dict | None,
    ) -> list[dict]:
        candidate_ids = apply_service.resolve_fast_apply_character_ids(
            character_id,
            snapshot_id,
        )
        targets = []
        for candidate_id in candidate_ids:
            if override is not None and int(override["character_id"]) != candidate_id:
                continue
            targets.append(
                {
                    "character_id": candidate_id,
                    "character_uid": apply_service.resolve_character_uid(
                        candidate_id,
                        snapshot_id,
                        explicit_uid=(override.get("character_uid") if override else None),
                    ),
                }
            )
        if not targets:
            raise RuntimeError("当前主角实例与手动选择结果不匹配")
        if override is not None and int(override["character_id"]) != int(targets[0]["character_id"]):
            raise RuntimeError("手动选择的角色实例与当前主角装配目标不匹配")
        return targets

    @staticmethod
    def _plan_preflight_errors(
        apply_service,
        prepared: list[dict],
        snapshot_id: int,
    ) -> list[dict]:
        errors = []
        for role in prepared:
            try:
                apply_service.validate_plan_for_fast_apply(
                    role["plan_id"],
                    stable_snapshot_id=snapshot_id,
                )
            except Exception as exc:
                errors.append({"role_name": role["role_name"], "error": str(exc)})
        if errors:
            return errors
        try:
            apply_service.validate_bulk_plans_for_fast_apply(
                prepared,
                stable_snapshot_id=snapshot_id,
            )
        except Exception as exc:
            return [{"role_name": "全角色方案", "error": str(exc)}]
        return []

    def _execute_prepared(
        self,
        user_dao,
        apply_service,
        prepared: list[dict],
        stable_snapshot_id: int,
        applied: list[dict],
        identity_requests: list[dict],
        job_id: int,
        progress_callback: ProgressCallback,
    ) -> dict | None:
        report_bulk_apply_progress(
            progress_callback,
            current=0,
            total=len(prepared),
            message="正在顺序下发全角色装配指令…",
        )
        for index, role in enumerate(prepared, start=1):
            role_name = role["role_name"]
            report_bulk_apply_progress(
                progress_callback,
                current=index - 1,
                total=len(prepared),
                message=f"正在下发 [{role_name}] 的装配指令…",
            )
            user_dao.mark_equipment_apply_job_item(
                role["job_item_id"],
                status="running",
            )
            try:
                result, character_id = self._apply_role(
                    apply_service,
                    role,
                    stable_snapshot_id,
                )
                user_dao.mark_equipment_apply_job_item(
                    role["job_item_id"],
                    status="succeeded",
                    before_snapshot_id=result.before_snapshot_id,
                    after_snapshot_id=result.after_snapshot_id,
                    verified=result.verified,
                )
                applied.append(
                    {
                        "role_name": role_name,
                        "character_id": character_id,
                        "plan_id": role["plan_id"],
                        "module_count": role.get("module_count"),
                        "core_count": role.get("core_count"),
                        "snapshot_id": result.after_snapshot_id,
                        "verified": result.verified,
                        "already_applied": result.already_applied,
                        "character_uid": result.character_uid,
                    }
                )
                report_bulk_apply_progress(
                    progress_callback,
                    current=index,
                    total=len(prepared),
                    message=(f"[{role_name}] 已确认" if result.verified else f"[{role_name}] 指令已下发"),
                )
            except Exception as exc:
                user_dao.mark_equipment_apply_job_item(
                    role["job_item_id"],
                    status="failed",
                    error=str(exc),
                )
                report_bulk_apply_progress(
                    progress_callback,
                    current=index - 1,
                    total=len(prepared),
                    message=f"[{role_name}] 下发失败",
                )
                return {
                    "job_id": job_id,
                    "applied": applied,
                    "identity_requests": identity_requests,
                    "failed_role": role_name,
                    "error": str(exc),
                    "completed": False,
                }
        return None

    @staticmethod
    def _apply_role(apply_service, role: dict, snapshot_id: int):
        targets = [
            {
                "character_id": role["character_id"],
                "character_uid": role["character_uid"],
            },
            *list(role.get("fallback_targets") or ()),
        ]
        result = None
        character_id = int(role["character_id"])
        last_error: Exception | None = None
        for index, target in enumerate(targets):
            try:
                character_id = int(target["character_id"])
                result = apply_service.apply_plan(
                    role["plan_id"],
                    character_uid=target["character_uid"],
                    target_character_id=character_id,
                    timeout=30.0,
                    verify_after_dispatch=False,
                    exact_loadout=True,
                    force_dispatch=True,
                    reset_before_apply=True,
                    stable_snapshot_id=snapshot_id,
                )
                if index:
                    logger.warning("主角女主实例装配失败后，已改用男主实例下发成功")
                break
            except Exception as exc:
                last_error = exc
                if index + 1 >= len(targets):
                    raise
                logger.warning(
                    "主角女主实例装配失败，正在尝试男主实例：{}",
                    exc,
                )
        if result is None:
            raise last_error or RuntimeError("主角装配未返回结果")
        return result, character_id

    def _postcheck_and_repair(
        self,
        user_dao,
        apply_service,
        prepared: list[dict],
        applied: list[dict],
        stable_snapshot_id: int,
        progress_callback: ProgressCallback,
    ) -> dict[str, Any]:
        output = {
            "postcheck_snapshot_id": None,
            "postrepair_snapshot_id": None,
            "postrepair_check_timed_out": False,
            "repair_errors": [],
        }
        if len(prepared) <= 1 or len(applied) != len(prepared):
            return output
        timeout = _snapshot_timeout(user_dao)
        report_bulk_apply_progress(
            progress_callback,
            current=len(prepared),
            total=len(prepared),
            message="正在等待装配后快照并检查遗漏…",
        )
        try:
            state = self.sync_service.wait_for_snapshot(
                after_snapshot_id=stable_snapshot_id,
                timeout=timeout,
            )
            output["postcheck_snapshot_id"] = state.last_snapshot_id
        except TimeoutError:
            logger.info("极速全角色装配后快照检查超时；保留已下发结果")
            return output
        except Exception as exc:
            logger.warning("极速全角色装配后快照检查失败：{}", exc)
            return output
        postcheck_id = output["postcheck_snapshot_id"]
        if postcheck_id is None or postcheck_id <= stable_snapshot_id:
            return output
        repaired = self._repair_in_snapshot(
            apply_service,
            applied,
            postcheck_id,
            output["repair_errors"],
        )
        if not repaired:
            return output
        report_bulk_apply_progress(
            progress_callback,
            current=len(prepared),
            total=len(prepared),
            message="补装已下发，正在等待第二份快照复查…",
        )
        try:
            state = self.sync_service.wait_for_snapshot(
                after_snapshot_id=postcheck_id,
                timeout=timeout,
            )
            output["postrepair_snapshot_id"] = state.last_snapshot_id
        except TimeoutError:
            output["postrepair_check_timed_out"] = True
            logger.info("极速全角色补装后二次快照检查超时；补装指令已下发，但无法确认最终状态")
            return output
        except Exception as exc:
            output["postrepair_check_timed_out"] = True
            logger.warning("极速全角色补装后二次快照检查失败：{}", exc)
            return output
        postrepair_id = output["postrepair_snapshot_id"]
        if postrepair_id is not None and postrepair_id > postcheck_id:
            self._verify_repairs(
                apply_service,
                repaired,
                postrepair_id,
                output["repair_errors"],
            )
        return output

    @staticmethod
    def _repair_in_snapshot(
        apply_service,
        applied: list[dict],
        snapshot_id: int,
        errors: list[dict],
    ) -> list[dict]:
        repaired = []
        for row in applied:
            try:
                repair = apply_service.apply_plan(
                    row["plan_id"],
                    character_uid=row["character_uid"],
                    target_character_id=row["character_id"],
                    timeout=30.0,
                    verify_after_dispatch=False,
                    exact_loadout=True,
                    force_dispatch=False,
                    reset_before_apply=True,
                    stable_snapshot_id=snapshot_id,
                )
                row["snapshot_id"] = snapshot_id
                if repair.already_applied:
                    row["verified"] = True
                    continue
                row["repaired"] = True
                repaired.append(row)
                logger.warning(
                    "装配后快照发现 [{}] 配装不完整，已卸空并补装",
                    row["role_name"],
                )
            except Exception as exc:
                errors.append({"role_name": row["role_name"], "error": str(exc)})
                logger.error(
                    "装配后快照补装 [{}] 失败：{}",
                    row["role_name"],
                    exc,
                )
        return repaired

    @staticmethod
    def _verify_repairs(
        apply_service,
        repaired: list[dict],
        snapshot_id: int,
        errors: list[dict],
    ) -> None:
        for row in repaired:
            try:
                mismatch = apply_service.verify_plan_in_snapshot(
                    row["plan_id"],
                    character_uid=row["character_uid"],
                    target_character_id=row["character_id"],
                    exact_loadout=True,
                    stable_snapshot_id=snapshot_id,
                )
                row["snapshot_id"] = snapshot_id
                if mismatch is None:
                    row["verified"] = True
                    row["repair_verified"] = True
                    continue
                row["repair_verification_error"] = mismatch
                errors.append(
                    {
                        "role_name": row["role_name"],
                        "error": f"补装后复查仍不完整：{mismatch}",
                    }
                )
                logger.error(
                    "补装后快照复查 [{}] 仍不完整：{}",
                    row["role_name"],
                    mismatch,
                )
            except Exception as exc:
                errors.append(
                    {
                        "role_name": row["role_name"],
                        "error": f"补装后复查失败：{exc}",
                    }
                )
                logger.error(
                    "补装后快照复查 [{}] 失败：{}",
                    row["role_name"],
                    exc,
                )
