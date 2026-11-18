# 构建配装方案差异、替换预览和已保存方案同步交互。
"""MainWindow methods for allocation."""

from __future__ import annotations

from src.ui.common.text_catalog import plain_label, resolve_text_catalog
from src.ui.common.enum_text import ui_enum_text
from src.domain.localization.catalog import localized_text

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.allocation.results.bonus_summary import (
    collect_added_uids,
    split_loadout_sources,
)
from src.features.allocation.results.plan_diff_pairing import pair_drive_diff_items
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_ITEM_TYPE,
    EQUIP_MAIN_STATS,
    EQUIP_MAIN_VALUE,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    plan_drives,
)
from src.services.allocation.results.comparison_scoring import comparison_display_item, comparison_notice


def _section_label(_window, text):
    """方案差异只保留当前页面实际使用的区段标题，不跨不可达页面转发。"""
    label = plain_label(text)
    label.setStyleSheet(themed_style(
        "font-size:14px;font-weight:700;color:#c9d1d9;"
        "border:none;background:transparent;padding:2px 0"
    ))
    return label


__all__ = [
    "_section_label",
    "_render_results",
    "_calc_grade",
    "_show_plan_diff_dialog",
    "_build_plan_diff_dialog",
    "_diff_item_card",
    "_diff_item_score_info",
    "_plan_diff_text",
    "_stat_w",
    "_stat_c",
    "_weighted_score",
    "_quality_coef",
    "_canonical_stat_name",
    "_stat_number_value",
    "_item_value",
    "_add_stat_total",
    "_fallback_tape_main_value",
    "_extra_shape_area",
    "_equipment_bonus_rows",
    "_role_base_bonus_rows",
    "_merge_bonus_row_lists",
    "_synthesize_character_bonus_rows",
    "_bonus_rows_for_mode",
    "_bonus_summary_mode_label",
    "_make_bonus_mode_switch",
    "_clear_layout_widgets",
    "_format_bonus_value",
    "_role_stat_priority_stats",
    "_bonus_stat_weight",
    "_sort_bonus_rows_for_role",
    "_sort_bonus_aligned_rows_for_role",
    "_bonus_stat_label_style",
    "_format_panel_value",
    "_sort_bonus_aligned_rows",
    "_role_bonus_summary_panel",
    "_refresh_bonus_summary_panel",
    "_aligned_bonus_comparison_rows",
    "_has_bonus_delta",
    "_bonus_row_widget",
    "_bonus_comparison_column",
    "_bonus_delta_row_widget",
    "_bonus_delta_column",
    "_bonus_comparison_widget",
    "_show_bonus_summary_dialog",
    "_show_bonus_comparison_dialog",
    "_score_drive_dict",
    "_score_tape_dict",
    "_equip_card",
]



def _calc_grade(self, score, area):
    max_score = area * 10.0
    if max_score == 0:
        return "D"
    ratio = score / max_score
    if ratio >= 0.8:
        return "ACE"
    elif ratio >= 0.7:
        return "SSS"
    elif ratio >= 0.6:
        return "SS"
    elif ratio >= 0.5:
        return "S"
    elif ratio >= 0.4:
        return "A"
    elif ratio >= 0.3:
        return "B"
    elif ratio >= 0.2:
        return "C"
    return "D"


