# 在单次页面请求内复用冻结战报事实，候选始终领取独立的可变副本。
"""Request-owned source facts shared by baseline and candidate analysis."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.services.battle_report.build.profile_normalization import (
    normalize_inferred_battle_build,
)
from src.services.battle_report.inputs.formal_tags import BattleFormalDamageTagService
from src.services.battle_report.build.equipment import apply_import_equipment_locks
from src.services.battle_report.history.access import StaleBattleReportContextError

if TYPE_CHECKING:
    from src.services.battle_report.history.service import BattleReportHistoryService


def _static_identity(path: Path | None) -> tuple[int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisInputs:
    """单次离线差分持有原始事实，候选只领取副本，不把派生编辑写回原件。"""

    battle_record_id: int
    _owner: object = field(repr=False, compare=False)
    _static_identity: tuple[int, int] | None = field(repr=False)
    _values: tuple[dict[str, Any] | None, ...] = field(repr=False, compare=False)
    _role_details: dict[int, dict[str, Any]] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def load(
        cls, history: BattleReportHistoryService, battle_record_id: int,
    ) -> BattleReportAnalysisInputs:
        static_path = history._dependencies.static_database_path
        static_identity = _static_identity(static_path)
        with history._open_current_dao() as dao:
            record = dao.load_battle_record(battle_record_id)
            if record is None:
                values = (None,) * 5
            else:
                evidence = dao.load_battle_axis_evidence(battle_record_id)
                build = normalize_inferred_battle_build(
                    dao.load_battle_build_snapshot(battle_record_id)
                )
                build_edit = dao.load_battle_build_edit(battle_record_id)
                locks = dao.load_battle_import_equipment_locks(battle_record_id)
                target = dao.load_battle_target_condition(battle_record_id)
                values = (record, evidence, build, build_edit, target)
        if record is not None:
            target = history._complete_target_condition(target, evidence)
            apply_import_equipment_locks(build, locks)
            history._localize_axis_evidence(evidence)
            BattleFormalDamageTagService.project(evidence, static_path)
            values = (record, evidence, build, build_edit, target)
        result = cls(battle_record_id, history, static_identity, deepcopy(values))
        result._validate(history, battle_record_id)
        return result

    def _validate(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> None:
        if self._owner is not history or self.battle_record_id != battle_record_id:
            raise ValueError("冻结分析输入不属于当前服务或战报")
        if not history._context_is_current(history._dependencies):
            raise StaleBattleReportContextError("战报账号上下文已经变化")
        if _static_identity(history._dependencies.static_database_path) != self._static_identity:
            raise StaleBattleReportContextError("冻结分析使用的静态数据已经变化")

    def copy_for(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> tuple[dict[str, Any] | None, ...]:
        self._validate(history, battle_record_id)
        return deepcopy(self._values)

    def role_detail_cache_for(
        self, history: BattleReportHistoryService, battle_record_id: int,
    ) -> dict[int, dict[str, Any]]:
        """角色资源缓存仅属于本请求；后续投影复制值，不跨账号或战报借用。"""
        self._validate(history, battle_record_id)
        return self._role_details
