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

from src.i18n import tr, display_term
from src.domain.allocation_rating import allocation_grade
from src.domain.loadout_plan_scores import exact_assignment_score_total
from src.app.theme import themed_style
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.loadout_equipment_identity import source_snapshots_share_equipment_uids
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.features.inventory.equipment_plan_projection import (
    display_stat_label as _display_stat_label,
)
from src.features.inventory.equipment_display_context import (
    equipment_presentation,
    equipment_paths as _equipment_paths,
)
from src.services.virtual_equipment_service import is_virtual_equipment_assignment
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
)


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
    "_inventory_uid_key",
    "_sqlite_inventory_item_display",
    "_sqlite_plan_display_state",
]
EQUIPMENT_ROLE_PLACEHOLDER_HEIGHT = 520
EQUIPMENT_VIEWPORT_PREFETCH_COUNT = 1
# Legacy test hosts and non-Qt callers retain the old batch-only path.
EQUIPMENT_INITIAL_RENDER_COUNT = 8
EQUIPMENT_RENDER_BATCH_SIZE = 3

from src.features.inventory.equipment_plan_display_state import (
    _inventory_uid_key,
    _sqlite_inventory_item_display,
    _sqlite_plan_display_state,
)


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


def _active_sqlite_equipment_users(
    user_dao,
    excluded_plan_id: int | str | None,
    *,
    target_snapshot_id: int | None = None,
) -> dict[tuple[str, int, int], tuple[str, ...]]:
    """Map native equipment UIDs to other active SQLite loadout roles once."""
    users: dict[tuple[str, int, int], list[str]] = {}
    target_summary_loader = getattr(user_dao, "inventory_snapshot_summary", None)
    target_summary = (
        target_summary_loader(int(target_snapshot_id)) or {}
        if target_snapshot_id is not None and callable(target_summary_loader)
        else {}
    )
    slot_plan_loader = getattr(user_dao, "list_current_loadout_slot_plans", None)
    if callable(slot_plan_loader):
        slot_rows = slot_plan_loader()
    else:
        # Keep the small legacy-DAO seam usable for older callers while the
        # production path always resolves individual current slots.
        slot_rows = [
            {
                "slot": {"character_id": role_name},
                "plan": plan,
            }
            for role_name, plan in getattr(
                user_dao,
                "list_active_loadout_plans_by_role",
                lambda: {},
            )().items()
        ]
    for row in slot_rows:
        slot = row["slot"]
        plan = row["plan"]
        owner_snapshot_id = plan.get("source_snapshot_id")
        if (
            target_snapshot_id is not None
            and owner_snapshot_id is not None
            and callable(target_summary_loader)
            and not source_snapshots_share_equipment_uids(
                int(target_snapshot_id),
                target_summary.get("source"),
                int(owner_snapshot_id),
                (target_summary_loader(int(owner_snapshot_id)) or {}).get("source"),
            )
        ):
            continue
        payload = plan.get("payload") or {}
        role_name = str(payload.get("source_role_name") or slot["character_id"])
        if excluded_plan_id is not None:
            plan_id = plan.get("plan_id")
            if plan_id is not None and int(plan_id) == excluded_plan_id:
                continue
            if isinstance(excluded_plan_id, str) and role_name == excluded_plan_id:
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