def _plan_diff_text(self, role_name, diff):
    removed = [
        _hydrate_diff_item(self, role_name, item)
        for item in (diff.get(DIFF_REMOVED, []) or [])
        if isinstance(item, dict)
    ]
    added = [
        _hydrate_diff_item(self, role_name, item)
        for item in (diff.get(DIFF_ADDED, []) or [])
        if isinstance(item, dict)
    ]
    catalog = getattr(self, "text_catalog", None)
    if not removed and not added:
        return localized_text(
            catalog,
            "allocation.results_diff.no_changes",
            "本次配装与已保存方案没有装备变动。",
        )
    lines = [localized_text(
        catalog,
        "allocation.results_diff.role_heading",
        "{role_name} 配装变动：",
        role_name=role_name,
    )]
    if removed:
        lines.append("\n" + ui_enum_text(catalog, "卸下："))
        lines.extend(f"- {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in removed)
    if added:
        lines.append("\n" + ui_enum_text(catalog, "换上："))
        lines.extend(f"+ {item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID)}" for item in added)
    return "\n".join(lines)


def _diff_item_score_info(self, item, role_name=None):
    if item.get("comparison_score_unavailable"):
        return None
    item_type = item.get(EQUIP_TYPE, "drive")
    if EQUIP_SCORE in item:
        score = float(item.get(EQUIP_SCORE, 0.0) or 0.0)
    elif role_name:
        role_cfg = (getattr(self, "roles_db", {}) or {}).get(role_name, {})
        weights = role_cfg.get("weights", {})
        quality = item.get(EQUIP_QUALITY, "Gold")
        sub_stats = item.get(EQUIP_SUB_STATS, {}) or {}
        if item_type == "tape":
            main_stat = item.get(EQUIP_MAIN_STATS, "")
            if isinstance(main_stat, dict):
                main_stat = next(iter(main_stat), "")
            score = float(self._score_tape_dict(
                str(main_stat or ""),
                sub_stats,
                weights,
                quality,
                role_cfg.get("main_weights"),
            ))
        else:
            score = float(self._score_drive_dict(
                sub_stats,
                item.get(EQUIP_SHAPE_ID, ""),
                weights,
                quality,
            ))
    else:
        return None
    grade = item.get(EQUIP_GRADE)
    if not grade:
        area = int(
            item.get(EQUIP_SCORE_AREA)
            or item.get(EQUIP_AREA)
            or (15 if item_type == "tape" else 0)
            or getattr(self, "_shape_areas", {}).get(item.get(EQUIP_SHAPE_ID, ""), 0)
            or 0
        )
        grade = self._calc_grade(score, area) if area else "D"
    return score, str(grade)


def _diff_value(item, key, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _diff_item_type(item):
    explicit = _diff_value(item, EQUIP_TYPE) or _diff_value(item, EQUIP_ITEM_TYPE)
    if explicit:
        return str(explicit)
    if _diff_value(item, EQUIP_SHAPE_ID) == "TAPE_15":
        return "tape"
    main_stats = _diff_value(item, EQUIP_MAIN_STATS)
    return "tape" if isinstance(main_stats, str) and main_stats else "drive"


def _diff_grade(self, score, area):
    calc = getattr(self, "_calc_grade", None)
    if calc:
        return calc(score, area)
    return _calc_grade(self, score, area)


def _diff_snapshot_from_source(self, role_name, source):
    uid = str(_diff_value(source, EQUIP_UID, "") or "")
    if not uid:
        return {}
    item_type = _diff_item_type(source)
    sub_stats = _diff_value(source, EQUIP_SUB_STATS, {}) or {}
    quality = _diff_value(source, EQUIP_QUALITY, "Gold")
    area = int(
        _diff_value(source, EQUIP_SCORE_AREA)
        or _diff_value(source, EQUIP_AREA)
        or (15 if item_type == "tape" else 0)
        or 0
    )
    role_scores = _diff_value(source, "role_scores", {}) or {}
    score = _diff_value(source, EQUIP_SCORE)
    if score is None and isinstance(role_scores, dict):
        score = role_scores.get(role_name)
    score_value = None if score is None else round(float(score or 0.0), 2)
    grade = _diff_value(source, EQUIP_GRADE)
    if grade is None and score_value is not None and area:
        grade = _diff_grade(self, score_value, area)

    snapshot = {
        EQUIP_UID: uid,
        EQUIP_TYPE: item_type,
        EQUIP_DISPLAY_NAME: str(_diff_value(source, EQUIP_DISPLAY_NAME, "") or uid),
        EQUIP_SUB_STATS: sub_stats,
        EQUIP_QUALITY: quality,
    }
    item_icon_path = _diff_value(source, "item_icon_path")
    if item_icon_path:
        snapshot["item_icon_path"] = item_icon_path
    if item_type == "tape":
        snapshot[EQUIP_SET_NAME] = _diff_value(source, EQUIP_SET_NAME, "") or "卡带"
        snapshot[EQUIP_MAIN_STATS] = _diff_value(source, EQUIP_MAIN_STATS, "")
        snapshot[EQUIP_MAIN_VALUE] = _diff_value(source, EQUIP_MAIN_VALUE, None)
        snapshot[EQUIP_SHAPE_ID] = "TAPE_15"
    else:
        snapshot[EQUIP_SHAPE_ID] = _diff_value(source, EQUIP_SHAPE_ID, "") or ""
    if area:
        snapshot[EQUIP_AREA] = area
        snapshot[EQUIP_SCORE_AREA] = area
    if score_value is not None:
        snapshot[EQUIP_SCORE] = score_value
    if grade is not None:
        snapshot[EQUIP_GRADE] = str(grade)
    return snapshot


def _merge_diff_item(base, source):
    merged = dict(base or {})
    for key, value in (source or {}).items():
        if key not in merged or merged[key] in (None, "", {}, []):
            merged[key] = value
    return merged


def _loadout_items_from_role_data(role_data):
    if not isinstance(role_data, dict):
        return []
    items = []
    tape = role_data.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict):
        items.append(tape)
    items.extend([item for item in role_data.get(ROLE_EQUIPPED_DRIVES, []) or [] if isinstance(item, dict)])
    return items


def _diff_saved_sources(self, role_name):
    role_data = (getattr(self, "equipped_state", {}) or {}).get(role_name, {})
    items = _loadout_items_from_role_data(role_data)
    return items


def _previous_loadout_from_diff(self, role_name, tape, drives, role_diff):
    role_diff = role_diff or {}
    removed = [dict(item) for item in (role_diff.get(DIFF_REMOVED, []) or []) if isinstance(item, dict)]
    added_uids = collect_added_uids(role_diff)
    kept = []
    if tape:
        uid = str(_diff_value(tape, EQUIP_UID, "") or "")
        if uid and uid not in added_uids:
            kept.append(tape if isinstance(tape, dict) else _diff_snapshot_from_source(self, role_name, tape))
    for drive in drives or []:
        uid = str(_diff_value(drive, EQUIP_UID, "") or "")
        if uid and uid not in added_uids:
            kept.append(drive if isinstance(drive, dict) else _diff_snapshot_from_source(self, role_name, drive))
    old_items = kept + [_hydrate_diff_item(self, role_name, item) for item in removed]
    return split_loadout_sources(old_items)


def _diff_plan_sources(self, role_name):
    plan = (getattr(self, "final_plan", {}) or {}).get(role_name, {})
    if not isinstance(plan, dict):
        return []
    return ([plan.get(PLAN_ASSIGNED_TAPE)] if plan.get(PLAN_ASSIGNED_TAPE) else []) + plan_drives(plan)


def _diff_inventory_sources(self):
    context = getattr(self, "app_context", None)
    database_path = (
        context.account.user_database_path if context is not None else getattr(self, "user_database_path", "")
    )
    path_key = str(database_path)
    snapshot_id = getattr(self, "_pending_allocation_snapshot_id", None)
    if snapshot_id is None:
        return {}
    static_identity = getattr(self, "_pending_allocation_static_identity", None)
    release = getattr(self, "_pending_allocation_static_release", None)
    try:
        from src.services.allocation.results.plan_save import require_allocation_release
        from src.integrations.static_catalog.static_catalog_release import StaticCatalogReleaseReader
        static_path = static_identity[0] if static_identity is not None else None
        release = require_allocation_release(release, static_path)
        StaticCatalogReleaseReader().ensure_unchanged(release)
    except RuntimeError:
        return {}
    cache_key = (path_key, int(snapshot_id), release)
    cached = getattr(self, "_diff_inventory_index_cache", None)
    if cached and cached[0] == cache_key:
        return cached[1]
    index = {}
    try:
        from src.services.allocation.inputs.inventory import load_inventory_projection

        with StaticCatalogReleaseReader().read_unit(static_path, expected_release=release) as unit:
            data = load_inventory_projection(path_key, int(snapshot_id), static_path, static_read_unit=unit)
    except Exception:
        data = []
    for item in data:
        if isinstance(item, dict) and item.get("uid"):
            index[str(item["uid"])] = item
    setattr(self, "_diff_inventory_index_cache", (cache_key, index))
    return index


def _parse_diff_display_name(item):
    display = str(item.get(EQUIP_DISPLAY_NAME) or "")
    if not display or "-" not in display:
        return {}
    shape_id, raw_stats = display.split("-", 1)
    parsed = {"shape_id": shape_id.strip(), "type": "drive"}
    stats = {}
    for part in raw_stats.split("|"):
        part = part.strip()
        if "_" not in part:
            continue
        name, value = part.rsplit("_", 1)
        try:
            stats[name.strip()] = float(str(value).replace("%", "").strip())
        except Exception:
            continue
    if stats:
        parsed["sub_stats"] = stats
    return parsed


def _hydrate_diff_item(self, role_name, item):
    hydrated = dict(item or {})
    uid = str(hydrated.get(EQUIP_UID, "") or "")
    if uid:
        for source in _diff_saved_sources(self, role_name):
            if str(source.get(EQUIP_UID, "")) == uid:
                hydrated = _merge_diff_item(hydrated, _diff_snapshot_from_source(self, role_name, source))
                break
        if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS) or EQUIP_SCORE not in hydrated:
            for source in _diff_plan_sources(self, role_name):
                if str(_diff_value(source, EQUIP_UID, "")) == uid:
                    hydrated = _merge_diff_item(hydrated, _diff_snapshot_from_source(self, role_name, source))
                    break
        if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS) or EQUIP_SCORE not in hydrated:
            source = _diff_inventory_sources(self).get(uid)
            if source:
                hydrated = _merge_diff_item(hydrated, _diff_snapshot_from_source(self, role_name, source))
    if not hydrated.get(EQUIP_SHAPE_ID) or not hydrated.get(EQUIP_SUB_STATS):
        hydrated = _merge_diff_item(hydrated, _parse_diff_display_name(hydrated))
    item_type = hydrated.get(EQUIP_TYPE) or hydrated.get(EQUIP_ITEM_TYPE)
    if item_type:
        hydrated[EQUIP_TYPE] = item_type
    elif hydrated.get(EQUIP_SHAPE_ID) == "TAPE_15":
        hydrated[EQUIP_TYPE] = "tape"
    else:
        hydrated[EQUIP_TYPE] = "drive"
    if EQUIP_SCORE in hydrated and EQUIP_GRADE not in hydrated:
        area = int(
            hydrated.get(EQUIP_SCORE_AREA)
            or hydrated.get(EQUIP_AREA)
            or (15 if hydrated.get(EQUIP_TYPE) == "tape" else 0)
            or 0
        )
        if area:
            hydrated[EQUIP_GRADE] = _diff_grade(self, float(hydrated.get(EQUIP_SCORE) or 0.0), area)
            hydrated[EQUIP_SCORE_AREA] = area
    return hydrated


