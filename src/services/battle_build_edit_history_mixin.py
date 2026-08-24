# 管理战报角色修改副本的窄历史 Service 操作。
"""Battle-build edit mutations split from the history analysis facade."""

from __future__ import annotations

from typing import Any

from src.services.battle_role_page_import_service import (
    BattleRolePageImportService,
)


class BattleBuildEditHistoryMixin:
    """Expose edit-copy state and role-page import mutations."""

    def sync_role_page_to_build_edit(
        self,
        battle_record_id: int,
        *,
        include_equipment: bool = False,
    ) -> dict[str, Any]:
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
        return {
            "available": build is not None,
            "has_edit": build_edit is not None,
            "is_active": bool((build_edit or {}).get("is_active")),
        }

    def save_build_edit(
        self,
        battle_record_id: int,
        profiles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._open_current_dao() as user_dao:
            result = user_dao.save_battle_build_edit(battle_record_id, profiles)
        return {"has_edit": True, "is_active": bool(result["is_active"])}

    def set_build_edit_active(
        self,
        battle_record_id: int,
        active: bool,
    ) -> dict[str, Any]:
        with self._open_current_dao() as user_dao:
            result = user_dao.set_battle_build_edit_active(
                battle_record_id,
                active,
            )
        return {"has_edit": True, "is_active": bool(result["is_active"])}
