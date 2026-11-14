# 冻结轴优化的明确报告、范围、参与角色与A配置选择，元数据查询不执行分析或写入事实。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path

from src.domain.battle_report.history.records import BattleReportHistoryEntry
from src.domain.catalog.static_catalog import StaticCatalogRelease
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader, static_catalog_read_unit
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest
from src.services.battle_report.history.access import list_history_entries
from src.services.equipment.loadouts.saved_state_loadout_bridge import (
    resolve_character_id_for_allocation_role,
    SavedStateLoadoutError,
    character_ids_for_static_role,
    custom_character_id_for_role,
)
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class AxisAllocationSelection:
    account_id: str
    generation: int
    user_database_path: Path
    static_database_path: Path
    dataset_id: str
    selected_role: str | None
    selected_character_id: int | None
    battle: BattleReportAnalysisLoadRequest
    anchor_mode: str
    participating_character_ids: tuple[int, ...]
    optimize_groups: bool = False
    team_source_input_digest: str | None = None
    selected_team_character_ids: tuple[int, ...] = ()
    fixed_team_reserved_uids: tuple[str, ...] = ()
    fixed_team_proof_json: str | None = None
    fixed_team_members_json: str | None = None
    team_inventory_snapshot_id: int | None = None
    rank_baseline: bool = False
    damage_priority_groups: tuple[tuple[int, ...], ...] = ()
    damage_priority_reason: str | None = None
    search_mode: str = "fast"
    expected_source_input_digest: str | None = None


@dataclass(frozen=True, slots=True)
class AxisAllocationChoices:
    entries: tuple[BattleReportHistoryEntry, ...]
    role_character_ids: tuple[tuple[str, int], ...]
    active_edit_record_ids: frozenset[int]
    dataset_id: str
    selected_roles: tuple[str, ...]
    static_release: StaticCatalogRelease | None = None


class AxisAllocationSelectionService:
    def __init__(self, *, dependencies, context_is_current):
        self.dependencies = dependencies
        self._context_is_current = context_is_current

    def checkpoint(self):
        if not self._context_is_current(self.dependencies):
            raise CancelledError

    def choices(self, selected_roles) -> AxisAllocationChoices:
        self.checkpoint()
        requested_roles = tuple(dict.fromkeys(selected_roles))
        dependencies = self.dependencies
        if dependencies.static_database_path is None:
            raise ValueError("轴优化缺少静态资料身份")
        with static_catalog_read_unit(dependencies.static_database_path) as unit, \
                UserDataDao(dependencies.user_database_path, account_id=dependencies.account_id) as dao, dao.read_consistent_state():
            if str(dao.profile()["account_id"]) != dependencies.account_id:
                raise CancelledError
            entries = list_history_entries(user_dao=dao, static_dataset_id=unit.release.dataset_id,
                                            static_schema_version=unit.release.schema_version)
            snapshot = dao.current_inventory_snapshot_id()
            roles = []
            # 冷启动时计算卡片尚未填充；候选身份可来自正式目录，但不据此自动选择计算成员。
            names = requested_roles or tuple(dict.fromkeys(
                [str(row["name_zh"]) for row in unit.dao.list_role_template_characters()]
                + [str(row["name_zh"]) for row in dao.list_custom_characters()]))
            for role in names:
                self.checkpoint()
                try:
                    if snapshot is not None:
                        character_id = resolve_character_id_for_allocation_role(role, unit.dao, dao, snapshot_id=snapshot)
                    else:
                        # 历史发现不要求库存；歧义角色留待正式计算时由稳定快照解析。
                        character_id = custom_character_id_for_role(role, dao)
                        if character_id is None:
                            ids = character_ids_for_static_role(role, unit.dao)
                            character_id = ids[0] if len(ids) == 1 else None
                except SavedStateLoadoutError:
                    character_id = None
                if character_id is not None:
                    roles.append((role, int(character_id)))
            active = frozenset(entry.battle_record_id for entry in entries
                if (dao.load_battle_build_edit(entry.battle_record_id) or {}).get("is_active") is True)
            dataset_id = unit.release.dataset_id
        self.checkpoint()
        return AxisAllocationChoices(entries, tuple(roles), active, dataset_id, requested_roles, unit.release)

    def select(self, choices: AxisAllocationChoices, *, battle_record_id, selected_role, detail_scope,
               anchor_mode, start_us=None, end_us=None) -> AxisAllocationSelection:
        self.checkpoint()
        if choices.static_release is not None:
            StaticCatalogReleaseReader().ensure_unchanged(choices.static_release)
        dependencies = self.dependencies
        entry = next((row for row in choices.entries if row.battle_record_id == battle_record_id), None)
        character_id = dict(choices.role_character_ids).get(selected_role)
        joint = selected_role == "__all_groups__"
        if entry is None or (not joint and (character_id is None or character_id not in entry.character_ids)) or joint and not choices.selected_roles:
            raise ValueError("请选择本账号战报中有正式身份的参与角色")
        if anchor_mode not in {"original", "effective"}:
            raise ValueError("请明确选择原始A或已启用修改副本A")
        if anchor_mode == "effective" and battle_record_id not in choices.active_edit_record_ids:
            raise ValueError("该战报没有已启用修改副本，请明确选择原始A")
        if detail_scope not in {"all", "first", "second", "range"}:
            raise ValueError("请明确选择保存事件范围")
        if (detail_scope == "first" and not entry.has_first_half) or (detail_scope == "second" and not entry.has_second_half):
            raise ValueError("该战报没有所选半场的冻结事件")
        if detail_scope == "range":
            if type(start_us) is not int or type(end_us) is not int or not 0 <= start_us < end_us:
                raise ValueError("请输入有效的分析开始和结束时刻")
        elif start_us is not None or end_us is not None:
            raise ValueError("非指定时段不能携带另一份时间范围")
        request = BattleReportAnalysisLoadRequest(battle_record_id, start_us=start_us, end_us=end_us,
            detail_scope=detail_scope if detail_scope in {"first", "second"} else None, detail_level="hit",
            selected_character_id=character_id, static_database_path=Path(dependencies.static_database_path).resolve())
        return AxisAllocationSelection(dependencies.account_id, dependencies.generation,
            Path(dependencies.user_database_path).resolve(), Path(dependencies.static_database_path).resolve(),
            choices.dataset_id, None if joint else selected_role, character_id, request, anchor_mode, tuple(entry.character_ids), joint)
