# 冻结真实轴配装后处理请求，复用正式评分和布局门禁，只接纳原生完整目标。
from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

from src.domain.equipment.equipment_normalizer import normalize_equipment_item
from src.integrations.battle_axis_evaluation import MODEL_ID, validate_axis_equipment
from src.integrations.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError
from src.services.allocation.axis_selection import AxisAllocationSelection
from src.services.allocation.inventory import SqliteAllocationInventory, legacy_shape_id, legacy_stat_name
from src.services.allocation.postprocess_facts import (
    allocation_inputs_digest, allocation_plan_wire, allocation_role_state_from_plan, locked_allocation_owners,
    restore_postprocess_results, allocation_save_projection, allocation_row_save_projection, verify_optimization_batch_rows,
    postprocess_first_scan_cut, linked_save_dependencies,
)
from src.services.battle_report.analysis.axis_evaluation import (
    axis_source_checkpoint, freeze_actual_axis_equipment, freeze_axis_source, verify_axis_source,
)
from src.services.battle_report.persistence import BattleReportPersistenceDependencies
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class FrozenAxisAllocationInputs:
    selection: AxisAllocationSelection
    dependencies: BattleReportPersistenceDependencies
    semantics_path: Path
    source: object | None
    snapshot_id: int | None
    inventory_json: str
    role_character_ids: tuple[tuple[str, int], ...]
    reserved_uids: tuple[str, ...]
    owners_sha256: str
    reason: str


@dataclass(frozen=True, slots=True)
class AxisOptimizationPreviewSource:
    inputs: FrozenAxisAllocationInputs
    metadata_sha256: str
    save_projection_sha256: str


def freeze_actual_allocation_inventory(request, dao, static_read_unit, snapshot_id, *, checkpoint=None):
    """把满级评分投影逐件绑定原快照属性身份；实际主词条数值始终来自原值。"""
    if checkpoint is not None:
        checkpoint()
    _, raw = dao.export_inventory_snapshot(snapshot_id)
    actual = freeze_actual_axis_equipment({"items": raw}, checkpoint=checkpoint)
    validate_axis_equipment(actual, complete=False)
    for index, item in enumerate(actual):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        identities = [(row["stat_group"], row["property_id"]) for row in item["stats"]]
        if len(set(identities)) != len(identities):
            raise ValueError("轴库存存在重复冲突的正式属性")
    mapping = {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}": row for row in actual}
    canonical = {row["uid"]: normalize_equipment_item(row) for row in
                 SqliteAllocationInventory(dao, static_read_unit.dao).build(snapshot_id).items}
    result = {}
    for index, item in enumerate(request.inventory):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        source, projected = mapping.get(item.uid), canonical.get(item.uid)
        if source is None or projected is None:
            raise ValueError("本次轴库存缺少真实快照装备")
        if any(allocation_plan_wire(getattr(item, key, None)) != projected.get(key) for key in
               ("item_type", "item_id", "quality", "area", "shape_id", "main_stats", "main_value", "sub_stats")):
            raise ValueError("轴装备评分身份与真实快照形状或属性不一致")
        if source["kind"] == "module":
            if legacy_shape_id(source["geometry"]) != item.shape_id or source["grid_count"] != item.area:
                raise ValueError("轴驱动形状与真实快照不一致")
        else:
            mains = [row for row in source["stats"] if row["stat_group"] == "main"]
            if (len(mains) != 1 or legacy_stat_name(mains[0]["property_id"]) != item.main_stats
                    or source["suit_id"] != item.suit_id):
                raise ValueError("轴卡带主词条或套装身份与真实快照不一致")
        result[item.uid] = source
    if checkpoint is not None:
        checkpoint()
    return result


