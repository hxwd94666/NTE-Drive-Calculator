# 冻结多角色标准指标模型并派发一次联合组作业，缺员整组保B0且独立组继续。
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import dataclass

from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError
from src.services.allocation.direct_postprocess import (
    MODEL_ID, OBJECTIVE,
)
from src.services.allocation.axis_postprocess import freeze_actual_allocation_inventory
from src.services.allocation.postprocess_facts import (
    allocation_inputs_digest, allocation_save_projection, allocation_row_save_projection,
    locked_allocation_owners, restore_postprocess_results, verify_optimization_batch_rows,
    postprocess_first_scan_cut, linked_save_dependencies,
)
from src.services.official_role.page import load_official_role_detail
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class FrozenDirectGroupInputs:
    details: tuple[tuple[str, Mapping], ...]
    role_character_ids: tuple[tuple[str, int], ...]
    source_inventory: tuple[Mapping, ...]
    reserved_uids: tuple[str, ...]
    owners_sha256: str
    snapshot_id: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class DirectGroupPreviewSource:
    inputs: FrozenDirectGroupInputs
    metadata_sha256: str
    save_projection_sha256: str


def freeze_direct_group_inputs(request, *, user_database_path, static_read_unit, source_inventory, reserved_uids=(), checkpoint=None):
    if checkpoint:
        checkpoint()
    copied = tuple(deepcopy(dict(item)) for item in source_inventory)
    snapshots = {row["official"].get("snapshot_id") for row in copied if isinstance(row.get("official"), Mapping)}
    snapshot = next(iter(snapshots)) if len(snapshots) == 1 else None
    ids = tuple((role, request.roles_db[role]["character_id"]) for role in request.role_order
        if not request.roles_db[role].get("is_custom") and type(request.roles_db[role].get("character_id")) is int
        and request.roles_db[role]["character_id"] > 0)
    reason = ""
    with UserDataDao(user_database_path) as dao, dao.read_consistent_state():
        owners = locked_allocation_owners(dao)
        try:
            if type(snapshot) is not int or snapshot != dao.current_inventory_snapshot_id():
                raise ValueError
            freeze_actual_allocation_inventory(request, dao, static_read_unit, snapshot, checkpoint=checkpoint)
        except NativeAnalysisCancelled:
            raise CancelledError from None
        except (ValueError, RuntimeError):
            reason = "完整库存的装备形状或正式词条来源未闭合"
    reserved = set(reserved_uids)
    reserved.update(f"nte-{kind}-{slot}-{serial}" for _plan, _character, kind, slot, serial, _revision in owners)
    details = []
    if not reason:
        for role, identity in ids:
            if checkpoint:
                checkpoint()
            try:
                detail = load_official_role_detail(user_database_path, identity, include_inventory_contexts=False,
                    include_replacement_candidates=False, static_database_path=static_read_unit.release.database_path,
                    static_read_unit=static_read_unit)
                details.append((role, detail))
            except (OSError, ValueError, RuntimeError):
                pass  # 缺模型成员留在原 groups；不是把成员从组目标中删掉。
    return FrozenDirectGroupInputs(tuple(details), ids, copied, tuple(sorted(reserved)),
                                   allocation_inputs_digest(owners), snapshot, reason)


def _base_metadata(frozen, client, role):
    return {"objective": OBJECTIVE, "model_id": MODEL_ID, "source_snapshot_id": frozen.snapshot_id,
        "character_id": dict(frozen.role_character_ids).get(role), "dataset_id": client.dataset_version,
        "engine_version": client.engine_version, "owners_sha256": frozen.owners_sha256,
        "allocation_character_ids": [identity for _, identity in frozen.role_character_ids],
        **getattr(client, "component_provenance", {})}


def _unready(plans, frozen, client, reason, *, status="not_ready"):
    result = deepcopy(plans)
    for role, plan in result.items():
        plan["damage_optimization"] = {**_base_metadata(frozen, client, role), "status": status,
            "reason": reason, "termination": "not_ready", "neighborhood": "single_pair_exchange", "neighborhood_complete": False}
    return result


