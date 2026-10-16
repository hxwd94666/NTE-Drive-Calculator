# 在图纸和基础分配前核对全部所选角色；原生证据缺口直接退回配置，不静默删角色或降级策略。
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
import json

from src.integrations.nte_analysis_core import NativeAnalysisCancelled
from src.services.allocation.axis_selection import AxisAllocationSelection
from src.services.equipment.saved_state_loadout_bridge import resolve_character_id_for_allocation_role, SavedStateLoadoutError
from src.storage.sqlite.user_data_dao import UserDataDao


_MESSAGES = {
    "axis_team_attempt_missing": "所选范围的战斗尝试记录缺失或时间不一致，请选择有冻结记录的半场或时段。",
    "axis_team_scope_conflict": "所选范围包含不同队伍，请选择一个队伍稳定的半场或时段。",
    "axis_team_changed": "所选时段内队伍发生变化，请缩小范围或更换战报。",
    "axis_team_events_missing": "所选范围没有可核对的保存事件，请重新选择范围。",
}


def prepare_axis_team_selection(client, selection, selected_roles, *, user_database_path, static_read_unit,
                                semantics_path, source_inventory=None, checkpoint=None):
    """在任何图纸或B0计算前冻结T/S/R及固定队友A的占用；不修改账号旧方案。"""
    def check():
        if checkpoint is not None:
            checkpoint()
    check()
    if not isinstance(selection, AxisAllocationSelection):
        raise ValueError("请先选择轴伤战报和事件范围。")
    if (Path(user_database_path).resolve() != selection.user_database_path
            or static_read_unit.release.database_path.resolve() != selection.static_database_path
            or static_read_unit.release.dataset_id != selection.dataset_id):
        raise CancelledError
    names = tuple(dict.fromkeys(selected_roles))
    if not names:
        raise ValueError("请先选择至少一个需要优化的队内角色。")
    # 旧单角色选择不应留下其他计算成员被B0改装。新界面只使用全部所选角色S。
    if not selection.optimize_groups and names != (selection.selected_role,):
        raise ValueError("轴伤优化范围与计算成员不一致，请重新选择本次全部计算角色，或只保留需要优化的角色。")
    mapped, unknown = [], []
    with UserDataDao(user_database_path, account_id=selection.account_id) as dao, dao.read_consistent_state():
        if str(dao.profile()["account_id"]) != selection.account_id:
            raise CancelledError
        snapshot = dao.current_inventory_snapshot_id()
        if snapshot is None:
            raise ValueError("轴伤优化需要完整稳定库存，请先读取库存。")
        if source_inventory is not None:
            snapshots = {row.get("official", {}).get("snapshot_id") for row in source_inventory}
            if snapshots != {snapshot}:
                raise CancelledError("轴伤库存来源已改变，请重新读取。")
        _, actual = dao.export_inventory_snapshot(snapshot)
        identities = [{key: item[key] for key in ("kind", "item_id", "uid_slot", "uid_serial")} for item in actual]
        for name in names:
            check()
            try:
                cid = resolve_character_id_for_allocation_role(name, static_read_unit.dao, dao, snapshot_id=snapshot)
            except SavedStateLoadoutError:
                cid = None
            if type(cid) is not int or cid <= 0:
                unknown.append(name)
            else:
                mapped.append((name, cid))
    if unknown:
        raise ValueError("这些角色缺少正式身份：" + "、".join(unknown) + "。请移除后再执行轴伤优化。")
    if len({cid for _, cid in mapped}) != len(mapped):
        raise ValueError("多个所选角色对应同一正式身份，请检查角色列表。")
    selector = dict(account_id=selection.account_id, generation=selection.generation,
                    user_database_path=str(selection.user_database_path), static_database_path=str(selection.static_database_path),
                    semantics_path=str(Path(semantics_path).resolve()), battle_record_id=selection.battle.battle_record_id,
                    start_us=selection.battle.start_us, end_us=selection.battle.end_us,
                    detail_scope=selection.battle.detail_scope, detail_level="hit")
    try:
        team = client.resolve_battle_axis_fixed_team(selector, [cid for _, cid in mapped], selection.anchor_mode,
                                                     identities, checkpoint=check)
    except NativeAnalysisCancelled:
        raise CancelledError from None
    check()
    if team["outside_character_ids"]:
        outsiders = set(team["outside_character_ids"])
        names = "、".join(name for name, cid in mapped if cid in outsiders)
        raise ValueError(f"轴伤优先仅优化所选战报队伍。队外角色：{names}。请移除这些角色或更换战报。")
    if not team["ready"]:
        raise ValueError(_MESSAGES.get(team["reason"], "所选战报缺少完整四人队伍证据，请更换具有完整队伍记录的战报；不会按出伤角色猜测队伍。"))
    if not team["fixed_ready"]:
        reasons = {
            "axis_fixed_scope_unverified": "所选半场的首击配置尚未核实，且缺少可复用的冻结兜底资料",
            "axis_fixed_inventory_missing": "固定队友的战报装备不在本次完整库存中",
            "axis_fixed_inventory_mismatch": "固定队友的装备身份与本次库存不一致",
            "axis_fixed_scope_conflict": "所选半场的角色或装备配置不同，请缩小战报范围",
            "axis_fixed_active_edit_missing": "所选活动修改副本已失效",
            "axis_fixed_uid_conflict": "冻结配装中有角色重复使用同一件装备",
            "axis_fixed_layout_missing": "固定队友的驱动坐标未完整保存",
        }
        detail = reasons.get(team["fixed_reason"], "固定队友的养成、装备或半场来源不完整")
        raise ValueError("轴伤优化尚未开始：" + detail + "。请更换具有完整冻结输入的战报或调整选择。")
    proof = {"source_input_digest": team["source_input_digest"], "fixed_build_sha256": team["fixed_build_sha256"],
             "inventory_identities": identities}
    return replace(selection, participating_character_ids=tuple(team["team_character_ids"]),
                   team_source_input_digest=team["source_input_digest"],
                   selected_team_character_ids=tuple(cid for _, cid in mapped),
                   fixed_team_reserved_uids=tuple(team["reserved_uids"]),
                   fixed_team_proof_json=json.dumps(proof, ensure_ascii=False, allow_nan=False),
                   fixed_team_members_json=json.dumps(team["fixed_members"], ensure_ascii=False, allow_nan=False),
                   team_inventory_snapshot_id=snapshot)