def freeze_axis_allocation_inputs(client, request, selection, *, user_database_path, static_read_unit,
                                  source_inventory, semantics_path, reserved_uids=(), checkpoint=None):
    if not isinstance(selection, AxisAllocationSelection) or (not selection.optimize_groups and selection.selected_role not in request.role_order):
        raise ValueError("轴策略需要明确选择本账号报告、范围、目标角色与A配置")
    if (Path(user_database_path).resolve() != selection.user_database_path
            or static_read_unit.release.database_path.resolve() != selection.static_database_path
            or static_read_unit.release.dataset_id != selection.dataset_id):
        raise CancelledError
    if checkpoint:
        checkpoint()
    deps = BattleReportPersistenceDependencies(selection.account_id, selection.user_database_path,
                                               selection.generation, selection.static_database_path)
    ids = tuple((role, int(request.roles_db[role]["character_id"])) for role in request.role_order
                if type(request.roles_db[role].get("character_id")) is int and request.roles_db[role]["character_id"] > 0
                and not request.roles_db[role].get("is_custom"))
    if (selection.team_source_input_digest is None
            or tuple(cid for _, cid in ids) != selection.selected_team_character_ids):
        raise ValueError("轴伤计算成员与队伍预检不一致，请重新选择后计算。")
    snapshots = {row["official"].get("snapshot_id") for row in source_inventory if isinstance(row.get("official"), Mapping)}
    snapshot = next(iter(snapshots)) if len(snapshots) == 1 else None
    if snapshot != selection.team_inventory_snapshot_id or not selection.fixed_team_proof_json:
        raise CancelledError("固定队友预留的库存来源已改变，请重新计算。")
    if not set(selection.fixed_team_reserved_uids) <= set(reserved_uids):
        raise ValueError("固定队友装备尚未预留，轴伤计算已停止。")
    reason, inventory, source = "", {}, None
    with UserDataDao(user_database_path, account_id=selection.account_id) as dao, dao.read_consistent_state():
        if str(dao.profile()["account_id"]) != selection.account_id:
            raise CancelledError
        owners = locked_allocation_owners(dao)
        try:
            if type(snapshot) is not int or snapshot != dao.current_inventory_snapshot_id():
                raise ValueError("轴策略需要本次完整稳定库存")
            inventory = freeze_actual_allocation_inventory(request, dao, static_read_unit, snapshot, checkpoint=checkpoint)
        except NativeAnalysisCancelled:
            raise CancelledError from None
        except (ValueError, RuntimeError):
            reason = "基础完成，轴优化未就绪：真实快照的装备身份、形状或属性未闭合"
    reserved = set(reserved_uids)
    reserved.update(f"nte-{kind}-{slot}-{serial}" for _plan_id, _character, kind, slot, serial, _revision in owners)
    if not selection.optimize_groups and dict(ids).get(selection.selected_role) != selection.selected_character_id:
        reason = "基础完成，轴优化未就绪：所选正式角色与本次分配身份不同"
    if not reason:
        try:
            source = freeze_axis_source(client, deps, Path(semantics_path), lambda _: True, selection.battle, checkpoint=checkpoint)
            if source.source_input_digest != selection.team_source_input_digest:
                raise CancelledError("战报队伍来源已改变，请重新选择。")
        except NativeAnalysisCancelled:
            # 用户停止不是模型缺失；不返回可继续进入结果页的未就绪对象。
            raise CancelledError from None
        except NativeAnalysisError:
            reason = "基础完成，轴优化未就绪：配套分析组件或战报输入身份未就绪"
    return FrozenAxisAllocationInputs(selection, deps, Path(semantics_path).resolve(), source, snapshot,
        json.dumps(inventory, ensure_ascii=False, allow_nan=False), ids, tuple(sorted(reserved)),
        allocation_inputs_digest(owners), reason)


def _metadata(frozen, client, *, status, reason="", response=None, input_identity=None):
    response = response or {}
    return {**deepcopy(response.get("optimization") or {}), "objective": MODEL_ID, "model_id": MODEL_ID,
        "selected_role": frozen.selection.selected_role, "character_id": frozen.selection.selected_character_id,
        "status": status, "reason": reason or (response.get("optimization") or {}).get("reason", ""),
        "source_snapshot_id": frozen.snapshot_id, "dataset_id": client.dataset_version,
        "engine_version": client.engine_version, "equipment_value_mode": "actual_level",
        "owners_sha256": frozen.owners_sha256,
        "fixed_team_build_sha256": json.loads(frozen.selection.fixed_team_proof_json)["fixed_build_sha256"] if frozen.selection.fixed_team_proof_json else None,
        "fixed_team_members": json.loads(frozen.selection.fixed_team_members_json or "[]"), "allocation_character_ids": [value for _, value in frozen.role_character_ids],
        "input_identity": input_identity, "source_input_digest": response.get("source_input_digest"),
        "frozen_identity": deepcopy(response.get("frozen_identity")), "scope": deepcopy(response.get("scope")),
        "battle_record_id": frozen.selection.battle.battle_record_id, "anchor_mode": frozen.selection.anchor_mode,
        "critical_policy": response.get("critical_policy"), "neighborhood": response.get("neighborhood"),
        **getattr(client, "component_provenance", {})}


def _keep_baseline(plans, frozen, client, reason, *, status="not_ready", identity=None, response=None):
    result = deepcopy(plans)
    for role in result:
        result[role]["damage_optimization"] = _metadata(frozen, client,
            status=status if role == frozen.selection.selected_role or frozen.selection.optimize_groups else "baseline_only", reason=reason,
            response=response if role == frozen.selection.selected_role or frozen.selection.optimize_groups else None, input_identity=identity)
    return result