def _diff_item_card(self, role_name, item, is_new=False):
    item = _hydrate_diff_item(self, role_name, item)
    role_cfg = self.roles_db.get(role_name, {})
    weights = item.get("_comparison_weights", role_cfg.get("weights", {}))
    main_weights = item.get("_comparison_main_weights", role_cfg.get("main_weights"))
    score_info = getattr(self, "_diff_item_score_info", None) or (
        lambda diff_item, role=None: _diff_item_score_info(self, diff_item, role)
    )
    item_type = item.get(EQUIP_TYPE, "drive")
    if item_type == "tape":
        label = item.get(EQUIP_SET_NAME) or ui_enum_text(
            getattr(self, "text_catalog", None), "卡带",
        )
        main_stat = item.get(EQUIP_MAIN_STATS, "")
        shape_id = None
    else:
        label = item.get(EQUIP_SHAPE_ID) or item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID, "")
        main_stat = ""
        shape_id = item.get(EQUIP_SHAPE_ID) or ""
    is_changed = bool(item.get(EQUIP_IS_CHANGED, False))
    card = self._equip_card(
        label,
        main_stat,
        item.get(EQUIP_SUB_STATS, {}) or {},
        shape_id,
        item.get(EQUIP_UID, ""),
        weights,
        score_info(item, role_name),
        item.get(EQUIP_QUALITY, "Gold"),
        is_new=is_new and not is_changed,
        is_changed=is_changed,
        is_duplicate_drive=bool(item.get("is_duplicate_drive", False)),
        main_weights=main_weights,
        card_variant="result",
        item_icon_path=item.get("item_icon_path"),
    )
    if item.get("comparison_score_unavailable"):
        card.layout().addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('selector.score_unknown')))
    return card