def optimize_standard_direct_groups(client, request, scorer, baseline_plans, frozen, *, checkpoint=None,
                                     native_job):
    if checkpoint:
        checkpoint()
    if frozen.reason or not getattr(client, "supports_allocation_group_postprocess", False):
        return _unready(baseline_plans, frozen, client, frozen.reason or "基础完成，多组直伤优化组件未就绪")
    if native_job is None:
        raise NativeAnalysisError("直伤策略必须消费完整 Rust 作业，不再调用旧分阶段计算入口")
    if native_job.response is None:
        return _unready(baseline_plans, frozen, client, frozen.reason or "基础配装尚不完整，保留角色优先结果")
    inner = native_job.inner
    identity = inner["input_identity"]
    try:
        response = native_job.response
        optimization = response["optimization"]
        eligible = {member["role"] for group in optimization["groups"] if group["termination"] != "not_ready"
                    and not postprocess_first_scan_cut(group)
                    and all(member["baseline_value"] is not None and member["candidate_value"] is not None for member in group["members"])
                    for member in group["members"]}
        base_scores = native_job.base_scores
        plans = restore_postprocess_results(request, scorer, baseline_plans, response["plans"], eligible, frozen.reserved_uids,
                                            allow_blueprint_changes=response["version"] == 2, base_scores=base_scores,
                                            native_verified=True)
        # 原子交换的两侧及各自同级组必须一起保存；按已冻结链接闭包，不读取最新配装补齐。
        dependencies = linked_save_dependencies(optimization["groups"], optimization["exchange_links"])
        for group in optimization["groups"]:
            members = deepcopy(group["members"])
            for member in members:
                role = member["role"]
                first_scan_cut = postprocess_first_scan_cut(group)
                completed = role in eligible and not first_scan_cut
                metadata = {**_base_metadata(frozen, client, role), **deepcopy(member), **deepcopy(native_job.facts),
                    "status": "completed" if completed else "budget_limited" if first_scan_cut else "not_ready",
                    "reason": "首个完整单件扫描被预算截断，保留B0；基础方案请另按角色优先重算保存" if first_scan_cut else group["reason"],
                    "input_identity": identity, "neighborhood": optimization["neighborhood"], "group_index": group["group_index"],
                    "group_members": members, "group_objective_value": group["objective_value"],
                    "save_dependencies": sorted(dependencies[role]),
                    "overall_termination": optimization["termination"],
                    **{key: deepcopy(group[key]) for key in ("termination", "evaluations", "rounds", "improvements", "candidate_visits",
                        "single_complete", "pair_complete", "exchange_complete", "blueprint_complete", "quantification_complete", "neighborhood_complete") if key in group},
                    **{key: deepcopy(optimization[key]) for key in ("projection_policy", "budget_policy", "elapsed_millis")
                       if key in optimization}}
                plans[role]["damage_optimization"] = metadata
                if completed:
                    plans[role]["direct_group_preview_source"] = DirectGroupPreviewSource(frozen, allocation_inputs_digest(metadata),
                        allocation_inputs_digest(allocation_save_projection(role, plans[role], frozen.snapshot_id, member["character_id"])))
        if checkpoint:
            checkpoint()
        return plans
    except NativeAnalysisCancelled:
        raise CancelledError from None
    except NativeAnalysisError:
        return _unready(baseline_plans, frozen, client, "基础完成，多组直伤响应验收失败，请核对配套组件", status="failed")


def verify_direct_group_save(dao, rows, *, dataset_id):
    verify_optimization_batch_rows(rows)
    provided = {row["role_name"] for row in rows}
    for row in rows:
        metadata = (row.get("payload") or {}).get("damage_optimization") or {}
        preview = row.get("direct_group_preview_source")
        if (not isinstance(preview, DirectGroupPreviewSource) or metadata.get("status") != "completed"
                or metadata.get("objective") != OBJECTIVE or metadata.get("model_id") != MODEL_ID
                or metadata.get("dataset_id") != dataset_id or metadata.get("source_snapshot_id") != row["snapshot_id"]
                or metadata.get("character_id") != row["character_id"]
                or allocation_inputs_digest(metadata) != preview.metadata_sha256
                or allocation_inputs_digest(allocation_row_save_projection(row)) != preview.save_projection_sha256):
            raise RuntimeError("组直伤目标、装备或基础评分与冻结预览不一致，请重新计算。")
        dependencies = metadata.get("save_dependencies")
        if not isinstance(dependencies, list) or not set(dependencies) <= provided:
            raise RuntimeError("跨角色交换的关联组须一起保存，不能只保存交换的一侧。")
        members = metadata.get("group_members") or ()
        if not {member["role"] for member in members} <= provided:
            raise RuntimeError("同级直伤组须完整保存全部已接纳成员。")
        expected = preview.inputs.owners_sha256
        if allocation_inputs_digest(locked_allocation_owners(dao)) != expected:
            raise RuntimeError("组直伤期间锁定配装已改变，请重新计算。")
