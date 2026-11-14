# 将冻结方案及结构化装配证据适配为展示状态，不从进度中文或当前数据库反推执行结果。
from __future__ import annotations

from src.domain.equipment.assembly.assembly_progress import AssemblyTarget
from src.domain.equipment.loadouts.loadout_plan_scores import persisted_loadout_total_score
from src.services.equipment.loadouts.loadout_slot_selection_service import LoadoutSlotSelectionService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger


def freeze_assembly_targets(database_path, role_names, slot_ids=None, *, dao_factory=UserDataDao):
    with dao_factory(database_path) as dao:
        return _targets_from_dao(dao, role_names, slot_ids)


def _targets_from_dao(dao, role_names, slot_ids):
    service = LoadoutSlotSelectionService(dao)
    selections = (
        service.resolve(slot_ids, defer_equipment_conflicts=True)
        if slot_ids
        else service.resolve_default_roles(role_names, defer_equipment_conflicts=True)
    )
    return tuple(
        AssemblyTarget(
            row.slot_id,
            row.character_id,
            row.role_name,
            row.slot_name,
            persisted_loadout_total_score(row.plan),
            row.plan_id,
            row.source_snapshot_id,
        )
        for row in selections
    )


def apply_progress_event(model, event):
    if event.get("phase"):
        model.phase = event["phase"]
    if not event.get("state"):
        return
    # 正式事件以槽位／方案身份关联；旧执行器只返回名字时由冻结任务边界作窄适配。
    for row in model.rows:
        target = row.target
        matches = (
            target.slot_id == event["slot_id"]
            if event.get("slot_id") is not None
            else target.plan_id == event["plan_id"]
            if event.get("plan_id") is not None
            else target.role_name == event.get("role_name")
        )
        if matches:
            model.update(
                target.slot_id,
                event["state"],
                **{
                    key: event[key]
                    for key in ("dispatched", "verified", "scope", "detail", "skip_counts")
                    if key in event
                },
            )


def reconcile_fast_result(model, report):
    for item in report.get("applied") or ():
        verified = bool(
            item.get("full_snapshot_verified") or (item.get("verified") and not item.get("scoped_verified"))
        )
        partial = bool(item.get("skipped_assignments"))
        apply_progress_event(
            model,
            {
                **item,
                "state": "partial" if partial and verified else "confirmed" if verified else "unknown",
                "dispatched": True,
                "verified": verified,
                "scope": "subset"
                if partial or (item.get("scoped_verified") and not verified)
                else "full"
                if verified
                else "none",
                "skip_counts": _skip_counts(item),
            },
        )
    for key in ("skipped_roles", "identity_requests", "preflight_errors", "repair_errors"):
        for item in report.get(key) or ():
            apply_progress_event(
                model,
                {
                    **item,
                    "state": "unknown" if key == "repair_errors" else "skipped",
                    "verified": False,
                    "detail": str(item.get("error") or item.get("reason") or ""),
                    "skip_counts": _skip_counts(item),
                },
            )
    if report.get("failed_role"):
        unknown = report.get("failure_kind") in {"outcome_unknown", "core_request_timeout", "cancelled"}
        apply_progress_event(
            model,
            {
                "role_name": report["failed_role"],
                "state": "unknown" if unknown else "failed",
                "detail": str(report.get("error") or ""),
                "verified": False,
            },
        )
    model.finish_pending(
        stopped=bool(report.get("failed_role") or report.get("snapshot_wait_failure") or report.get("preflight_errors"))
    )


def _skip_counts(item):
    skipped = item.get("skipped_assignments") or ()
    return (
        sum(row.get("reason") == "virtual" for row in skipped),
        sum(row.get("reason") == "missing" for row in skipped),
    )


def emit_role_progress(callback, role, state, *, phase="dispatching", dispatched=None, verified=None, scope=None):
    if not callable(callback):
        return
    event = {key: role[key] for key in ("plan_id", "slot_id", "role_name") if key in role}
    if "skipped_assignments" in role:
        event["skip_counts"] = _skip_counts(role)
    event.update(phase=phase)
    if state is not None:
        event["state"] = state
    for key, value in (("dispatched", dispatched), ("verified", verified), ("scope", scope)):
        if value is not None:
            event[key] = value
    try:
        callback(event)
    except Exception:
        # 展示失效不改变已冻结业务任务，不记录角色或请求正文。
        logger.debug("装配状态展示回调失败", exc_info=True)


def emit_verification_progress(callback, row):
    full = bool(row.get("full_snapshot_verified"))
    partial = bool(row.get("skipped_assignments"))
    emit_role_progress(
        callback,
        row,
        "partial" if full and partial else "confirmed" if full else "unknown",
        phase="verifying",
        dispatched=True,
        verified=full,
        scope="full" if full and not partial else "subset",
    )