def _append_equipment_swap_frame(body_layout, role_name, old_item, new_item, diff_item_card, *, text_catalog=None):
    pair_frame = QFrame()
    pair_frame.setStyleSheet(
        themed_style("QFrame{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:8px 10px}")
    )
    pair_layout = QVBoxLayout(pair_frame)
    pair_layout.setSpacing(6)
    pair_layout.setContentsMargins(8, 6, 8, 6)

    old_lbl = plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.append_equipment_swap_frame.old_lbl'))
    old_lbl.setStyleSheet(
        themed_style("font-size:11px;font-weight:700;color:#f85149;border:none;background:transparent;padding:2px 4px")
    )
    pair_layout.addWidget(old_lbl)
    if old_item is not None:
        pair_layout.addWidget(diff_item_card(role_name, old_item, is_new=False))
    else:
        pair_layout.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.append_equipment_swap_frame.caption')))

    arrow = plain_label("  ↓")
    arrow.setStyleSheet(
        themed_style(
            "font-size:18px;font-weight:700;color:#58a6ff;border:none;background:transparent;padding:0 0 0 12px"
        )
    )
    pair_layout.addWidget(arrow)

    new_lbl = plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.append_equipment_swap_frame.new_lbl'))
    new_lbl.setStyleSheet(
        themed_style("font-size:11px;font-weight:700;color:#56d364;border:none;background:transparent;padding:2px 4px")
    )
    pair_layout.addWidget(new_lbl)
    if new_item is not None:
        pair_layout.addWidget(diff_item_card(role_name, new_item, is_new=True))
    else:
        pair_layout.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.allocation.append_equipment_swap_frame.caption_2')))

    body_layout.addWidget(pair_frame)


