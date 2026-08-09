# 构建配装展示状态并编排已保存方案的单件替换优化。
"""MainWindow methods for inventory."""

from __future__ import annotations


from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.domain.allocation_rating import allocation_grade
from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.app.theme import themed_style
from src.features.inventory.warehouse import warehouse_item_view
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
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


from src.features.inventory.equipment_display_context import (
    equipment_presentation,
    equipment_paths as _equipment_paths,
)

def _official_stat_values(stats):
    values = {}
    for stat in stats or []:
        property_id = str(stat.get("property_id") or "")
        label = _OFFICIAL_STAT_LABELS.get(property_id, property_id or "未知属性")
        value = float(stat.get("value", 0.0) or 0.0)
        if stat.get("percent"):
            value *= 100.0
        values[label] = round(value, 6)
    return values


def _display_shape_id(geometry):
    value = str(geometry or "").removeprefix("EquipmentGeometry_").casefold()
    return _OFFICIAL_SHAPE_LABELS.get(value, str(geometry or "未知形状"))


def _sqlite_plan_display_state(
    plan,
    user_dao,
    static_dao,
    *,
    inventory_by_snapshot=None,
    shape_cells=None,
    suit_names=None,
    attribute_ids=None,
):
    """将活动 SQLite 方案转换为配装页展示模型；不读取旧 JSON。"""
    snapshot_id = int(plan["source_snapshot_id"])
    if inventory_by_snapshot is None:
        items = {(row["uid_serial"], row["uid_slot"]): row for row in user_dao.list_inventory_items(snapshot_id)}
    else:
        items = inventory_by_snapshot.get(snapshot_id, {})
    if shape_cells is None:
        shape_cells = {shape["shape_id"]: shape.get("cells") or [] for shape in static_dao.list_shapes()}
    if suit_names is None:
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
    if attribute_ids is None:
        attribute_ids = {str(attribute["attribute_id"]) for attribute in static_dao.list_equipment_attributes()}
    payload = plan.get("payload") or {}
    last_diff = dict(payload.get("last_diff") or {})
    added_uids = {str(uid) for uid in (last_diff.get(DIFF_ADDED_UIDS) or ()) if uid}
    changed_uids = {str(uid) for uid in (payload.get("changed_uids") or ()) if uid}
    board = [["0" for _ in range(5)] for _ in range(5)]
    drives = []
    tape = None
    for assignment in plan["assignments"]:
        raw = assignment.get("raw_assignment") or {}
        item = (
            virtual_equipment_inventory_item(raw)
            if is_virtual_equipment_assignment(raw)
            else items.get((assignment["uid_serial"], assignment["uid_slot"]))
        )
        if item is None:
            message = (
                f"方案 #{plan.get('plan_id')} 的装备 UID "
                f"({assignment.get('uid_slot')}, {assignment.get('uid_serial')}) "
                f"不在来源快照 {snapshot_id} 中"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        unknown_properties = [
            str(stat.get("property_id") or "")
            for stat in (*item.get("main_stats", ()), *item.get("sub_stats", ()))
            if str(stat.get("property_id") or "") not in attribute_ids
        ]
        if unknown_properties:
            message = (
                f"方案 #{plan.get('plan_id')} 的来源快照包含当前静态数据未定义的属性 ID："
                f"{', '.join(sorted(set(unknown_properties)))}"
            )
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        uid_prefix = "module" if item["kind"] == "module" else "core"
        uid = f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}"
        item_icon_path = warehouse_item_view(item).get("item_icon_path")
        if item["kind"] == "core":
            suit_id = str(item.get("suit_id") or "")
            if suit_id not in suit_names:
                message = f"方案 #{plan.get('plan_id')} 的核心套装 {suit_id or '<empty>'} 不在当前静态数据中"
                logger.error("已保存方案兼容性错误：{}", message)
                raise RuntimeError(message)
            main_stats = _official_stat_values(item.get("main_stats"))
            tape = {
                EQUIP_UID: uid,
                EQUIP_SET_NAME: suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "未知套装")),
                EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"),
                EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
                    str(item.get("quality")).casefold(), "Gold"
                ),
                "discarded": bool(item.get("discarded")),
                "item_icon_path": item_icon_path,
                "virtual": bool(item.get("virtual")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
            continue
        geometry = item.get("geometry")
        shape_id = _display_shape_id(geometry)
        official_shape = "EquipmentGeometry_" + str(geometry or "").removeprefix("EquipmentGeometry_")
        if official_shape not in shape_cells:
            message = f"方案 #{plan.get('plan_id')} 的驱动形状 {geometry or '<empty>'} 不在当前静态数据中"
            logger.error("已保存方案兼容性错误：{}", message)
            raise RuntimeError(message)
        drives.append(
            {
                EQUIP_UID: uid,
                EQUIP_SHAPE_ID: shape_id,
                EQUIP_SUB_STATS: _official_stat_values(item.get("sub_stats")),
                EQUIP_QUALITY: {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
                    str(item.get("quality")).casefold(), "Gold"
                ),
                "discarded": bool(item.get("discarded")),
                # Snapshot ingestion derives duplicate state for modules.  Keep it
                # on the saved-plan view model so the card can show it alongside
                # discard/new/change state.
                "is_duplicate_drive": bool(item.get("is_duplicate_drive")),
                "duplicate_group_id": item.get("duplicate_group_id"),
                "duplicate_index": item.get("duplicate_index"),
                "duplicate_count": item.get("duplicate_count"),
                "item_icon_path": item_icon_path,
                "virtual": bool(item.get("virtual")),
                EQUIP_IS_CHANGED: uid in changed_uids,
                EQUIP_IS_NEW: uid in added_uids and uid not in changed_uids,
            }
        )
        row, column = assignment.get("target_row"), assignment.get("target_column")
        for cell in shape_cells.get(official_shape, []):
            target_row = int(row) + int(cell["x"]) - 1
            target_column = int(column) + int(cell["y"]) - 1
            if 0 <= target_row < 5 and 0 <= target_column < 5:
                board[target_row][target_column] = shape_id
    return {
        ROLE_BLUEPRINT_LAYOUT: board,
        ROLE_EQUIPPED_TAPE: tape,
        ROLE_EQUIPPED_DRIVES: drives,
        ROLE_TOTAL_SCORE: float(plan.get("score") or 0.0),
        ROLE_TOTAL_GRADE: "",
        "strategy_mode": payload.get("strategy", ""),
        "_sqlite_plan_id": plan["plan_id"],
        "_sqlite_source_snapshot_id": snapshot_id,
        "_sqlite_assignment_scores_complete": exact_assignment_score_total(
            plan["assignments"],
            payload.get("assignment_scores") or {},
        ) is not None,
        "_allocation_locked": bool(plan.get("allocation_locked")),
        ROLE_LAST_DIFF: last_diff,
    }


def _sqlite_inventory_item_display(row, suit_names):
    """Project one official snapshot item for the saved-plan replacement dialog."""
    kind = str(row.get("kind") or "")
    quality = {"orange": "Gold", "purple": "Purple", "blue": "Blue"}.get(
        str(row.get("quality") or "").casefold(), "Gold"
    )
    uid = f"nte-{'module' if kind == 'module' else 'core'}-{row.get('uid_slot')}-{row.get('uid_serial')}"
    if kind == "core":
        main_stats = _official_stat_values(row.get("main_stats"))
        return {
            EQUIP_UID: uid,
            EQUIP_SET_NAME: suit_names.get(str(row.get("suit_id") or ""), str(row.get("suit_id") or "未知套装")),
            EQUIP_MAIN_STATS: next(iter(main_stats), "未知主词条"),
            EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
            EQUIP_QUALITY: quality,
            "_role_main_stats": main_stats,
            "_item_id": str(row.get("item_id") or ""),
            "_uid_serial": int(row.get("uid_serial") or 0),
            "_uid_slot": int(row.get("uid_slot") or 0),
        }
    return {
        EQUIP_UID: uid,
        EQUIP_SHAPE_ID: _display_shape_id(row.get("geometry")),
        EQUIP_SUB_STATS: _official_stat_values(row.get("sub_stats")),
        EQUIP_QUALITY: quality,
        "_item_id": str(row.get("item_id") or ""),
        "is_duplicate_drive": bool(row.get("is_duplicate_drive")),
        "duplicate_group_id": row.get("duplicate_group_id"),
        "duplicate_index": row.get("duplicate_index"),
        "duplicate_count": row.get("duplicate_count"),
        "_uid_serial": int(row.get("uid_serial") or 0),
        "_uid_slot": int(row.get("uid_slot") or 0),
    }


def _replacement_item_icon(asset_catalog, item_kind, item):
    """Resolve the packaged official core or module image for a replacement card."""
    if asset_catalog is None:
        return None
    item_id = str(item.get("_item_id") or "")
    kind = "module" if item_kind == "drive" else "core"
    return asset_catalog.inventory_item_icon(kind, item_id) if item_id else None


def _replacement_assignments(plan, old_uid, replacement):
    """Copy immutable plan assignments while replacing exactly one native UID."""
    replacement_serial = int(replacement["_uid_serial"])
    replacement_slot = int(replacement["_uid_slot"])
    assignments = []
    replaced = False
    for source in plan.get("assignments") or []:
        assignment = dict(source)
        uid = f"nte-{'module' if assignment.get('kind') == 'module' else 'core'}-{assignment.get('uid_slot')}-{assignment.get('uid_serial')}"
        if uid == str(old_uid):
            assignment["uid_serial"] = replacement_serial
            assignment["uid_slot"] = replacement_slot
            raw_assignment = dict(assignment.get("raw_assignment") or {})
            raw_assignment["uid"] = {"serial": replacement_serial, "slot": replacement_slot}
            assignment["raw_assignment"] = raw_assignment
            replaced = True
        assignments.append(assignment)
    if not replaced:
        raise ValueError("当前装备已变化，请刷新配装页面后重试。")
    return assignments


def _active_sqlite_equipment_users(user_dao, excluded_role_name: str) -> dict[tuple[str, int, int], tuple[str, ...]]:
    """Map native equipment UIDs to other active SQLite loadout roles once."""
    users: dict[tuple[str, int, int], list[str]] = {}
    for role_name, plan in user_dao.list_active_loadout_plans_by_role().items():
        if role_name == excluded_role_name:
            continue
        for assignment in plan.get("assignments") or []:
            kind = str(assignment.get("kind") or "")
            if kind not in {"module", "core"}:
                continue
            try:
                key = (kind, int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            except (KeyError, TypeError, ValueError):
                continue
            role_users = users.setdefault(key, [])
            if role_name not in role_users:
                role_users.append(role_name)
    return {key: tuple(names) for key, names in users.items()}


def _sqlite_replacement_candidates(database_path, role_name, item_kind, old_uid):
    """Read compatible alternatives from the active plan's immutable snapshot."""
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        plan = user_dao.get_active_loadout_plan_for_role(role_name)
        if plan is None:
            raise ValueError("未找到该角色的已保存方案")
        if plan.get("allocation_locked"):
            raise ValueError("锁定方案不能进行替换优化；请先解除锁定")
        snapshot_id = int(plan["source_snapshot_id"])
        rows = user_dao.list_inventory_items(snapshot_id)
        suit_names = {
            str(suit["suit_id"]): str(suit.get("name_zh") or suit["suit_id"]) for suit in static_dao.list_suits()
        }
        displays = [_sqlite_inventory_item_display(row, suit_names) for row in rows]
        items_by_key = {(int(item["_uid_serial"]), int(item["_uid_slot"])): item for item in displays}
        current = next((item for item in displays if str(item.get(EQUIP_UID)) == str(old_uid)), None)
        if current is None:
            raise ValueError("当前装备不在该方案绑定的背包快照中")
        expected_kind = "module" if item_kind == "drive" else "core"
        assigned = {
            (int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            for assignment in plan.get("assignments") or []
            if str(assignment.get("kind")) == expected_kind
        }
        equipped_by_roles = _active_sqlite_equipment_users(user_dao, role_name)
        locked_uids = {
            (str(owner["kind"]), int(owner["uid_serial"]), int(owner["uid_slot"]))
            for owner in user_dao.list_allocation_locked_equipment_owners()
        }
        old_key = (int(current["_uid_serial"]), int(current["_uid_slot"]))
        assigned_items = [
            items_by_key[(int(assignment["uid_serial"]), int(assignment["uid_slot"]))]
            for assignment in plan.get("assignments") or []
            if (int(assignment["uid_serial"]), int(assignment["uid_slot"])) in items_by_key
        ]
        plan_drives = [item for item in assigned_items if item.get(EQUIP_SHAPE_ID)]
        plan_tape = next((item for item in assigned_items if item.get(EQUIP_SET_NAME)), None)
        candidates = []
        for row, item in zip(rows, displays):
            if str(row.get("kind")) != expected_kind:
                continue
            item_key = (int(item["_uid_serial"]), int(item["_uid_slot"]))
            if item_key == old_key or item_key in assigned:
                continue
            if (expected_kind, item_key[0], item_key[1]) in locked_uids:
                continue
            if item_kind == "drive" and item.get(EQUIP_SHAPE_ID) != current.get(EQUIP_SHAPE_ID):
                continue
            if item_kind == "tape" and item.get(EQUIP_SET_NAME) != current.get(EQUIP_SET_NAME):
                continue
            candidate = dict(item)
            candidate["_used_by"] = equipped_by_roles.get(
                (expected_kind, int(candidate["_uid_serial"]), int(candidate["_uid_slot"])), ()
            )
            candidates.append(candidate)
        return plan, current, candidates, plan_drives, plan_tape


def _open_official_saved_plan_optimizer(
    window,
    role_name: str,
    item_kind: str,
    uid: str,
) -> bool:
    """Open the replacement flow backed by the new SQLite role panel.

    The old role editor was removed, but this inventory card action remained.
    Loading the official role detail here makes the panel data available before
    direct-damage evaluation and keeps the replacement calculation on the same
    SQLite path as the new role page.
    """
    database_path = _equipment_paths(window)[0]
    with UserDataDao(database_path) as dao:
        plan = dao.get_active_loadout_plan_for_role(role_name)
    if not isinstance(plan, dict):
        return False
    if plan.get("allocation_locked"):
        QMessageBox.information(window, "替换优化", "当前方案已锁定，请先在配装页解除锁定。")
        return True
    character_id = plan.get("character_id")
    if character_id is None:
        return False
    from src.services.official_role_page_service import load_official_role_detail
    from src.ui.controllers.official_role_replacement_controller import (
        show_official_role_replacement,
    )

    detail = load_official_role_detail(database_path, int(character_id))
    expected_kind = "module" if item_kind == "drive" else "core"
    target = next(
        (
            item
            for item in (detail.get("equipment_contexts", {}).get("saved", {}).get("items") or ())
            if str(item.get("kind") or "") == expected_kind
            and f"nte-{expected_kind}-{item.get('uid_slot')}-{item.get('uid_serial')}" == str(uid)
        ),
        None,
    )
    if not isinstance(target, dict):
        return False
    refresh = getattr(window, "_refresh_equip", None)
    show_official_role_replacement(
        window,
        detail,
        target,
        on_saved=refresh if callable(refresh) else None,
    )
    return True


def _optimize_saved_equipment(
    self,
    role_name: str,
    item_kind: str,
    uid: str,
    *,
    weights_override: dict[str, float] | None = None,
    main_weights_override: dict[str, float] | None = None,
    rank_by_damage: bool = True,
    after_replace=None,
    core_term: str = "卡带",
    assignment_scores_override: dict[str, float] | None = None,
    exclude_used_by_others: bool = False,
    replacement_persister=None,
):
    """Restore per-card optimization using only the active SQLite plan snapshot."""
    if rank_by_damage and weights_override is None and main_weights_override is None:
        try:
            if _open_official_saved_plan_optimizer(self, role_name, item_kind, uid):
                return
        except Exception as exc:
            QMessageBox.warning(self, "优化替换", f"无法读取官方角色详情：{exc}")
            return
        QMessageBox.warning(self, "优化替换", "当前方案无法在官方角色详情中定位，请重新计算并保存后重试。")
        return
    database_path, _, asset_dir = _equipment_paths(self)
    try:
        plan, current, candidates, _plan_drives, _plan_tape = _sqlite_replacement_candidates(
            database_path, role_name, item_kind, uid
        )
    except Exception as exc:
        QMessageBox.warning(self, "优化替换", str(exc))
        return
    if exclude_used_by_others:
        candidates = [candidate for candidate in candidates if not candidate.get("_used_by")]
    role_cfg = {}
    if not isinstance(weights_override, dict) or not isinstance(main_weights_override, dict):
        role_cfg = (getattr(self, "roles_db", {}) or {}).get(role_name, {})
    weights = dict(weights_override) if isinstance(weights_override, dict) else role_cfg.get("weights", {})
    main_weights = (
        dict(main_weights_override) if isinstance(main_weights_override, dict) else role_cfg.get("main_weights")
    )
    presentation = equipment_presentation(self)
    if item_kind == "drive":
        score = lambda item: float(
            presentation.score_drive(
                item.get(EQUIP_SUB_STATS, {}), item.get(EQUIP_SHAPE_ID, ""), weights, item.get(EQUIP_QUALITY, "Gold")
            )
        )
        title = f"优化替换 - {current.get(EQUIP_SHAPE_ID) or '驱动'}"
    else:
        score = lambda item: float(
            presentation.score_tape(
                item.get(EQUIP_MAIN_STATS, ""),
                item.get(EQUIP_SUB_STATS, {}),
                weights,
                item.get(EQUIP_QUALITY, "Gold"),
                main_weights,
            )
        )
        title = f"替换{core_term} - {current.get(EQUIP_SET_NAME) or core_term}"
    current_score = score(current)
    current_margin = None
    ranked = sorted(
        ((None, score(candidate), candidate) for candidate in candidates),
        key=lambda row: row[1],
        reverse=True,
    )[:30]
    if not ranked:
        QMessageBox.information(self, "优化替换", "当前快照中没有可替换的同类装备。")
        return

    # Keep the same current-item / candidate-list layout used by the 角色功能 page.
    # Only the visual structure is shared: all items below still come from one
    # stable SQLite snapshot and the replacement is saved as a SQLite plan.
    item_label = current.get(EQUIP_SHAPE_ID) if item_kind == "drive" else current.get(EQUIP_SET_NAME)
    asset_catalog = GameUiAssetCatalog(asset_dir / "game_ui")
    dialog = QDialog(self)
    dialog.setWindowTitle(f"{role_name} · {title}")
    dialog.resize(850, 650)
    layout = QVBoxLayout(dialog)
    role_header = QLabel(f"装配角色：{role_name}")
    role_header.setStyleSheet(
        themed_style(
            "font-size:15px;font-weight:800;color:#4dd0e1;"
            "border:1px solid #4dd0e1;border-radius:7px;padding:5px 12px;"
            "background:rgba(77,208,225,0.10)"
        )
    )
    layout.addWidget(role_header)
    summary_text = (
        f"当前直伤收益：{current_margin:+.2f}%（候选按直伤收益排序）"
        if rank_by_damage
        else "候选按当前词条配装权重评分排序"
    )
    summary = QLabel(
        f"{summary_text}；仅显示同{('形状' if item_kind == 'drive' else '套装')}的候选装备，"
        "不会占用本方案其他已选装备。"
    )
    summary.setWordWrap(True)
    summary.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(summary)
    current_group = QGroupBox("当前驱动" if item_kind == "drive" else f"当前{core_term}")
    current_layout = QVBoxLayout(current_group)
    current_layout.addWidget(
        presentation.equipment_card(
            item_label or core_term,
            current.get(EQUIP_MAIN_STATS, ""),
            current.get(EQUIP_SUB_STATS, {}),
            current.get(EQUIP_SHAPE_ID),
            current.get(EQUIP_UID, ""),
            weights,
            (
                current_score,
                allocation_grade(
                    current_score,
                    15
                    if item_kind == "tape"
                    else presentation.shape_area(
                        current.get(EQUIP_SHAPE_ID, ""),
                        3,
                    ),
                ),
            ),
            current.get(EQUIP_QUALITY, "Gold"),
            is_duplicate_drive=item_kind == "drive" and bool(current.get("is_duplicate_drive")),
            main_weights=main_weights,
            card_variant="inventory",
            item_icon_path=_replacement_item_icon(asset_catalog, item_kind, current),
        )
    )
    layout.addWidget(current_group)
    candidates_group = QGroupBox(f"可替换{'驱动' if item_kind == 'drive' else core_term} ({len(ranked)}个)")
    candidates_layout = QVBoxLayout(candidates_group)
    scroll = QScrollArea(candidates_group)
    scroll.setWidgetResizable(True)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(8)
    for candidate_margin, candidate_score, candidate in ranked:

        def apply_replacement(_checked=False, selected=candidate, selected_score=candidate_score):
            try:
                assignments = _replacement_assignments(plan, uid, selected)
                # This is an explicit user replacement: show green CHANGE for
                # the incoming item, and keep a complete SQLite diff for the
                # button/dialog after the page is refreshed.
                replacement_diff = {
                    DIFF_CHANGED: True,
                    DIFF_ADDED_UIDS: [str(selected.get(EQUIP_UID) or "")],
                    DIFF_ADDED: [
                        {
                            EQUIP_UID: str(selected.get(EQUIP_UID) or ""),
                            EQUIP_IS_CHANGED: True,
                        }
                    ],
                    DIFF_REMOVED: [{EQUIP_UID: str(current.get(EQUIP_UID) or "")}],
                }
                replacement_payload = dict(plan.get("payload") or {})
                replacement_payload["last_diff"] = replacement_diff
                replacement_payload["changed_uids"] = [str(selected.get(EQUIP_UID) or "")]
                assignment_scores = dict(
                    assignment_scores_override
                    if isinstance(assignment_scores_override, dict)
                    else replacement_payload.get("assignment_scores") or {}
                )
                assignment_scores.pop(str(uid), None)
                assignment_scores[str(selected.get(EQUIP_UID) or "")] = float(selected_score)
                replacement_payload["assignment_scores"] = assignment_scores
                exact_score = exact_assignment_score_total(assignments, assignment_scores)
                if callable(replacement_persister):
                    replacement_persister(selected, selected_score, current_score)
                else:
                    with UserDataDao(database_path) as dao:
                        dao.save_loadout_plan(
                            name=str(plan.get("name") or f"优化方案：{role_name}"),
                            character_id=int(plan["character_id"]),
                            assignments=assignments,
                            source_snapshot_id=int(plan["source_snapshot_id"]),
                            status="saved",
                            score=(
                                exact_score
                                if is_virtual_equipment_assignment(current)
                                and exact_score is not None
                                else float(plan.get("score") or 0.0)
                                - current_score
                                + selected_score
                            ),
                            payload=replacement_payload,
                            is_active=True,
                        )
            except Exception as exc:
                QMessageBox.warning(dialog, "替换失败", str(exc))
                return
            dialog.accept()
            self._refresh_equip(restore_role_name=role_name)
            if callable(after_replace):
                after_replace(selected, selected_score, current_score)
            QMessageBox.information(self, "优化替换", "已保存为新的配装方案。")

        candidate_card = QWidget()
        candidate_layout = QVBoxLayout(candidate_card)
        candidate_layout.setContentsMargins(0, 0, 0, 0)
        candidate_layout.setSpacing(4)
        candidate_layout.addWidget(
            presentation.equipment_card(
                candidate.get(EQUIP_SHAPE_ID) or candidate.get(EQUIP_SET_NAME, core_term),
                candidate.get(EQUIP_MAIN_STATS, ""),
                candidate.get(EQUIP_SUB_STATS, {}),
                candidate.get(EQUIP_SHAPE_ID),
                candidate.get(EQUIP_UID, ""),
                weights,
                (
                    candidate_score,
                    allocation_grade(
                        candidate_score,
                        15
                        if item_kind == "tape"
                        else presentation.shape_area(
                            candidate.get(EQUIP_SHAPE_ID, ""),
                            3,
                        ),
                    ),
                ),
                candidate.get(EQUIP_QUALITY, "Gold"),
                is_duplicate_drive=item_kind == "drive" and bool(candidate.get("is_duplicate_drive")),
                main_weights=main_weights,
                replacement_callback=apply_replacement,
                replacement_text="替换",
                card_variant="inventory",
                item_icon_path=_replacement_item_icon(asset_catalog, item_kind, candidate),
            )
        )
        if rank_by_damage:
            margin = QLabel(f"直伤收益：{candidate_margin:+.2f}%")
            margin.setStyleSheet(themed_style("color:#ffaa00;font-weight:700;font-size:12px"))
            candidate_layout.addWidget(margin)
        used_by = tuple(candidate.get("_used_by") or ())
        if used_by:
            user_label = QLabel(f"使用者：{', '.join(used_by)}")
            user_label.setStyleSheet(themed_style("color:#ff9800;font-size:12px"))
            candidate_layout.addWidget(user_label)
        content_layout.addWidget(candidate_card)
    content_layout.addStretch()
    scroll.setWidget(content)
    candidates_layout.addWidget(scroll)
    layout.addWidget(candidates_group, 1)
    close = QPushButton("关闭")
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    dialog.exec()
