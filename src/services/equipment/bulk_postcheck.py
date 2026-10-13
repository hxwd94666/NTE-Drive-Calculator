# 编排批量装配请求投影与独立原生后验复核，未知结果不重发。
"""请求投影仅供展示；优先用独立完整观测复核批量装配结果。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import CancelledError
import time
from typing import Any

from src.integrations.nte_core import equipment_request_failure_kind
from src.services.equipment.apply_recovery import RecoveryStopped
from src.utils.logger import logger


ProgressReporter = Callable[[int, int, str, bool], None]


def postcheck_and_repair(
    sync_service,
    user_dao,
    apply_service,
    prepared: list[dict],
    applied: list[dict],
    *,
    stable_snapshot_id: int,
    frozen_inventory_uids: frozenset[tuple[int, int]],
    timeout: float,
    max_attempts: int,
    report_progress: ProgressReporter,
    check_cancelled: Callable[[], None] | None = None,
    observation_cursor: int | None = None,
    check_source: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """优先使用原生完整观测精确复核；局部片段仅作不落库的既有兜底。
    
    请求状态的展示投影不是成功证据，未知字段或未观测结果不触发重发。"""

    output = {
        "postcheck_snapshot_id": None,
        "postrepair_snapshot_id": None,
        "postrepair_check_timed_out": False,
        "snapshot_wait_failure": None,
        "attempt_snapshots": [],
        "repair_errors": [],
        "scoped_verification_count": 0,
        "scoped_snapshot_wait_timed_out": False,
        "scoped_unverified_count": 0,
        "full_snapshot_verification_count": 0,
        "full_snapshot_wait_timed_out": False,
    }
    if not applied or len(applied) != len(prepared):
        return output
    pending = [row for row in applied if not row.get("already_applied")]
    for row in applied:
        row["attempt_count"] = 0 if row.get("already_applied") else 1
    if not pending:
        return output

    after_snapshot_id = stable_snapshot_id
    for attempt in range(1, max_attempts + 1):
        if check_cancelled is not None:
            check_cancelled()
        if attempt > 1:
            cursor_reader = getattr(sync_service, "inventory_observation_cursor", None)
            if callable(cursor_reader):
                observation_cursor = cursor_reader()
            pending = dispatch_retry_attempt(
                sync_service,
                apply_service,
                pending,
                after_snapshot_id,
                attempt,
                output["repair_errors"],
                check_cancelled=check_cancelled,
                user_dao=user_dao,
            )
            if output["repair_errors"]:
                output["snapshot_wait_failure"] = dict(output["repair_errors"][-1])
                return output
            if not pending:
                return output

        report_progress(
            len(prepared),
            len(prepared),
            f"第 {attempt} 次装配已下发，正在等待完整背包快照复核…",
            True,
        )
        round_deadline = time.monotonic() + timeout
        observation: dict = {}
        full_snapshot_id = (
            wait_for_guarded_full_snapshot(
                sync_service,
                user_dao,
                after_snapshot_id=after_snapshot_id,
                frozen_inventory_uids=frozen_inventory_uids,
                timeout=max(0.0, round_deadline - time.monotonic()),
                check_cancelled=check_cancelled,
                after_cursor=observation_cursor,
                observation_sink=observation,
            )
            if frozen_inventory_uids
            else None
        )
        if full_snapshot_id is not None:
            if check_source is not None:
                check_source()
            after_snapshot_id = full_snapshot_id
            if output["postcheck_snapshot_id"] is None:
                output["postcheck_snapshot_id"] = full_snapshot_id
            if attempt > 1:
                output["postrepair_snapshot_id"] = full_snapshot_id
            logger.info(
                "极速装配第 {} 轮收到完整背包快照，开始精确复核 {} 个角色",
                attempt,
                len(pending),
            )
            pending = verify_complete_snapshot(
                user_dao,
                apply_service,
                pending,
                snapshot_id=full_snapshot_id,
                check_cancelled=check_cancelled,
                observed_items=observation.get("items"),
            )
            output["full_snapshot_verification_count"] = sum(
                bool(row.get("full_snapshot_verified")) for row in applied
            )
            if any(row.get("full_snapshot_verification_error") for row in pending):
                output["snapshot_wait_failure"] = {
                    "attempt": attempt, "kind": "snapshot_error",
                    "error": "完整背包复核字段不完整，已停止后续装配请求。",
                }
                return output
            if not pending:
                logger.info("极速装配完整快照复核通过")
                return output
            if attempt == max_attempts:
                append_final_mismatch_errors(output["repair_errors"], pending, attempt)
                return output
            continue

        output["full_snapshot_wait_timed_out"] = True
        if check_source is not None:
            check_source()
        logger.info("极速装配第 {} 轮未收到完整背包快照，转入局部事件兜底复核", attempt)
        pending = verify_scoped_equipment_events(
            sync_service,
            user_dao,
            apply_service,
            pending,
            timeout=max(0.0, round_deadline - time.monotonic()),
            check_cancelled=check_cancelled,
        )
        output["scoped_verification_count"] = sum(
            bool(row.get("scoped_verified")) for row in applied
        )
        if not pending:
            return output

        retryable = [
            row for row in pending
            if row.get("scoped_event_observed") and row.get("last_mismatch")
        ]
        output["scoped_unverified_count"] = len(pending) - len(retryable)
        if not retryable:
            output["scoped_snapshot_wait_timed_out"] = True
            return output
        if attempt == max_attempts:
            append_final_mismatch_errors(output["repair_errors"], retryable, attempt)
            return output
        pending = retryable
    return output


def wait_for_guarded_full_snapshot(
    sync_service,
    user_dao,
    *,
    after_snapshot_id: int,
    frozen_inventory_uids: frozenset[tuple[int, int]],
    timeout: float,
    check_cancelled: Callable[[], None] | None = None,
    after_cursor: int | None = None,
    observation_sink: dict | None = None,
) -> int | None:
    """等待原生完整观测，要求原冻结UID集合不变且观测游标确实推进。"""

    waiter = getattr(sync_service, "wait_for_snapshot", None)
    if not callable(waiter):
        return None
    observation_waiter = getattr(sync_service, "wait_for_inventory_observation", None)
    observed = after_cursor is not None and callable(observation_waiter)
    deadline = time.monotonic() + timeout
    while True:
        if check_cancelled is not None:
            check_cancelled()
        remaining = max(0.0, deadline - time.monotonic())
        try:
            wait_seconds = min(0.25, remaining) if check_cancelled is not None else remaining
            state = (observation_waiter(after_cursor=after_cursor, timeout=wait_seconds) if observed else
                     waiter(after_snapshot_id=after_snapshot_id, timeout=wait_seconds))
            break
        except TimeoutError:
            if check_cancelled is None or time.monotonic() >= deadline:
                return None
    snapshot_id = getattr(state, "last_snapshot_id", None)
    if (not isinstance(snapshot_id, int) or snapshot_id < after_snapshot_id
        or (not observed and snapshot_id == after_snapshot_id)
        or (observed and state.cursor <= after_cursor)):
        return None
    summary = user_dao.inventory_snapshot_summary(snapshot_id)
    if (
        summary is None
        or summary.get("source") != "nte_core"
        or not bool(summary.get("complete"))
    ):
        return None
    rows = list(state.items) if observed and hasattr(state, "items") else user_dao.list_inventory_items(snapshot_id)
    snapshot_uids = frozenset(
        (int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0))
        for row in rows
        if int(row.get("uid_slot") or 0) > 0 and int(row.get("uid_serial") or 0) > 0
    )
    if snapshot_uids != frozen_inventory_uids:
        return None
    if observed and hasattr(state, "items") and observation_sink is not None:
        observation_sink["items"] = rows
    return snapshot_id


def verify_complete_snapshot(
    user_dao,
    apply_service,
    pending: list[dict],
    *,
    snapshot_id: int,
    check_cancelled: Callable[[], None] | None = None,
    observed_items: list[dict] | None = None,
) -> list[dict]:
    verifier = getattr(apply_service, "verify_plan_in_snapshot", None)
    if not callable(verifier):
        return pending
    unresolved: list[dict] = []
    for row in pending:
        if check_cancelled is not None:
            check_cancelled()
        row.pop("full_snapshot_verification_error", None)
        try:
            verification = apply_service.verify_plan_in_items if observed_items is not None else verifier
            source = {"items": observed_items} if observed_items is not None else {"stable_snapshot_id": snapshot_id}
            mismatch = verification(
                row["plan_id"],
                character_uid=row["character_uid"],
                target_character_id=row["character_id"],
                exact_loadout=True,
                ignore_module_placement=True,
                **source,
            )
        except CancelledError:
            raise
        except Exception as exc:
            row["full_snapshot_verification_error"] = str(exc)
            unresolved.append(row)
            continue
        if mismatch is not None:
            row["last_mismatch"] = mismatch
            unresolved.append(row)
            continue
        row["verified"] = True
        row["full_snapshot_verified"] = True
        row["verification_source"] = "full_inventory_snapshot"
        if row.get("repaired"):
            row["repair_verified"] = True
        if check_cancelled is not None:
            check_cancelled()
        user_dao.mark_equipment_apply_job_item(
            row["job_item_id"],
            status="succeeded",
            before_snapshot_id=None,
            after_snapshot_id=snapshot_id,
            verified=True,
        )
    return unresolved


def append_final_mismatch_errors(
    errors: list[dict],
    pending: list[dict],
    attempt: int,
) -> None:
    for row in pending:
        errors.append({
            "role_name": row["role_name"],
            "attempt": attempt,
            "kind": "loadout_mismatch",
            "error": f"第 {attempt} 次装配后完整快照复核仍不一致：{row['last_mismatch']}",
        })


def verify_scoped_equipment_events(
    sync_service,
    user_dao,
    apply_service,
    pending: list[dict],
    *,
    timeout: float,
    check_cancelled: Callable[[], None] | None = None,
) -> list[dict]:
    waiter = getattr(sync_service, "wait_for_observed_equipment_snapshot", None)
    if not callable(waiter):
        waiter = getattr(sync_service, "wait_for_equipment_snapshot", None)
    verifier = getattr(apply_service, "verify_plan_in_items", None)
    if not callable(waiter) or not callable(verifier):
        return pending
    unresolved: list[dict] = []
    deadline = time.monotonic() + max(0.0, timeout)
    for row in pending:
        if check_cancelled is not None:
            check_cancelled()
        required_uids = row.get("scoped_required_uids")
        cursor = row.get("scoped_snapshot_cursor")
        if not isinstance(required_uids, frozenset) or not isinstance(cursor, int):
            unresolved.append(row)
            continue
        try:
            while True:
                if check_cancelled is not None:
                    check_cancelled()
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    scoped_snapshot = waiter(required_uids, after_cursor=cursor,
                        timeout=min(0.25, remaining) if check_cancelled is not None else remaining)
                    break
                except TimeoutError:
                    if check_cancelled is None or time.monotonic() >= deadline:
                        raise
            row["scoped_event_observed"] = True
            _log_scoped_equipment_snapshot(
                role_name=str(row["role_name"]),
                required_uids=required_uids,
                items=list(scoped_snapshot.items),
            )
            mismatch = verifier(
                row["plan_id"],
                items=list(scoped_snapshot.items),
                character_uid=row["character_uid"],
                target_character_id=row["character_id"],
                exact_loadout=False,
                fragment_only=True,
            )
        except CancelledError:
            raise
        except TimeoutError:
            logger.info(
                "极速装配局部事件诊断：角色={}，目标件={}，本轮未收到包含目标装备的局部事件",
                row["role_name"],
                len(required_uids),
            )
            unresolved.append(row)
            continue
        except Exception as exc:
            row["scoped_verification_error"] = str(exc)
            unresolved.append(row)
            continue
        if mismatch is not None:
            row["last_mismatch"] = mismatch
            unresolved.append(row)
            continue
        row["verified"] = True
        row["scoped_verified"] = True
        if row.get("repaired"):
            row["repair_verified"] = True
        row["verification_source"] = "scoped_equipment_event"
        if check_cancelled is not None:
            check_cancelled()
        user_dao.mark_equipment_apply_job_item(
            row["job_item_id"],
            status="succeeded",
            before_snapshot_id=None,
            after_snapshot_id=None,
            verified=True,
        )
    return unresolved


def _log_scoped_equipment_snapshot(
    *,
    role_name: str,
    required_uids: frozenset[tuple[int, int]],
    items: list[dict],
) -> None:
    """只记录字段是否存在与计数，不序列化装备UID或原始事件。"""

    by_uid = {
        (int(item.get("uid", {}).get("slot") or 0), int(item.get("uid", {}).get("serial") or 0)): item
        for item in items
        if isinstance(item.get("uid"), dict)
    }
    planned_items = [by_uid[pair] for pair in required_uids if pair in by_uid]
    logger.info(
        "极速装配局部事件诊断：角色={}，目标件={}，已回传={}，已装备={}，含角色ID={}，含角色实例={}，含格位={}",
        role_name,
        len(required_uids),
        len(planned_items),
        sum(item.get("equipped") is True for item in planned_items),
        sum(isinstance(item.get("equipped_character_id"), int) for item in planned_items),
        sum(isinstance(item.get("equipped_character_uid"), dict) for item in planned_items),
        sum(isinstance(item.get("equipped_placement"), dict) for item in planned_items),
    )


def dispatch_retry_attempt(
    sync_service,
    apply_service,
    pending: list[dict],
    snapshot_id: int,
    attempt: int,
    errors: list[dict],
    *,
    check_cancelled: Callable[[], None] | None = None,
    user_dao=None,
) -> list[dict]:
    dispatched = []
    for row in pending:
        if check_cancelled is not None:
            check_cancelled()
        try:
            cursor_reader = getattr(sync_service, "scoped_equipment_snapshot_cursor", None)
            if callable(cursor_reader):
                row["scoped_snapshot_cursor"] = int(cursor_reader())
            row.pop("last_mismatch", None)
            row.pop("scoped_event_observed", None)
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
                row["repair_verified"] = True
                continue
            row["repaired"] = True
            row["attempt_count"] = attempt
            dispatched.append(row)
            logger.warning(
                "第 {} 次装配前复核发现 [{}] 配装不完整，已卸空并重装",
                attempt,
                row["role_name"],
            )
        except CancelledError:
            raise
        except Exception as exc:
            kind = "recovery_exhausted" if isinstance(exc, RecoveryStopped) else equipment_request_failure_kind(exc)
            errors.append({
                "role_name": row["role_name"],
                "attempt": attempt,
                "kind": kind,
                "error": f"第 {attempt} 次装配请求失败：{exc}",
            })
            if user_dao is not None:
                if check_cancelled is not None:
                    check_cancelled()
                user_dao.mark_equipment_apply_job_item(row["job_item_id"], status="failed", verified=False,
                    error=f"第 {attempt} 次装配请求失败：{exc}")
            logger.error("第 {} 次装配 [{}] 请求失败：{}", attempt, row["role_name"], exc)
            # 修复请求失败后停止后续角色；无回执不表示未执行，不重复未知副作用。
            return []
    return dispatched


def project_dispatched_loadouts(
    user_dao,
    applied: list[dict],
    *,
    snapshot_id: int,
    frozen_plans: dict[int, dict] | None = None,
) -> int:
    """把已接受请求投影到原固定库存，不改成员集合、目标或当前快照指针。
    
    该投影仅供展示和后续诊断；原生新观测覆盖它，确认路径不以它证明成功。"""

    projector = getattr(user_dao, "apply_inventory_command_state_projection", None)
    if not applied or not callable(projector):
        return 0
    rows = user_dao.list_inventory_items(snapshot_id)
    by_uid = {(int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0)): row for row in rows}
    target_character_uids = {
        (int(role["character_uid"]["slot"]), int(role["character_uid"]["serial"]))
        for role in applied
        if isinstance(role.get("character_uid"), dict)
    }
    projected: list[dict] = []
    for row in rows:
        character_uid = row.get("equipped_character_uid")
        if not isinstance(character_uid, dict):
            continue
        pair = (int(character_uid.get("slot") or 0), int(character_uid.get("serial") or 0))
        if pair not in target_character_uids:
            continue
        item = dict(row)
        item["uid"] = {"slot": row["uid_slot"], "serial": row["uid_serial"]}
        item.update(
            {
                "equipped": False,
                "equipped_character_id": None,
                "equipped_character_uid": None,
                "equipped_placement": None,
            }
        )
        projected.append(item)
    for role in applied:
        plan = (
            frozen_plans.get(int(role["plan_id"]))
            if frozen_plans is not None
            else user_dao.get_loadout_plan(int(role["plan_id"]))
        )
        if plan is None:
            continue
        character_uid = dict(role["character_uid"])
        for assignment in plan.get("assignments") or ():
            pair = (
                int(assignment.get("uid_slot") or 0),
                int(assignment.get("uid_serial") or 0),
            )
            row = by_uid.get(pair)
            if row is None:
                continue
            item = dict(row)
            item["uid"] = {"slot": pair[0], "serial": pair[1]}
            placement = None
            if assignment.get("kind") == "module":
                placement = {
                    "row": assignment.get("target_row"),
                    "column": assignment.get("target_column"),
                }
            item.update(
                {
                    "equipped": True,
                    "equipped_character_id": int(role["character_id"]),
                    "equipped_character_uid": character_uid,
                    "equipped_placement": placement,
                }
            )
            projected.append(item)
    return int(projector(snapshot_id, projected))
