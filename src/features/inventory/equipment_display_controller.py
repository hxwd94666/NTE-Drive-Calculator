# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QMessageBox,
)

from src.storage.sqlite.user_data_dao import UserDataDao
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_UID,
)
from src.utils.logger import logger


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_refresh_equip",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "_toggle_role_allocation_lock",
]

EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

_OFFICIAL_STAT_LABELS = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_OFFICIAL_SHAPE_LABELS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}


from src.features.inventory.equipment_display_view import (
    _equipment_paths,
    _equipment_compare_signature,
    _same_equipment_by_ocr,
    _page_equipment,
    _refresh_equip,
)


from src.features.inventory.equipment_plan_optimizer import (
    _optimize_saved_equipment,
)




def _saved_plan_diff_text(self, role_name, diff):
    removed = diff.get(DIFF_REMOVED, []) or []
    added = diff.get(DIFF_ADDED, []) or []
    if not removed and not added:
        return "本次保存与上一套方案没有装备变动。"
    lines = [f"{role_name} 配装变动："]
    if removed:
        lines.append("\n卸下：")
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n换上：")
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)


def _show_saved_plan_diff_dialog(self, role_name, diff):
    if hasattr(self, "_build_plan_diff_dialog"):
        self._build_plan_diff_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self, "配装变动", self._saved_plan_diff_text(role_name, diff))


def _clear_all_equipment(self):
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        plans = dao.list_active_loadout_plans_by_role()
    if not plans:
        QMessageBox.information(self, "清空配装", "当前没有已保存的配装。")
        return
    ret = QMessageBox.question(
        self,
        "清空配装",
        "确定要从当前配装页移除所有已保存方案吗？\n方案历史和任务记录会保留，但这些方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    skipped_locked = []
    with UserDataDao(database_path) as dao:
        for role_name, plan in plans.items():
            if plan.get("allocation_locked"):
                skipped_locked.append(role_name)
                continue
            dao.deactivate_loadout_plan(plan["plan_id"])
    self._refresh_equip()
    if skipped_locked:
        QMessageBox.information(
            self,
            "清空配装",
            "已清空未锁定方案。以下方案因计算锁定而保留：" + "、".join(skipped_locked),
        )
    logger.success("已清空所有未锁定角色配装")


def _delete_role_equipment(self: Any, role_name: str) -> None:
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        plan = dao.get_active_loadout_plan_for_role(role_name)
    if plan is None:
        self._refresh_equip()
        return
    ret = QMessageBox.question(
        self,
        "删除角色配装",
        f"确定要从当前配装页移除 [{role_name}] 的已保存方案吗？\n方案历史会保留，但该方案不再参与装配。",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    try:
        with UserDataDao(database_path) as dao:
            dao.deactivate_loadout_plan(plan["plan_id"])
    except Exception as exc:
        QMessageBox.warning(self, "删除角色配装", str(exc))
        return
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


def _toggle_role_allocation_lock(self: Any, role_name: str) -> bool | None:
    """Persist one lock change and return its new state without rebuilding UI."""

    database_path = _equipment_paths(self)[0]
    try:
        with UserDataDao(database_path) as dao:
            plan = dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError("未找到该角色的活动配装方案")
            locked = not bool(plan.get("allocation_locked"))
            dao.set_allocation_lock(int(plan["plan_id"]), locked)
    except Exception as exc:
        logger.warning(f"切换配装锁定失败 role={role_name}: {exc}")
        QMessageBox.warning(self, "配装锁定", str(exc))
        return None
    logger.info(
        f"配装锁定已{'开启' if locked else '解除'}: role={role_name}, plan_id={plan['plan_id']}"
    )
    return locked


class EquipmentDisplayControllerMixin:
    """Explicit MainWindow surface for saved equipment-plan display."""

    _equipment_compare_signature = _equipment_compare_signature
    _same_equipment_by_ocr = _same_equipment_by_ocr
    _page_equipment = _page_equipment
    _refresh_equip = _refresh_equip
    _saved_plan_diff_text = _saved_plan_diff_text
    _show_saved_plan_diff_dialog = _show_saved_plan_diff_dialog
    _clear_all_equipment = _clear_all_equipment
    _delete_role_equipment = _delete_role_equipment
    _toggle_role_allocation_lock = _toggle_role_allocation_lock
    _optimize_saved_equipment = _optimize_saved_equipment