def optimize_frozen_axis(client, request, scorer, baseline_plans, frozen, *, checkpoint=None, native_job):
    if not frozen.selection.optimize_groups:
        raise NativeAnalysisError("轴伤优化必须使用完整队伍目标的统一作业")
    if frozen.reason or frozen.source is None or not getattr(client, "supports_battle_axis_group_postprocess", False):
        return _keep_baseline(baseline_plans, frozen, client, frozen.reason or "基础完成，轴优化组件未就绪")
    def check():
        axis_source_checkpoint(client, frozen.dependencies, frozen.semantics_path, lambda _: True,
                               frozen.source, checkpoint=checkpoint)
    check()
    if native_job is None:
        raise NativeAnalysisError("轴伤策略必须消费完整 Rust 作业，不再调用旧分阶段计算入口")
    if native_job.response is None:
        return _keep_baseline(baseline_plans, frozen, client, frozen.reason or "基础配装尚不完整，保留角色优先结果")
    inner = native_job.inner
    identity = inner["input_identity"]
    try:
        verify_axis_source(client, frozen.dependencies, frozen.semantics_path, lambda _: True, frozen.source, checkpoint=checkpoint)
        response = native_job.response
        if response["source_input_digest"] is not None and response["source_input_digest"] != frozen.source.source_input_digest:
            raise CancelledError
        verify_axis_source(client, frozen.dependencies, frozen.semantics_path, lambda _: True, frozen.source, checkpoint=checkpoint)
        optimization = response["optimization"]
        groups = optimization["groups"]
        eligible = {member["role"] for group in groups if group["termination"] != "not_ready"
                    and not postprocess_first_scan_cut(group, improvements=optimization["improvements"])
                    for member in group["members"]}
        base_scores = native_job.base_scores
        plans = restore_postprocess_results(request, scorer, baseline_plans, response["plans"], eligible,
                                             frozen.reserved_uids,
                                             allow_blueprint_changes=response["version"] == 2, base_scores=base_scores,
                                             native_verified=True)
        # 共享目标依赖完整Bk；未保存成员仍是冻结B0假定，不能事后以当前槽位补齐。
        shared_builds = {"source_snapshot_id": frozen.snapshot_id, "input_identity": identity,
            "source_input_digest": frozen.source.source_input_digest,
            "roles": [{"role": role, "character_id": character_id,
                "basis": "accepted" if role in eligible else "baseline_assumption",
                "role_state": allocation_role_state_from_plan(plans[role])}
                for role, character_id in frozen.role_character_ids
                if character_id in frozen.selection.participating_character_ids]}
        result = _keep_baseline(plans, frozen, client, "非优化成员仅完成基础配装", identity=identity, response=response)
        save_dependencies = linked_save_dependencies(groups, optimization["exchange_links"])
        for group in groups:
            for member in group["members"]:
                role = member["role"]
                first_scan_cut = postprocess_first_scan_cut(group, improvements=optimization["improvements"])
                completed = role in eligible and not first_scan_cut
                metadata = _metadata(frozen, client, status="completed" if completed else "budget_limited" if first_scan_cut else "not_ready",
                                     response=response, input_identity=identity)
                metadata.update(deepcopy(native_job.facts))
                metadata.update(selected_role=role, character_id=member["character_id"], reason="首个完整单件扫描被预算截断，保留B0；基础方案请另按角色优先重算保存" if first_scan_cut else group["reason"])
                metadata.update(save_dependencies=save_dependencies[role], shared_build_dependencies=deepcopy(shared_builds),
                    shared_build_dependencies_sha256=allocation_inputs_digest(shared_builds),
                    group_index=group["group_index"], group_members=deepcopy(group["members"]),
                    overall_termination=optimization["termination"],
                    **{key: deepcopy(group[key]) for key in ("termination", "evaluations", "rounds", "improvements",
                        "single_complete", "pair_complete", "exchange_complete", "blueprint_complete", "enumeration_complete", "quantification_complete", "neighborhood_complete") if key in group})
                result[role]["damage_optimization"] = metadata
                if completed:
                    result[role]["axis_preview_source"] = AxisOptimizationPreviewSource(frozen, allocation_inputs_digest(metadata),
                        allocation_inputs_digest(allocation_save_projection(role, result[role], frozen.snapshot_id, member["character_id"])))
        if not groups:
            return _keep_baseline(baseline_plans, frozen, client, optimization["reason"], identity=identity, response=response)
        check()
        return result
    except NativeAnalysisCancelled:
        raise CancelledError from None
    except NativeAnalysisError:
        return _keep_baseline(baseline_plans, frozen, client, "基础完成，轴优化响应验收失败，请核对配套组件", status="failed", identity=identity)


