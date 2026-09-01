# 构建库存查看、筛选和详情页面。
"""MainWindow methods for inventory."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.i18n import tr, display_term
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.optimizer.contracts import (
    DIFF_CHANGED,
    DIFF_ADDED,
    DIFF_REMOVED,
    EQUIP_DISPLAY_NAME,
    EQUIP_SET_NAME,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
)
from src.features.inventory.equipment_display_context import equipment_presentation
from src.features.inventory.equipment_loadout_scoring import (
    score_equipment_display_state,
)
from src.features.inventory.equipment_master_detail_view import (
    update_equipment_role_status,
)
from src.services.game_loadout_projection_service import (
    GameLoadoutImportRequest,
    GameLoadoutProjectionService,
)
from src.utils.logger import logger


__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_set_equipment_mode",
    "_refresh_equip",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "_toggle_role_allocation_lock",
    "_manage_loadout_slot",
    "_import_game_loadout",
    "_import_all_game_loadouts",
    "reset_equipment_account_state",
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
    _request_equipment_graduation_rate,
    _set_equipment_mode,
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
    presentation = equipment_presentation(self)
    build_dialog = getattr(presentation, "plan_diff_dialog", None)
    if callable(build_dialog):
        build_dialog(role_name, diff).exec()
        return
    QMessageBox.information(self, tr("配装变动"), self._saved_plan_diff_text(role_name, diff))


def _clear_all_equipment(self):
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        slot_rows: list[dict[str, Any]] = getattr(
            dao,
            "list_current_loadout_slot_plans",
            lambda: [],
        )()
        plans = [
            (
                str((row.get("plan", {}).get("payload") or {}).get("source_role_name") or "未知角色"),
                str((row.get("slot") or {}).get("slot_name") or "未命名槽位"),
                row.get("plan") or {},
            )
            for row in slot_rows
        ]
        if not plans:
            plans = [
                (role_name, role_name, plan)
                for role_name, plan in dao.list_active_loadout_plans_by_role().items()
            ]
    if not plans:
        QMessageBox.information(self, tr("清空配装"), tr("当前没有已保存的配装。"))
        return
    ret = QMessageBox.question(
        self,
        tr("清空配装"),
        tr("确定要从当前配装页移除所有已保存方案吗？\n方案历史和任务记录会保留，但这些方案不再参与装配。"),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    skipped_locked = []
    with UserDataDao(database_path) as dao:
        for role_name, slot_name, plan in plans:
            if plan.get("allocation_locked"):
                skipped_locked.append(f"{role_name} · {slot_name}")
                continue
            dao.deactivate_loadout_plan(plan["plan_id"])
    self._saved_equipment_cache_valid = False
    self._refresh_equip()
    if skipped_locked:
        QMessageBox.information(
            self,
            tr("清空配装"),
            tr("已清空未锁定方案。以下方案因计算锁定而保留：{names}",
               names=tr("、").join(display_term(n) for n in skipped_locked)),
        )
    logger.success("已清空所有未锁定角色配装")


def invalidate_saved_equipment_cache(self: Any) -> None:
    """Public cross-feature hook after a persisted loadout mutation."""

    self._saved_equipment_cache_valid = False


def reset_equipment_account_state(self: Any) -> None:
    """Discard account-owned projections before the shell refreshes a page."""

    invalidate_saved_equipment_cache(self)
    self._equip_load_token = object()
    self._equipment_graduation_tokens = {}
    self._saved_equipment_states = {}
    self._game_loadout_states = {}
    self._equip_selected_role_by_mode = {}


def refresh_saved_equipment_after_mutation(
    self: Any,
    *,
    restore_role_name: str | None = None,
) -> None:
    """Refresh mutated loadouts while retaining the selected role."""

    if restore_role_name is None:
        mode = getattr(self, "_equipment_mode", "saved")
        selected_by_mode = getattr(self, "_equip_selected_role_by_mode", {})
        if isinstance(selected_by_mode, dict):
            selected = selected_by_mode.get(mode)
            restore_role_name = str(selected) if selected else None
    invalidate_saved_equipment_cache(self)
    self._refresh_equip(restore_role_name=restore_role_name)


def _delete_role_equipment(
    self: Any,
    role_name: str,
    *,
    plan_id: int | None = None,
) -> None:
    database_path = _equipment_paths(self)[0]
    with UserDataDao(database_path) as dao:
        plan = dao.get_loadout_plan(int(plan_id)) if plan_id is not None else dao.get_active_loadout_plan_for_role(role_name)
    if plan is None:
        self._refresh_equip()
        return
    ret = QMessageBox.question(
        self,
        tr("删除角色配装"),
        tr("确定要从当前配装页移除 [{role}] 的已保存方案吗？\n方案历史会保留，但该方案不再参与装配。",
           role=display_term(str(role_name))),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if ret != QMessageBox.Yes:
        return
    try:
        with UserDataDao(database_path) as dao:
            dao.deactivate_loadout_plan(plan["plan_id"])
    except Exception as exc:
        QMessageBox.warning(self, tr("删除角色配装"), str(exc))
        return
    self._saved_equipment_cache_valid = False
    self._refresh_equip()
    logger.success(f"已删除角色配装: {role_name}")


def _manage_loadout_slot(
    self: Any,
    slot_id: int,
    *,
    role_name: str | None = None,
) -> None:
    """Open one character-scoped manager for its visible loadout slots."""

    database_path = _equipment_paths(self)[0]
    try:
        with UserDataDao(database_path) as dao:
            initial_slot = dao.get_loadout_slot(int(slot_id))
        if initial_slot is None or initial_slot.get("is_archived"):
            raise RuntimeError("当前配装槽位已不存在，请刷新页面。")
    except Exception as exc:
        QMessageBox.warning(self, tr("管理配装槽位"), str(exc))
        return

    character_id = int(initial_slot["character_id"])
    character_label = role_name or str(initial_slot["slot_name"])
    if initial_slot.get("slot_key") == "primary" and initial_slot.get("slot_name") == "主力" and role_name:
        try:
            with UserDataDao(database_path) as dao:
                dao.rename_loadout_slot(int(initial_slot["slot_id"]), role_name)
            initial_slot["slot_name"] = role_name
        except Exception as exc:
            QMessageBox.warning(self, tr("管理配装槽位"), str(exc))
            return
    dialog = QDialog(self)
    dialog.setWindowTitle(tr("{role} · 配装槽位管理", role=display_term(character_label)))
    dialog.setFixedWidth(360)
    layout = QVBoxLayout(dialog)
    layout.setSpacing(12)

    selector_row = QHBoxLayout()
    selector_row.addWidget(QLabel(tr("管理槽位：")))
    selector = QComboBox(dialog)
    selector.setMinimumWidth(260)
    selector_row.addWidget(selector, 1)
    layout.addLayout(selector_row)

    plan_status = QLabel(dialog)
    plan_status.setWordWrap(True)
    plan_status.setStyleSheet(
        "QLabel{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px;}"
    )
    layout.addWidget(plan_status)

    actions = QHBoxLayout()
    create_button = QPushButton(tr("新增槽位"), dialog)
    rename_button = QPushButton(tr("重命名"), dialog)
    archive_button = QPushButton(tr("删除槽位"), dialog)
    archive_button.setStyleSheet("QPushButton{color:#ff8b8b}")
    actions.addWidget(create_button)
    actions.addWidget(rename_button)
    actions.addWidget(archive_button)
    layout.addLayout(actions)

    changed = False
    slots: list[dict[str, Any]] = []

    def slot_label(slot: dict[str, Any]) -> str:
        if slot.get("slot_key") == "primary" and str(slot["slot_name"]) == "主力":
            return character_label
        return str(slot["slot_name"])

    def slot_suit_text(slot: dict[str, Any]) -> str:
        slot_state = next(
            (
                state
                for state in (getattr(self, "_saved_equipment_states", {}) or {}).values()
                if isinstance(state, dict)
                and state.get("_loadout_slot_id") == slot["slot_id"]
            ),
            {},
        )
        suit_names = {
            str(drive.get(EQUIP_SET_NAME)).strip()
            for drive in slot_state.get(ROLE_EQUIPPED_DRIVES, ()) or ()
            if isinstance(drive, dict) and str(drive.get(EQUIP_SET_NAME) or "").strip()
        }
        return f"套装：{' / '.join(sorted(suit_names))}" if suit_names else "套装：未装备"

    def selected_slot() -> dict[str, Any] | None:
        selected_id = selector.currentData()
        return next(
            (candidate for candidate in slots if candidate["slot_id"] == selected_id),
            None,
        )

    def update_selected_slot() -> None:
        slot = selected_slot()
        if slot is None:
            plan_status.setText(tr("没有可管理的配装槽位。"))
            rename_button.setEnabled(False)
            archive_button.setEnabled(False)
            return
        plan = slot.get("current_plan") or {}
        if plan:
            if plan.get("allocation_locked"):
                status = "状态：锁定"
            elif bool(((plan.get("payload") or {}).get("last_diff") or {}).get(DIFF_CHANGED)):
                status = "状态：变动"
            else:
                status = "状态：已配装"
        else:
            status = "状态：未配装"
        plan_status.setText(f"{status}\n{slot_suit_text(slot)}")
        rename_button.setEnabled(True)
        archive_button.setEnabled(len(slots) > 1)
        archive_button.setToolTip(tr("至少保留一个配装槽位") if len(slots) <= 1 else "")

    def reload_slots(selected_id: int | None = None) -> None:
        nonlocal slots
        with UserDataDao(database_path) as dao:
            slots = dao.list_loadout_slots(character_id)
        selector.blockSignals(True)
        selector.clear()
        for slot in slots:
            suffix = "（已锁定）" if (slot.get("current_plan") or {}).get("allocation_locked") else ""
            selector.addItem(f"{slot_label(slot)}{suffix}", int(slot["slot_id"]))
        target_id = selected_id if selected_id is not None else int(slot_id)
        target_index = selector.findData(target_id)
        selector.setCurrentIndex(target_index if target_index >= 0 else 0)
        selector.blockSignals(False)
        update_selected_slot()

    def create_slot() -> None:
        nonlocal changed
        name, accepted = QInputDialog.getText(dialog, "新增配装槽位", "槽位名称：")
        if not accepted:
            return
        try:
            with UserDataDao(database_path) as dao:
                new_slot_id = dao.create_loadout_slot(character_id, name)
        except Exception as exc:
            QMessageBox.warning(dialog, tr("新增配装槽位"), str(exc))
            return
        changed = True
        reload_slots(new_slot_id)

    def rename_slot() -> None:
        nonlocal changed
        slot = selected_slot()
        if slot is None:
            return
        name, accepted = QInputDialog.getText(
            dialog,
            "重命名配装槽位",
            "槽位名称：",
            text=slot_label(slot),
        )
        if not accepted:
            return
        try:
            with UserDataDao(database_path) as dao:
                dao.rename_loadout_slot(int(slot["slot_id"]), name)
        except Exception as exc:
            QMessageBox.warning(dialog, tr("重命名配装槽位"), str(exc))
            return
        changed = True
        reload_slots(int(slot["slot_id"]))

    def archive_slot() -> None:
        nonlocal changed
        slot = selected_slot()
        if slot is None or len(slots) <= 1:
            return
        answer = QMessageBox.question(
            dialog,
            tr("删除配装槽位"),
            tr("删除 [{slot}] 后，该槽位当前方案不再参与展示和装配；历史记录保留。是否继续？",
               slot=slot_label(slot)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            with UserDataDao(database_path) as dao:
                dao.archive_loadout_slot(int(slot["slot_id"]))
        except Exception as exc:
            QMessageBox.warning(dialog, tr("删除配装槽位"), str(exc))
            return
        changed = True
        reload_slots()

    selector.currentIndexChanged.connect(lambda _index: update_selected_slot())
    create_button.clicked.connect(create_slot)
    rename_button.clicked.connect(rename_slot)
    archive_button.clicked.connect(archive_slot)
    reload_slots(int(slot_id))
    dialog.exec()
    if changed:
        invalidate_saved_equipment_cache(self)
        self._refresh_equip(restore_role_name=character_label)


def _toggle_role_allocation_lock(
    self: Any,
    role_name: str,
    *,
    plan_id: int | None = None,
    state_key: str | None = None,
) -> bool | None:
    """Persist one current-slot lock change and refresh its navigator badge."""

    database_path = _equipment_paths(self)[0]
    try:
        with UserDataDao(database_path) as dao:
            plan = dao.get_loadout_plan(int(plan_id)) if plan_id is not None else dao.get_active_loadout_plan_for_role(role_name)
            if plan is None:
                raise RuntimeError("未找到该角色的活动配装方案")
            locked = not bool(plan.get("allocation_locked"))
            dao.set_allocation_lock(int(plan["plan_id"]), locked)
    except Exception as exc:
        logger.warning(f"切换配装锁定失败 role={role_name}: {exc}")
        QMessageBox.warning(self, tr("配装锁定"), str(exc))
        return None
    logger.info(
        f"配装锁定已{'开启' if locked else '解除'}: role={role_name}, plan_id={plan['plan_id']}"
    )
    invalidate_saved_equipment_cache(self)
    update_equipment_role_status(
        self,
        state_key or role_name,
        _allocation_locked=locked,
    )
    game_state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    if isinstance(game_state, dict):
        game_state["_game_existing_plan_locked"] = locked
    return locked


def _game_loadout_scores(
    self: Any,
    role_name: str,
    state: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    presentation = equipment_presentation(self)
    return score_equipment_display_state(
        presentation,
        role_name,
        state,
        getattr(self, "roles_db", {}) or {},
    )


def _import_game_loadout(self: Any, role_name: str) -> None:
    state = (getattr(self, "_game_loadout_states", {}) or {}).get(role_name)
    if not isinstance(state, dict):
        QMessageBox.warning(self, tr("导入游戏内方案"), tr("当前展示已失效，请刷新后重试。"))
        return
    projection = state.get("_game_projection")
    if projection is None or not bool(state.get("_game_importable")):
        QMessageBox.warning(
            self,
            tr("导入游戏内方案"),
            str(state.get("_game_reason") or tr("当前游戏内装备不能形成完整方案。")),
        )
        return
    database_path, static_database_path, _ = _equipment_paths(self)
    try:
        with UserDataDao(database_path) as user_dao:
            slots = user_dao.list_loadout_slots(int(projection.character_id))
            if not slots:
                user_dao.create_loadout_slot(
                    int(projection.character_id),
                    "主力",
                    slot_key="primary",
                )
                slots = user_dao.list_loadout_slots(int(projection.character_id))
            labels = [
                f"{role_name if slot['slot_name'] == '主力' else slot['slot_name']}（槽位 #{slot['slot_id']}）" + ("（已锁定）" if (slot.get("current_plan") or {}).get("allocation_locked") else "")
                for slot in slots
            ]
        if len(slots) == 1:
            target_slot = slots[0]
        else:
            label, accepted = QInputDialog.getItem(
                self,
                "导入目标槽位",
                f"将 [{role_name}] 的游戏内配装导入到：",
                labels,
                0,
                False,
            )
            if not accepted:
                return
            target_slot = slots[labels.index(label)]
        if (target_slot.get("current_plan") or {}).get("allocation_locked"):
            QMessageBox.warning(self, tr("导入游戏内方案"), tr("目标配装槽位已锁定，请先解除锁定。"))
            return
    except Exception as exc:
        QMessageBox.warning(self, tr("导入游戏内方案"), str(exc))
        return
    if state.get("_game_existing_plan_id") is not None and not bool(state.get("_game_imported")):
        answer = QMessageBox.question(
            self,
            tr("导入游戏内方案"),
            tr("导入后将替换 [{role}] 当前的计算器配装方案，是否继续？", role=display_term(role_name)),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

    try:
        total_score, assignment_scores = _game_loadout_scores(self, role_name, state)
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            plan_id = GameLoadoutProjectionService(user_dao, static_dao).import_role(
                projection,
                score=total_score,
                assignment_scores=assignment_scores,
                slot_id=int(target_slot["slot_id"]),
            )
    except Exception as exc:
        logger.warning(f"导入游戏内配装失败 role={role_name}: {exc}")
        QMessageBox.warning(self, tr("导入游戏内方案"), str(exc))
        return
    logger.info(f"已导入游戏内配装 role={role_name}, plan_id={plan_id}")
    QMessageBox.information(
        self, tr("导入游戏内方案"),
        tr("[{role}] 已导入为计算器配装方案。", role=display_term(role_name))
    )
    self._saved_equipment_cache_valid = False
    self._refresh_equip(restore_role_name=role_name)


def _import_all_game_loadouts(self: Any) -> None:
    states = getattr(self, "_game_loadout_states", {}) or {}
    eligible = [
        (role_name, state)
        for role_name, state in states.items()
        if isinstance(state, dict)
        and bool(state.get("_game_importable"))
        and not bool(state.get("_game_imported"))
        and not bool(state.get("_game_existing_plan_locked"))
    ]
    locked_count = sum(
        isinstance(state, dict)
        and bool(state.get("_game_importable"))
        and bool(state.get("_game_existing_plan_locked"))
        for state in states.values()
    )
    if not eligible:
        QMessageBox.information(self, tr("一键导入"), tr("当前没有待导入的完整游戏内方案。"))
        return
    prompt = (
        f"将导入 {len(eligible)} 名角色的游戏内方案。"
        "对应角色的现有未锁定方案会被替换，是否继续？"
    )
    if locked_count:
        prompt += f"\n\n另有 {locked_count} 名角色因现有方案已锁定而跳过。"
    answer = QMessageBox.question(
        self,
        tr("一键导入"),
        prompt,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    if answer != QMessageBox.Yes:
        return

    requests = []
    try:
        for role_name, state in eligible:
            total_score, assignment_scores = _game_loadout_scores(
                self,
                role_name,
                state,
            )
            requests.append(GameLoadoutImportRequest(
                projection=state["_game_projection"],
                score=total_score,
                assignment_scores=assignment_scores,
            ))
        database_path, static_database_path, _ = _equipment_paths(self)
        with UserDataDao(database_path) as user_dao, StaticGameDataDao(static_database_path) as static_dao:
            plan_ids = GameLoadoutProjectionService(user_dao, static_dao).import_roles(
                requests,
            )
    except Exception as exc:
        logger.warning(f"一键导入游戏内配装失败: {exc}")
        QMessageBox.warning(self, tr("一键导入"), str(exc))
        return
    logger.info(f"已一键导入游戏内配装 count={len(plan_ids)}")
    message = f"已导入 {len(plan_ids)} 名角色的游戏内方案。"
    if locked_count:
        message += f"\n{locked_count} 名角色因方案已锁定而跳过。"
    QMessageBox.information(self, tr("一键导入"), message)
    self._saved_equipment_cache_valid = False
    self._refresh_equip()


class EquipmentDisplayControllerMixin:
    """Explicit MainWindow surface for saved equipment-plan display."""

    _equipment_compare_signature = _equipment_compare_signature
    _same_equipment_by_ocr = _same_equipment_by_ocr
    _page_equipment = _page_equipment
    _set_equipment_mode = _set_equipment_mode
    _refresh_equip = _refresh_equip
    _request_equipment_graduation_rate = _request_equipment_graduation_rate
    invalidate_saved_equipment_cache = invalidate_saved_equipment_cache
    reset_equipment_account_state = reset_equipment_account_state
    refresh_saved_equipment_after_mutation = (
        refresh_saved_equipment_after_mutation
    )
    _saved_plan_diff_text = _saved_plan_diff_text
    _show_saved_plan_diff_dialog = _show_saved_plan_diff_dialog
    _clear_all_equipment = _clear_all_equipment
    _delete_role_equipment = _delete_role_equipment
    _toggle_role_allocation_lock = _toggle_role_allocation_lock
    _manage_loadout_slot = _manage_loadout_slot
    _import_game_loadout = _import_game_loadout
    _import_all_game_loadouts = _import_all_game_loadouts
    _optimize_saved_equipment = _optimize_saved_equipment