def _build_plan_diff_dialog(self, role_name, diff):
    catalog = getattr(self, "text_catalog", None)
    dlg = QDialog(getattr(self, "dialog_parent", None))
    dlg.setWindowTitle(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.allocation.build_plan_diff_dialog.dlg', role_name=f'{role_name}'))
    dlg.setMinimumSize(320, 240)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)
    notice = plain_label(comparison_notice(diff))
    notice.setWordWrap(True)
    layout.addWidget(notice)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(0, 0, 0, 0)
    body_layout.setSpacing(10)
    section_label = getattr(self, "_section_label", None) or (lambda text: _section_label(self, text))
    diff_item_card = getattr(self, "_diff_item_card", None) or (
        lambda role, item, is_new=False: _diff_item_card(self, role, item, is_new)
    )

    removed = [
        _hydrate_diff_item(self, role_name, comparison_display_item(item, diff))
        for item in (diff.get(DIFF_REMOVED, []) or [])
        if isinstance(item, dict)
    ]
    added = [
        _hydrate_diff_item(self, role_name, comparison_display_item(item, diff))
        for item in (diff.get(DIFF_ADDED, []) or [])
        if isinstance(item, dict)
    ]
    if not removed and not added:
        body_layout.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.allocation.build_plan_diff_dialog.caption')))
    else:
        removed_tape = [it for it in removed if it.get(EQUIP_TYPE) == "tape"]
        removed_drives = [it for it in removed if it.get(EQUIP_TYPE) != "tape"]
        added_tape = [it for it in added if it.get(EQUIP_TYPE) == "tape"]
        added_drives = [it for it in added if it.get(EQUIP_TYPE) != "tape"]

        pair_index = 0

        if removed_tape or added_tape:
            pair_index += 1
            body_layout.addWidget(section_label(localized_text(
                catalog,
                "allocation.results_diff.pair_title",
                "变动 {index}：{detail}",
                index=pair_index,
                detail=ui_enum_text(catalog, "卡带"),
            )))
            _append_equipment_swap_frame(
                body_layout,
                role_name,
                removed_tape[0] if removed_tape else None,
                added_tape[0] if added_tape else None,
                diff_item_card,
                text_catalog=getattr(self, 'text_catalog', None),
            )

        drive_pairs, unmatched_old, unmatched_new = pair_drive_diff_items(
            removed_drives,
            added_drives,
            getattr(self, "_shape_areas", {}) or {},
        )

        for old_d, new_d in drive_pairs:
            pair_index += 1
            old_sid = old_d.get(EQUIP_SHAPE_ID, ui_enum_text(catalog, "未知驱动"))
            new_sid = new_d.get(EQUIP_SHAPE_ID, ui_enum_text(catalog, "未知驱动"))
            title = localized_text(
                catalog,
                "allocation.results_diff.pair_transition",
                "变动 {index}：{old} → {new}",
                index=pair_index,
                old=old_sid,
                new=new_sid,
            ) if old_sid != new_sid else localized_text(
                catalog,
                "allocation.results_diff.pair_title",
                "变动 {index}：{detail}",
                index=pair_index,
                detail=old_sid,
            )
            body_layout.addWidget(section_label(title))
            _append_equipment_swap_frame(body_layout, role_name, old_d, new_d, diff_item_card, text_catalog=getattr(self, 'text_catalog', None))

        for old_d in unmatched_old:
            pair_index += 1
            body_layout.addWidget(
                section_label(
                    localized_text(
                        catalog,
                        "allocation.results_diff.pair_title",
                        "变动 {index}：{detail}",
                        index=pair_index,
                        detail=old_d.get(EQUIP_SHAPE_ID) or ui_enum_text(catalog, "驱动"),
                    )
                )
            )
            _append_equipment_swap_frame(
                body_layout,
                role_name,
                old_d,
                None,
                diff_item_card,
                text_catalog=getattr(self, 'text_catalog', None),
            )

        for new_d in unmatched_new:
            pair_index += 1
            body_layout.addWidget(
                section_label(
                    localized_text(
                        catalog,
                        "allocation.results_diff.pair_title",
                        "变动 {index}：{detail}",
                        index=pair_index,
                        detail=new_d.get(EQUIP_SHAPE_ID) or ui_enum_text(catalog, "驱动"),
                    )
                )
            )
            _append_equipment_swap_frame(
                body_layout,
                role_name,
                None,
                new_d,
                diff_item_card,
                text_catalog=getattr(self, 'text_catalog', None),
            )

    body_layout.addStretch()
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dlg, QSize(820, 560))
    return dlg


def _show_plan_diff_dialog(self, role_name, diff):
    self._build_plan_diff_dialog(role_name, diff).exec()
