# 管理战报角色修改副本的窄历史 Service 操作。
"""Battle-build edit mutations split from the history analysis facade."""

from __future__ import annotations

from typing import Any

from src.services.advancement_stage_service import select_fork_breakthrough
from src.services.battle_role_page_import_service import (
    BattleRolePageImportService,
)
from src.services.official_role_page_service import load_official_role_detail
from src.services.official_role_profile_service import (
    OfficialRoleProfileService,
    OfficialRoleProfileUpdate,
)
from src.storage.sqlite.user_data_dao import (
    UserDataError,
    UserDataValidationError,
)


class BattleBuildEditHistoryMixin:
    """Expose edit-copy state and role-page import mutations."""

    def sync_role_page_to_build_edit(
        self,
        battle_record_id: int,
        *,
        include_equipment: bool = False,
    ) -> dict[str, Any]:
        self._assert_counterfactual_editable(battle_record_id)
        if include_equipment:
            with self._open_current_dao() as user_dao:
                if not user_dao.battle_report_equipment_editable(battle_record_id):
                    raise UserDataValidationError(
                        "当前战报的固化或假定配装只读，不能从角色页同步空幕/驱动"
                    )
        editor_data = self.load_build_editor_data(
            battle_record_id,
            seed_from_role_page=True,
        )
        return self.save_build_edit(
            battle_record_id,
            BattleRolePageImportService.profiles(
                editor_data,
                include_equipment=include_equipment,
            ),
        )

    def load_build_edit_state(self, battle_record_id: int) -> dict[str, bool]:
        with self._open_current_dao() as user_dao:
            build = user_dao.load_battle_build_snapshot(battle_record_id)
            build_edit = user_dao.load_battle_build_edit(battle_record_id)
            available = user_dao.battle_report_counterfactual_editable(
                battle_record_id
            )
        return {
            "available": build is not None and available,
            "has_edit": build_edit is not None,
            "is_active": bool((build_edit or {}).get("is_active")),
        }

    def save_build_edit(
        self,
        battle_record_id: int,
        profiles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._assert_counterfactual_editable(battle_record_id)
        with self._open_current_dao() as user_dao:
            result = user_dao.save_battle_build_edit(battle_record_id, profiles)
        return {"has_edit": True, "is_active": bool(result["is_active"])}

    def set_build_edit_active(
        self,
        battle_record_id: int,
        active: bool,
    ) -> dict[str, Any]:
        self._assert_counterfactual_editable(battle_record_id)
        with self._open_current_dao() as user_dao:
            result = user_dao.set_battle_build_edit_active(
                battle_record_id,
                active,
            )
        return {"has_edit": True, "is_active": bool(result["is_active"])}

    def sync_build_edit_to_role_page(self, battle_record_id: int) -> int:
        """Copy cultivation fields only; frozen equipment never crosses this boundary."""

        self._assert_counterfactual_editable(battle_record_id)
        with self._open_current_dao() as user_dao:
            build_edit = user_dao.load_battle_build_edit(battle_record_id)
        if build_edit is None:
            raise UserDataError("当前战报还没有可同步的角色修改副本")
        static_path = self._dependencies.static_database_path
        if static_path is None:
            raise UserDataError("当前应用没有可用的官方静态数据库")
        updates = []
        for character in build_edit.get("characters") or ():
            profile = dict(character.get("profile") or {})
            current_detail = load_official_role_detail(
                self._dependencies.user_database_path,
                int(character["character_id"]),
                include_inventory_contexts=False,
                static_database_path=static_path,
            )
            role_page_ordinal = (current_detail.get("profile") or {}).get(
                "ordinal"
            )
            current_ordinal = int(
                character["ordinal"]
                if role_page_ordinal is None
                else role_page_ordinal
            )
            fork_id = character.get("fork_id")
            fork_breakthrough_stage = character.get("fork_breakthrough_stage")
            if fork_id and fork_breakthrough_stage is None:
                fork_template = next(
                    (
                        row
                        for row in current_detail.get("forks") or ()
                        if str(row.get("fork_id") or "") == str(fork_id)
                    ),
                    None,
                )
                resolved = select_fork_breakthrough(
                    (fork_template or {}).get("breakthroughs") or (),
                    int(character.get("fork_level") or 1),
                )
                if resolved is None:
                    raise UserDataError(
                        f"角色 {character['character_id']} 的旧战报弧盘突破阶段无法解析"
                    )
                # 旧副本的 NULL 只在同步边界解析；不回写战报原始或修改副本。
                fork_breakthrough_stage = int(resolved["stage"])
            updates.append(OfficialRoleProfileUpdate(
                character_id=int(character["character_id"]),
                character_level=int(character["character_level"]),
                breakthrough_stage=int(character["breakthrough_stage"]),
                awakening_level=int(character["awakening_level"]),
                selected_awaken_effect_ids=tuple(
                    str(value)
                    for value in profile.get("selected_awaken_effect_ids") or ()
                ),
                likeability_level_10_enabled=bool(
                    character["likeability_level_10_enabled"]
                ),
                fork_id=fork_id,
                fork_level=character.get("fork_level"),
                fork_breakthrough_stage=fork_breakthrough_stage,
                fork_refinement_level=character.get("fork_refinement_level"),
                selected_skill_id=character.get("selected_skill_id"),
                skill_levels={
                    str(key): int(value)
                    for key, value in (profile.get("skill_levels") or {}).items()
                },
                ordinal=current_ordinal,
            ))
        return OfficialRoleProfileService(
            self._dependencies.user_database_path
        ).save_profiles(updates)

    def _assert_counterfactual_editable(self, battle_record_id: int) -> None:
        with self._open_current_dao() as user_dao:
            editable = user_dao.battle_report_counterfactual_editable(
                battle_record_id
            )
        if not editable:
            raise UserDataValidationError("旧版战报仅支持查看，不能修改或重算")