def _sqlite_replacement_candidates(
    database_path,
    role_name,
    item_kind,
    old_uid,
    *,
    plan_id: int | None = None,
):
    """Read compatible alternatives from the selected plan's immutable snapshot."""
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        plan = user_dao.get_loadout_plan(plan_id) if plan_id is not None else user_dao.get_active_loadout_plan_for_role(role_name)
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
        equipped_by_roles = _active_sqlite_equipment_users(
            user_dao,
            int(plan["plan_id"]),
            target_snapshot_id=snapshot_id,
        )
        snapshot_summary = user_dao.inventory_snapshot_summary(snapshot_id) or {}
        locked_uids: set[tuple[str, int, int]] = set()
        for owner in user_dao.list_allocation_locked_equipment_owners():
            owner_snapshot_id = owner.get("source_snapshot_id")
            if owner_snapshot_id is not None:
                owner_summary = user_dao.inventory_snapshot_summary(
                    int(owner_snapshot_id)
                ) or {}
                same_item_space = source_snapshots_share_equipment_uids(
                    snapshot_id,
                    snapshot_summary.get("source"),
                    int(owner_snapshot_id),
                    owner_summary.get("source"),
                )
            else:
                # An old locked plan without a fixed snapshot is ambiguous;
                # keep the historical conservative reservation behavior.
                same_item_space = True
            if same_item_space:
                locked_uids.add((
                    str(owner["kind"]),
                    int(owner["uid_serial"]),
                    int(owner["uid_slot"]),
                ))
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
    *,
    plan_id: int | None = None,
) -> bool:
    """Open the replacement flow backed by the new SQLite role panel.

    The old role editor was removed, but this inventory card action remained.
    Loading the official role detail here makes the panel data available before
    direct-damage evaluation and keeps the replacement calculation on the same
    SQLite path as the new role page.
    """
    database_path = _equipment_paths(window)[0]
    with UserDataDao(database_path) as dao:
        plan = (
            dao.get_loadout_plan(int(plan_id))
            if plan_id is not None
            else dao.get_active_loadout_plan_for_role(role_name)
        )
    if not isinstance(plan, dict):
        return False
    if plan.get("allocation_locked"):
        QMessageBox.information(window, tr("替换优化"), tr("当前方案已锁定，请先在配装页解除锁定。"))
        return True
    character_id = plan.get("character_id")
    if character_id is None:
        return False
    from src.services.official_role_page_service import load_official_role_detail
    from src.ui.controllers.official_role_replacement_controller import (
        show_official_role_replacement,
    )

    detail = load_official_role_detail(database_path, int(character_id))
    contexts = detail.get("equipment_contexts", {}) or {}
    context_key = next(
        (
            key
            for key, context in contexts.items()
            if int((context.get("plan") or {}).get("plan_id") or 0)
            == int(plan.get("plan_id") or 0)
        ),
        None,
    )
    if context_key is None:
        return False
    expected_kind = "module" if item_kind == "drive" else "core"
    target = next(
        (
            item
            for item in (contexts.get(context_key, {}).get("items") or ())
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
        context_key=context_key,
        on_saved=refresh if callable(refresh) else None,
    )
    return True


def _saved_plan_uses_custom_character(
    database_path,
    role_name: str,
    *,
    plan_id: int | None,
) -> bool:
    """Resolve custom-role identity from the saved plan's account-local ID."""

    with UserDataDao(database_path) as dao:
        plan = (
            dao.get_loadout_plan(int(plan_id))
            if plan_id is not None
            else dao.get_active_loadout_plan_for_role(role_name)
        )
        if not isinstance(plan, dict) or plan.get("character_id") is None:
            return False
        character_id = int(plan["character_id"])
        return any(
            int(custom["character_id"]) == character_id
            for custom in dao.list_custom_characters()
        )


def _custom_plan_weight_overrides(
    database_path,
    plan: dict,
) -> tuple[dict[str, float], dict[str, float]]:
    """Read current custom-role weights in the labels rendered on equipment cards."""

    character_id = plan.get("character_id")
    if character_id is None:
        return {}, {}
    with UserDataDao(database_path) as dao:
        record = dao.get_character_weight_preferences(int(character_id)) or {}

    def display_weights(field_name: str) -> dict[str, float]:
        result: dict[str, float] = {}
        for property_id, raw_weight in (record.get(field_name) or {}).items():
            try:
                weight = float(raw_weight)
            except (TypeError, ValueError):
                continue
            if weight > 0:
                result[_display_stat_label(property_id)] = weight
        return result

    return display_weights("property_weights"), display_weights("main_property_weights")


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
    plan_id: int | None = None,
):
    """Restore per-card optimization using only the selected SQLite plan snapshot."""
    is_custom_role = False
    if (
        weights_override is None
        and main_weights_override is None
        and replacement_persister is None
    ):
        database_path = _equipment_paths(self)[0]
        is_custom_role = _saved_plan_uses_custom_character(
            database_path,
            role_name,
            plan_id=plan_id,
        )
        if not is_custom_role:
            try:
                if _open_official_saved_plan_optimizer(
                    self,
                    role_name,
                    item_kind,
                    uid,
                    plan_id=plan_id,
                ):
                    return
            except Exception as exc:
                QMessageBox.warning(self, tr("优化替换"), tr("无法读取官方角色详情：{error}", error=exc))
                return
            QMessageBox.warning(self, tr("优化替换"), tr("当前方案无法在官方角色详情中定位，请重新计算并保存后重试。"))
            return
        # Custom roles intentionally have no official detail or damage model.
        # Their replacement flow ranks only by their current account weights.
        rank_by_damage = False
    database_path, _, asset_dir = _equipment_paths(self)
    try:
        plan, current, candidates, _plan_drives, _plan_tape = _sqlite_replacement_candidates(
            database_path, role_name, item_kind, uid, plan_id=plan_id
        )
    except Exception as exc:
        QMessageBox.warning(self, tr("优化替换"), str(exc))
        return
    if exclude_used_by_others:
        candidates = [candidate for candidate in candidates if not candidate.get("_used_by")]
    role_cfg = {}
    custom_weights: dict[str, float] | None = None
    custom_main_weights: dict[str, float] | None = None
    if is_custom_role:
        custom_weights, custom_main_weights = _custom_plan_weight_overrides(database_path, plan)
    if not isinstance(weights_override, dict) or not isinstance(main_weights_override, dict):
        role_cfg = (getattr(self, "roles_db", {}) or {}).get(role_name, {})
    weights = (
        dict(weights_override)
        if isinstance(weights_override, dict)
        else custom_weights
        if custom_weights is not None
        else role_cfg.get("weights", {})
    )
    main_weights = (
        dict(main_weights_override)
        if isinstance(main_weights_override, dict)
        else custom_main_weights
        if custom_main_weights is not None
        else role_cfg.get("main_weights")
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
        QMessageBox.information(self, tr("优化替换"), tr("当前快照中没有可替换的同类装备。"))
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
    role_header = QLabel(tr("装配角色：{role}", role=display_term(role_name)))
    role_header.setStyleSheet(
        themed_style(
            "font-size:15px;font-weight:800;color:#4dd0e1;"
            "border:1px solid #4dd0e1;border-radius:7px;padding:5px 12px;"
            "background:rgba(77,208,225,0.10)"
        )
    )
    layout.addWidget(role_header)
    summary_text = (
        "自建角色仅按当前词条与卡带主属性权重评分排序"
        if is_custom_role
        else (
        f"当前直伤收益：{current_margin:+.2f}%（候选按直伤收益排序）"
        if rank_by_damage and current_margin is not None
        else "候选按当前词条配装权重评分排序"
        )
    )
    scope_term = tr("形状") if item_kind == "drive" else tr("套装")
    summary = QLabel(
        tr("{summary}；仅显示同{scope}的候选装备，不会占用本方案其他已选装备。",
           summary=summary_text, scope=scope_term)
    )
    summary.setWordWrap(True)
    summary.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(summary)
    current_group = QGroupBox(
        tr("当前驱动") if item_kind == "drive" else tr("当前{term}", term=core_term)
    )
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
            main_value=current.get("main_value"),
        )
    )
    layout.addWidget(current_group)
    candidates_group = QGroupBox(tr("可替换{kind} ({count}个)",
                             kind=display_term("驱动") if item_kind == "drive" else core_term,
                             count=len(ranked)))
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
                        save_replacement_to_slot = getattr(
                            dao, "save_replacement_plan_to_slot", None
                        )
                        save_kwargs = {
                            "name": str(plan.get("name") or f"优化方案：{role_name}"),
                            "assignments": assignments,
                            "source_snapshot_id": int(plan["source_snapshot_id"]),
                            "status": "saved",
                            "score": (
                                exact_score
                                if is_virtual_equipment_assignment(current)
                                and exact_score is not None
                                else float(plan.get("score") or 0.0)
                                - current_score
                                + selected_score
                            ),
                            "payload": replacement_payload,
                        }
                        if plan.get("slot_id") is not None and callable(save_replacement_to_slot):
                            save_replacement_to_slot(int(plan["slot_id"]), **save_kwargs)
                        else:
                            dao.save_loadout_plan(
                                character_id=int(plan["character_id"]),
                                is_active=bool(plan.get("is_active")),
                                slot_id=plan.get("slot_id"),
                                **save_kwargs,
                            )
            except Exception as exc:
                QMessageBox.warning(dialog, tr("替换失败"), str(exc))
                return
            dialog.accept()
            self._saved_equipment_cache_valid = False
            self._refresh_equip(restore_role_name=role_name)
            if callable(after_replace):
                after_replace(selected, selected_score, current_score)
            QMessageBox.information(self, tr("优化替换"), tr("已保存为新的配装方案。"))

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
                main_value=candidate.get("main_value"),
            )
        )
        if rank_by_damage:
            margin = QLabel(tr("直伤收益：{value:+.2f}%", value=candidate_margin))
            margin.setStyleSheet(themed_style("color:#ffaa00;font-weight:700;font-size:12px"))
            candidate_layout.addWidget(margin)
        used_by = tuple(candidate.get("_used_by") or ())
        if used_by:
            user_label = QLabel(
                tr("使用者：{names}", names=", ".join(display_term(n) for n in used_by))
            )
            user_label.setStyleSheet(themed_style("color:#ff9800;font-size:12px"))
            candidate_layout.addWidget(user_label)
        content_layout.addWidget(candidate_card)
    content_layout.addStretch()
    scroll.setWidget(content)
    candidates_layout.addWidget(scroll)
    layout.addWidget(candidates_group, 1)
    close = QPushButton(tr("关闭"))
    close.clicked.connect(dialog.accept)
    layout.addWidget(close)
    dialog.exec()
