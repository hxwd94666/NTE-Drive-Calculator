"""Complete-snapshot-first post-checking for bulk equipment apply."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from src.integrations.nte_core import equipment_request_failure_kind
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
) -> dict[str, Any]:
    """Prefer a guarded complete snapshot; retain residual checks as fallback.

    The full path provides exact position and ownership validation.  Scoped
    packets remain a non-persisted fallback for core versions that only return
    role fragments after an equipment request.
    """

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
        if attempt > 1:
            pending = dispatch_retry_attempt(
                sync_service,
                apply_service,
                pending,
                stable_snapshot_id,
                attempt,
                output["repair_errors"],
            )
            if not pending:
                return output

        report_progress(
            len(prepared),
            len(prepared),
            f"第 {attempt} 次装配已下发，正在等待完整背包快照复核…",
            True,
        )
        round_deadline = time.monotonic() + timeout
        full_snapshot_id = (
            wait_for_guarded_full_snapshot(
                sync_service,
                user_dao,
                after_snapshot_id=after_snapshot_id,
                frozen_inventory_uids=frozen_inventory_uids,
                timeout=max(0.0, round_deadline - time.monotonic()),
            )
            if frozen_inventory_uids
            else None
        )
        if full_snapshot_id is not None:
            after_snapshot_id = full_snapshot_id
            output["postcheck_snapshot_id"] = full_snapshot_id
            if attempt > 1:
                output["postrepair_snapshot_id"] = full_snapshot_id
            pending = verify_complete_snapshot(
                user_dao,
                apply_service,
                pending,
                snapshot_id=full_snapshot_id,
            )
            output["full_snapshot_verification_count"] = sum(
                bool(row.get("full_snapshot_verified")) for row in applied
            )
            if not pending:
                return output
            if attempt == max_attempts:
                append_final_mismatch_errors(output["repair_errors"], pending, attempt)
                return output
            continue

        output["full_snapshot_wait_timed_out"] = True
        pending = verify_scoped_equipment_events(
            sync_service,
            user_dao,
            apply_service,
            pending,
            timeout=max(0.0, round_deadline - time.monotonic()),
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
) -> int | None:
    """Wait for a native complete snapshot with the original full UID set."""

    waiter = getattr(sync_service, "wait_for_snapshot", None)
    if not callable(waiter):
        return None
    try:
        state = waiter(
            after_snapshot_id=after_snapshot_id,
            timeout=max(0.0, timeout),
        )
    except TimeoutError:
        return None
    snapshot_id = getattr(state, "last_snapshot_id", None)
    if not isinstance(snapshot_id, int) or snapshot_id <= after_snapshot_id:
        return None
    summary = user_dao.inventory_snapshot_summary(snapshot_id)
    if (
        summary is None
        or summary.get("source") != "nte_core"
        or not bool(summary.get("complete"))
    ):
        return None
    snapshot_uids = frozenset(
        (int(row.get("uid_slot") or 0), int(row.get("uid_serial") or 0))
        for row in user_dao.list_inventory_items(snapshot_id)
        if int(row.get("uid_slot") or 0) > 0 and int(row.get("uid_serial") or 0) > 0
    )
    return snapshot_id if snapshot_uids == frozen_inventory_uids else None


def verify_complete_snapshot(
    user_dao,
    apply_service,
    pending: list[dict],
    *,
    snapshot_id: int,
) -> list[dict]:
    verifier = getattr(apply_service, "verify_plan_in_snapshot", None)
    if not callable(verifier):
        return pending
    unresolved: list[dict] = []
    for row in pending:
        try:
            mismatch = verifier(
                row["plan_id"],
                character_uid=row["character_uid"],
                target_character_id=row["character_id"],
                exact_loadout=True,
                stable_snapshot_id=snapshot_id,
            )
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
) -> list[dict]:
    waiter = getattr(sync_service, "wait_for_equipment_snapshot", None)
    verifier = getattr(apply_service, "verify_plan_in_items", None)
    if not callable(waiter) or not callable(verifier):
        return pending
    unresolved: list[dict] = []
    deadline = time.monotonic() + max(0.0, timeout)
    for row in pending:
        required_uids = row.get("scoped_required_uids")
        cursor = row.get("scoped_snapshot_cursor")
        if not isinstance(required_uids, frozenset) or not isinstance(cursor, int):
            unresolved.append(row)
            continue
        try:
            scoped_snapshot = waiter(
                required_uids,
                after_cursor=cursor,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            row["scoped_event_observed"] = True
            mismatch = verifier(
                row["plan_id"],
                items=list(scoped_snapshot.items),
                character_uid=row["character_uid"],
                target_character_id=row["character_id"],
                exact_loadout=False,
                fragment_only=True,
            )
        except TimeoutError:
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
        user_dao.mark_equipment_apply_job_item(
            row["job_item_id"],
            status="succeeded",
            before_snapshot_id=None,
            after_snapshot_id=None,
            verified=True,
        )
    return unresolved


def dispatch_retry_attempt(
    sync_service,
    apply_service,
    pending: list[dict],
    snapshot_id: int,
    attempt: int,
    errors: list[dict],
) -> list[dict]:
    dispatched = []
    for row in pending:
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
        except Exception as exc:
            errors.append({
                "role_name": row["role_name"],
                "attempt": attempt,
                "kind": equipment_request_failure_kind(exc),
                "error": f"第 {attempt} 次装配请求失败：{exc}",
            })
            logger.error("第 {} 次装配 [{}] 请求失败：{}", attempt, row["role_name"], exc)
    return dispatched
