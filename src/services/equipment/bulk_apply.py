# 编排多角色极速装配的预检、任务持久化、顺序执行和快照复查。
"""编排原生多角色装配，任务和冻结输入不归UI控件所有。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any

from src.integrations.nte_core import equipment_request_failure_kind
from src.integrations.operation_guard import OperationGuard, require_operation
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.services.equipment.apply_recovery import EquipmentApplyRecovery, RecoveryStopped
from src.services.equipment.bulk_postcheck import postcheck_and_repair, project_dispatched_loadouts
from src.services.equipment.apply import EquipmentApplyError, EquipmentApplyService
from src.services.equipment.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


ProgressCallback = Callable[[dict[str, Any]], None] | None
MAX_EQUIPMENT_APPLY_ATTEMPTS = 3
FULL_SNAPSHOT_VERIFICATION_SECONDS = 10.0
POST_APPLY_GUARD_GRACE_SECONDS = 90.0


def report_bulk_apply_progress(
    callback: ProgressCallback,
    *,
    current: int,
    total: int,
    message: str,
    show_progress_bar: bool = True,
) -> None:
    if not callable(callback):
        return
    try:
        callback(
            {
                "current": max(0, int(current)),
                "total": max(1, int(total)),
                "message": str(message),
                "show_progress_bar": bool(show_progress_bar),
            }
        )
    except Exception:
        logger.debug("极速装配进度回调失败", exc_info=True)


def _snapshot_timeout(user_dao: UserDataDao) -> float:
    """返回单轮派发后的完整快照等待预算，不随用户库状态扩张。"""

    del user_dao
    return FULL_SNAPSHOT_VERIFICATION_SECONDS


class BulkEquipmentApplyService:
    """编排一个已持久化、库存及目标均冻结的多角色装配任务。"""

    def __init__(
        self,
        database_path: str | Path,
        sync_service,
        *,
        dao_factory=UserDataDao,
        apply_service_factory=EquipmentApplyService,
        operation_context: OperationContext | None = None,
        operation_guard: OperationGuard | None = None,
        cancel_event: Event | None = None,
        check_current: Callable[[], None] | None = None,
    ) -> None:
        self.operation_guard = operation_guard
        self.database_path = Path(database_path)
        self.sync_service = sync_service
        self.dao_factory = dao_factory
        self.apply_service_factory = apply_service_factory
        self.operation_context = operation_context or OperationContext.create(
            "equipment_apply"
        )
        self.cancel_event = cancel_event or Event()
        self.check_current = check_current or (lambda: None)

    def _check(self):
        self.check_current()
        require_operation(self.operation_guard, "native_equipment")
        if self.cancel_event.is_set():
            raise CancelledError("装配任务已取消")

    def run(
        self,
        role_names: list[str] | None = None,
        *,
        slot_ids: list[int] | None = None,
        identity_overrides: dict[str, dict] | None = None,
        job_id: int | None = None,
        progress_callback: ProgressCallback = None,
    ) -> dict[str, Any]:
        self._check()
        if job_id is None and bool(role_names) == bool(slot_ids):
            raise RuntimeError("极速装配必须指定角色或显式配装槽位（二者只能选其一）")
        requested_count = len(slot_ids or ()) if slot_ids else len(role_names or ())
        started = monotonic()
        log_event("INFO", "equipment_apply.bulk_started", "执行极速装配",
                  self.operation_context, requested_role_count=requested_count,
                  resume_job_id=job_id, phase="started")
        try:
            result = self._run(
                role_names or [],
                slot_ids=slot_ids,
                identity_overrides=identity_overrides,
                job_id=job_id,
                progress_callback=progress_callback,
            )
        except Exception as error:
            log_event("ERROR", "equipment_apply.bulk_failed", "极速装配未完成",
                      self.operation_context, phase="failed", result="failed",
                      error_type=type(error).__name__, duration_ms=round((monotonic() - started) * 1000, 3))
            raise
        failed = not result.get("completed") or bool(result.get("repair_errors") or result.get("snapshot_wait_failure"))
        log_event("WARNING" if failed else "INFO",
                  "equipment_apply.bulk_failed" if failed else "equipment_apply.bulk_succeeded",
                  "极速装配未完成" if failed else "极速装配派发完成",
                  self.operation_context, job_id=result.get("job_id"),
                  applied_count=len(result.get("applied") or []), completed=bool(result.get("completed")),
                  recovery_retry_count=result.get("recovery_retry_count", 0),
                  snapshot_recovery_count=result.get("snapshot_recovery_count", 0),
                  failure_kind=result.get("failure_kind"), phase="failed" if failed else "succeeded",
                  result="failed" if failed else "succeeded", duration_ms=round((monotonic() - started) * 1000, 3))
        return result

    def _run(
        self,
        role_names: list[str],
        *,
        slot_ids: list[int] | None = None,
        identity_overrides: dict[str, dict] | None = None,
        job_id: int | None = None,
        progress_callback: ProgressCallback = None,
    ) -> dict[str, Any]:
        identity_overrides = identity_overrides or {}
        applied: list[dict[str, Any]] = []
        identity_requests: list[dict[str, Any]] = []
        pinned_snapshot_id: int | None = None
        with self.dao_factory(self.database_path) as user_dao:
            self._check()
            apply_service = self.apply_service_factory(
                user_dao,
                self.sync_service,
                operation_guard=self.operation_guard,
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
                    slot_ids,
                    identity_overrides,
                )
            if early is not None:
                return early

            stable_snapshot_id = pinned_snapshot_id or apply_service.require_stable_snapshot()
            apply_service.execution_check = self._check
            frozen_plans = apply_service.freeze_plans(prepared, stable_snapshot_id)
            apply_service.validate_bulk_plans_for_fast_apply(prepared, stable_snapshot_id=stable_snapshot_id)
            frozen_inventory_uids = self._inventory_uid_pairs(user_dao, stable_snapshot_id)
            recovery = EquipmentApplyRecovery(
                self.sync_service, user_dao, snapshot_id=stable_snapshot_id,
                inventory_uids=frozen_inventory_uids, operation_guard=self.operation_guard,
                cancel_event=self.cancel_event, check_current=self.check_current,
                operation_context=self.operation_context.with_values(job_id=job_id),
                progress=lambda message: report_bulk_apply_progress(progress_callback,
                    current=len(applied), total=len(prepared), message=message),
            )
            apply_service.recovery = recovery
            guard_token = self._begin_full_inventory_guard(
                frozen_inventory_uids,
                source_snapshot_id=stable_snapshot_id,
            )
            try:
                with recovery.batch():
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
                self.check_current()
                projected_count = self._project_dispatched_loadouts(
                    user_dao,
                    applied,
                    snapshot_id=stable_snapshot_id,
                    frozen_plans=frozen_plans,
                )
                if failure is not None:
                    failure["projected_count"] = projected_count
                    failure["recovery_retry_count"] = recovery.short_retry_count
                    failure["snapshot_recovery_count"] = recovery.total_sync_recoveries
                    return failure

                self._check()
                postcheck = self._postcheck_and_repair(
                    user_dao,
                    apply_service,
                    prepared,
                    applied,
                    stable_snapshot_id,
                    progress_callback,
                    frozen_inventory_uids=frozen_inventory_uids,
                    observation_cursor=recovery.last_dispatch_observation_cursor,
                    check_source=recovery.check_source,
                )
                self._check()
                completed = user_dao.complete_equipment_apply_job_if_done(int(job_id))
            finally:
                self._end_full_inventory_guard(guard_token)
                apply_service.recovery = None
        return {
            "job_id": job_id,
            "applied": applied,
            "identity_requests": identity_requests,
            **postcheck,
            "projected_count": projected_count,
            "completed": completed,
            "recovery_retry_count": recovery.short_retry_count,
            "snapshot_recovery_count": recovery.total_sync_recoveries,
        }

    @staticmethod
    def _inventory_uid_pairs(user_dao, snapshot_id: int) -> frozenset[tuple[int, int]]:
        return frozenset(
            (int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0))
            for row in user_dao.list_inventory_items(snapshot_id)
            if int(row.get("uid_slot") or 0) > 0 and int(row.get("uid_serial") or 0) > 0
        )

    @staticmethod
    def _project_dispatched_loadouts(user_dao, applied: list[dict], *, snapshot_id: int,
                                   frozen_plans: dict[int, dict] | None = None) -> int:
        return project_dispatched_loadouts(user_dao, applied, snapshot_id=snapshot_id,
                                           frozen_plans=frozen_plans)

    def _begin_full_inventory_guard(
        self,
        item_uids: frozenset[tuple[int, int]],
        *,
        source_snapshot_id: int,
    ) -> object | None:
        begin = getattr(self.sync_service, "begin_full_inventory_guard", None)
        if not callable(begin):
            return None
        try:
            return begin(item_uids, source_snapshot_id=source_snapshot_id)
        except TypeError:
            return begin(item_uids)

    def _end_full_inventory_guard(self, token: object | None) -> None:
        if token is None:
            return
        finish = getattr(self.sync_service, "finish_full_inventory_guard", None)
        if callable(finish) and finish(
            token,
            grace_seconds=POST_APPLY_GUARD_GRACE_SECONDS,
        ):
            return
        end = getattr(self.sync_service, "end_full_inventory_guard", None)
        if callable(end):
            end(token)

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
        slot_ids: list[int] | None,
        identity_overrides: dict[str, dict],
    ) -> tuple[list[dict], list[dict], int | None, dict | None]:
        snapshot_id = user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            raise RuntimeError("用户数据库中还没有稳定背包快照")
        prepared: list[dict] = []
        identity_requests: list[dict] = []
        preflight_errors: list[dict] = []
        selected_plans: list[tuple[str, dict]] = []
        selection_service = LoadoutSlotSelectionService(user_dao)
        if slot_ids:
            requested_selections = selection_service.resolve(slot_ids)
        else:
            requested_selections = selection_service.resolve_default_roles(role_names)
        custom_character_ids = {
            int(row["character_id"])
            for row in user_dao.list_custom_characters()
        }
        custom_selections = [
            selection for selection in requested_selections
            if selection.character_id in custom_character_ids
        ]
        if custom_selections:
            return (
                [],
                [],
                None,
                {
                    "applied": [],
                    "preflight_errors": [
                        {
                            "role_name": selection.role_name,
                            "error": "自建角色没有游戏角色实例，极速装配不适用；请使用自动装配。",
                        }
                        for selection in custom_selections
                    ],
                },
            )
        native_selections = selection_service.resolve(
            [selection.slot_id for selection in requested_selections],
            require_native_snapshot=True,
        )
        for selection in native_selections:
            selected_plans.append((selection.role_name, dict(selection.plan)))
        for role_name, plan in selected_plans:
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
            self._check()
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
                cursor_reader = getattr(
                    self.sync_service, "scoped_equipment_snapshot_cursor", None
                )
                required_uid_reader = getattr(
                    apply_service, "plan_equipment_uid_pairs", None
                )
                if callable(cursor_reader) and callable(required_uid_reader):
                    role["scoped_snapshot_cursor"] = int(cursor_reader())
                    role["scoped_required_uids"] = required_uid_reader(role["plan_id"])
                result, character_id = self._apply_role(
                    apply_service,
                    role,
                    stable_snapshot_id,
                )
                self.check_current()
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
                        "job_item_id": role["job_item_id"],
                        "character_id": character_id,
                        "plan_id": role["plan_id"],
                        "module_count": role.get("module_count"),
                        "core_count": role.get("core_count"),
                        "snapshot_id": result.after_snapshot_id,
                        "verified": result.verified,
                        "already_applied": result.already_applied,
                        "character_uid": result.character_uid,
                        # 派发前游标随实际applied项保存；后验复核不追随可变prepared请求。
                        "scoped_snapshot_cursor": role.get("scoped_snapshot_cursor"),
                        "scoped_required_uids": role.get("scoped_required_uids"),
                    }
                )
                report_bulk_apply_progress(
                    progress_callback,
                    current=index,
                    total=len(prepared),
                    message=(f"[{role_name}] 已确认" if result.verified else f"[{role_name}] 指令已下发"),
                )
            except Exception as exc:
                self.check_current()
                kind = ("cancelled" if isinstance(exc, CancelledError) else
                        "recovery_exhausted" if isinstance(exc, RecoveryStopped) else
                        equipment_request_failure_kind(exc))
                log_event("WARNING", "equipment_apply.role_failed", "角色装配已停止",
                          self.operation_context, job_id=job_id, plan_id=role["plan_id"],
                          character_id=role["character_id"], failure_kind=kind,
                          stop_reason=getattr(exc, "reason", kind),
                          step=getattr(exc, "step", "command"),
                          module_index=getattr(exc, "module_index", 0),
                          error_code=getattr(exc, "code", None), domain_code=getattr(exc, "domain_code", None))
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
                    "failure_kind": kind,
                    "stop_reason": getattr(exc, "reason", kind),
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
                # A different protagonist may only resolve a local preflight
                # failure, never an attempted command or transport failure.
                if (not isinstance(exc, EquipmentApplyError)
                    or getattr(apply_service, "dispatch_started", False)):
                    raise
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
        *,
        frozen_inventory_uids: frozenset[tuple[int, int]] | None = None,
        observation_cursor: int | None = None,
        check_source: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        return postcheck_and_repair(
            self.sync_service,
            user_dao,
            apply_service,
            prepared,
            applied,
            stable_snapshot_id=stable_snapshot_id,
            frozen_inventory_uids=frozen_inventory_uids or frozenset(),
            timeout=_snapshot_timeout(user_dao),
            max_attempts=MAX_EQUIPMENT_APPLY_ATTEMPTS,
            check_cancelled=self._check,
            observation_cursor=observation_cursor,
            check_source=check_source,
            report_progress=lambda current, total, message, show_progress_bar: report_bulk_apply_progress(
                progress_callback,
                current=current,
                total=total,
                message=message,
                show_progress_bar=show_progress_bar,
            ),
        )