def verify_axis_postprocess_save(dao, rows, *, dataset_id, client, checkpoint=None):
    verify_optimization_batch_rows(rows, shared_axis=True)
    sources = {}
    provided = {row["role_name"] for row in rows}
    for row in rows:
        metadata = (row.get("payload") or {}).get("damage_optimization") or {}
        preview = row.get("axis_preview_source")
        if not isinstance(preview, AxisOptimizationPreviewSource) or preview.metadata_sha256 != allocation_inputs_digest(metadata):
            raise RuntimeError("轴优化元数据与冻结预览不一致，请重新计算。")
        projection = allocation_row_save_projection(row)
        if allocation_inputs_digest(projection) != preview.save_projection_sha256:
            raise RuntimeError("保存装备或基础评分与已接纳的轴优化预览不一致，请重新计算。")
        frozen = preview.inputs
        if frozen.selection.optimize_groups:
            linked = metadata.get("save_dependencies")
            if not isinstance(linked, list) or not set(linked) <= provided:
                raise RuntimeError("跨角色交换的关联组须一起保存，不能只保存交换的一侧。")
            dependencies = metadata.get("shared_build_dependencies")
            expected = {(role, character_id) for role, character_id in frozen.role_character_ids
                        if character_id in frozen.selection.participating_character_ids}
            builds = dependencies.get("roles", []) if isinstance(dependencies, Mapping) else []
            if (not isinstance(dependencies, Mapping) or not isinstance(builds, list)
                    or any(not isinstance(build, Mapping) for build in builds)
                    or len(builds) != len(expected)
                    or {(build.get("role"), build.get("character_id")) for build in builds} != expected
                    or dependencies.get("source_snapshot_id") != frozen.snapshot_id
                    or dependencies.get("input_identity") != metadata.get("input_identity")
                    or frozen.source is None or dependencies.get("source_input_digest") != frozen.source.source_input_digest
                    or metadata.get("shared_build_dependencies_sha256") != allocation_inputs_digest(dependencies)
                    or any(build.get("basis") not in {"accepted", "baseline_assumption"}
                           or not isinstance(build.get("role_state"), Mapping) for build in builds)
                    or not any(build["role"] == row["role_name"] and build["basis"] == "accepted"
                               and build["role_state"] == row.get("role_state") for build in builds)):
                raise RuntimeError("共享轴目标缺少完整冻结构筑依赖或依赖已改变，请重新计算。")
        if (not frozen.selection.fixed_team_proof_json
                or metadata.get("fixed_team_build_sha256") != json.loads(frozen.selection.fixed_team_proof_json)["fixed_build_sha256"]
                or metadata.get("fixed_team_members") != json.loads(frozen.selection.fixed_team_members_json or "[]")):
            raise RuntimeError("固定队友构筑依赖与冻结预览不一致，请重新计算。")
        if (frozen.source is None
                or metadata.get("objective") != MODEL_ID or metadata.get("model_id") != MODEL_ID
                or metadata.get("status") != "completed" or metadata.get("equipment_value_mode") != "actual_level"
                or metadata.get("source_snapshot_id") != row.get("snapshot_id") or metadata.get("dataset_id") != dataset_id
                or metadata.get("character_id") != row.get("character_id")
                or metadata.get("character_id") != dict(frozen.role_character_ids).get(row["role_name"])
                or metadata.get("source_input_digest") != frozen.source.source_input_digest
                or Path(dao.database_path).resolve() != frozen.selection.user_database_path):
            raise RuntimeError("轴优化与冻结预览身份不一致，请重新计算。")
        if (client.dataset_version != dataset_id or client.engine_version != metadata.get("engine_version")
                or any(metadata.get(key) != value for key, value in client.component_provenance.items())):
            raise RuntimeError("轴优化组件来源已改变，请重新计算。")
        if (metadata.get("owners_sha256") != frozen.owners_sha256
                or allocation_inputs_digest(locked_allocation_owners(dao))
                   != frozen.owners_sha256):
            raise RuntimeError("轴优化期间锁定配装已改变，请重新计算。")
        if "group_index" in metadata and not {member["role"] for member in metadata.get("group_members") or ()} <= provided:
            raise RuntimeError("同轴共享目标的已接纳组须完整保存全部成员。")
        sources[frozen.source.source_input_digest] = frozen
    for frozen in sources.values():
        verify_axis_source(client, frozen.dependencies, frozen.semantics_path, lambda _: True, frozen.source, checkpoint=checkpoint)
