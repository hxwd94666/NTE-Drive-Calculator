# 将指定配装槽位与当前角色配置冻结为只读分享面板，沿用公共面板及评分服务。
"""No login, game interaction, persistence, or damage estimation."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.allocation_rating import allocation_grade, loadout_total_grade
from src.domain.drive_layout import SHAPE_FOOTPRINTS
from src.domain.loadout_share import LoadoutSharePanel, ShareEquipment, ShareStat
from src.domain.loadout_share_highlight import ShareHighlightPolicy, share_formal_property, share_property_id
from src.integrations.loadout_share_assets import share_asset
from src.optimizer.contracts import (
    EQUIP_GRADE, EQUIP_MAIN_STATS, EQUIP_QUALITY, EQUIP_SCORE, EQUIP_SET_NAME,
    EQUIP_SHAPE_ID, EQUIP_SUB_STATS, EQUIP_UID, ROLE_EQUIPPED_DRIVES, ROLE_EQUIPPED_TAPE,
)
from src.optimizer.scoring import ScoringEngine
from src.services.equipment_level_projection_service import project_equipment_items_to_max_level
from src.services.equipment_scoring_service import score_drive_stats, score_tape_stats
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.advancement_stage_service import fork_panel_stats
from src.services.official_role_page_service import (
    calculate_official_role_attribute_summaries, load_official_role_detail,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


@dataclass(frozen=True, slots=True)
class LoadoutShareRequest:
    user_database_path: Path
    static_database_path: Path
    asset_root: Path
    config_dir: Path
    character_id: int | None
    role_name: str
    slot_name: str
    score: float
    state: Mapping[str, Any]
    weights: Mapping[str, float]
    main_weights: Mapping[str, float] | None
    shape_areas: Mapping[str, int]


def _check_cancel(cancelled: Callable[[], bool]) -> None:
    if cancelled():
        raise CancelledError()


def _number(value: float, percent: bool = False) -> str:
    if not math.isfinite(value):
        raise ValueError("分享面板包含非有限数值")
    if percent:
        return f"{value * 100:.1f}".rstrip("0").rstrip(".") + "%"
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"


def _source_suffix(profile: Mapping[str, Any], key: str) -> str:
    source = (profile.get("field_sources") or {}).get(key)
    return {"template": " · 模板", "account": " · 配置", "native_observed": ""}.get(source, "")


def _profile_stat(profile: Mapping[str, Any], key: str, label: str, suffix: str = "") -> ShareStat:
    value = profile.get(key)
    return ShareStat(label, "—" if value is None else f"{value}{suffix}{_source_suffix(profile, key)}")


def _equipment_stat(label: str, value: Any, highlighted: bool, *, locked: bool = False) -> ShareStat:
    raw = float(value)
    percent = "%" in label
    return _stat(label.replace("%", ""), _number(raw / 100 if percent else raw, percent),
                 highlighted, label, locked=locked)


def _stat(label: str, value: str, highlighted: bool = False, property_id: str = "", *, locked: bool = False) -> ShareStat:
    formal_key = {"PanelHP": "HPMaxBase", "PanelAtk": "AtkBase", "PanelDef": "DefBase",
                  "PanelCritRate": "CritBase", "PanelCritDamage": "CritDamageBase"}.get(property_id, property_id)
    # The manifest contains formal IDs, not localized scoring aliases.
    formal_key = share_formal_property(formal_key)
    formal_icon = share_asset("properties", formal_key) if formal_key else None
    icon_key = next((key for name, key in (
        ("暴击伤害", "crit_damage"), ("暴击率", "crit_rate"), ("生命", "health"),
        ("攻击", "attack"), ("防御", "defense"), ("倾陷", "inclination_strength"),
        ("充能", "cost_gain"), ("伤害", "damage_up"),
    ) if name in label), "")
    return ShareStat(label, value, highlighted, formal_icon or share_asset("properties", icon_key), locked)


def build_loadout_share_panel(
    request: LoadoutShareRequest, *, cancelled: Callable[[], bool] = lambda: False,
) -> LoadoutSharePanel:
    """Use this slot's pinned items, never the role's currently selected slot."""
    _check_cancel(cancelled)
    if not math.isfinite(request.score):
        raise ValueError("配装总评分格式异常")
    catalog = GameUiAssetCatalog(request.asset_root)
    highlights = ShareHighlightPolicy.from_weights(request.weights, request.main_weights)
    official_items = tuple(request.state.get("_official_items") or ())
    detail: dict[str, Any] = {}
    calculation_items: list[dict[str, Any]] = []
    if request.character_id is not None:
        with StaticGameDataDao(request.static_database_path) as dao:
            official = dao.get_character(request.character_id) is not None
            if official:
                calculation_items = project_equipment_items_to_max_level(official_items, dao)
        if official:
            detail = load_official_role_detail(
                request.user_database_path, request.character_id,
                asset_root=request.asset_root, static_database_path=request.static_database_path,
                include_inventory_contexts=False,
            )
    _check_cancel(cancelled)
    profile = detail.get("profile") or {}
    item_names = detail.get("item_names") or {}
    cultivation = tuple(
        _profile_stat(profile, key, label, suffix)
        for key, label, suffix in (
            ("character_level", "角色等级", ""),
            ("awakening_level", "觉醒", "觉"),
        )
    )
    attributes: tuple[ShareStat, ...] = ()
    if detail and official_items:
        summaries = calculate_official_role_attribute_summaries(detail, calculation_items)
        priority = {key: index for index, key in enumerate(("PanelHP", "PanelAtk", "PanelDef", "PanelCritRate", "PanelCritDamage"))}
        labels = {"PanelHP": "生命值", "PanelAtk": "攻击力", "PanelDef": "防御力"}
        attributes = tuple(_stat(labels.get(row.key, row.label), _number(row.value, row.percent),
                                 highlights.role(row.weight_property_ids),
                                 row.key)
                           for row in sorted(summaries["character"], key=lambda row: priority.get(row.key, 99))
                           if row.key != "ChargeGetEfficiencyBase")  # 展示过滤，不改公共属性汇总。
    skills = tuple(
        ShareStat(str(skill.get("display_name_zh") or skill.get("name_zh") or "技能"),
                  f"Lv.{profile['skill_levels'][str(skill['skill_id'])]}{_source_suffix(profile, 'skill_levels')}"
                  if str(skill.get("skill_id")) in (profile.get("skill_levels") or {}) else "—",
                  icon=catalog.skill_icon(str(skill.get("skill_id") or "")))
        for skill in detail.get("skills") or ()
        if str(skill.get("ability_type") or "").casefold() != "passive"
    )
    fork_id = str(profile.get("fork_id") or "")
    fork = next((row for row in detail.get("forks") or () if row.get("fork_id") == fork_id), {})
    fork_description = " · ".join(
        f"{label}{profile[key]}{_source_suffix(profile, key)}" if profile.get(key) is not None else f"{label}—"
        for key, label in (("fork_level", "Lv."), ("fork_refinement_level", "精炼 "))
    ) if fork_id else "未配置弧盘"
    fork_values = fork_panel_stats(fork, int(profile.get("fork_level") or 0),
                                  breakthrough_stage=profile.get("fork_breakthrough_stage"))
    fork_stats = tuple(_stat(
        str((detail.get("attributes", {}).get(key) or {}).get("display_name_zh") or key),
        _number(value, bool((detail.get("attributes", {}).get(key) or {}).get("show_percent"))),
        property_id=key,
    ) for key, value in fork_values.items() if value and key != "ChargeGetEfficiencyBase")
    raw_by_uid = {
        f"nte-{item['kind']}-{item.get('uid_slot')}-{item.get('uid_serial')}": item
        for item in calculation_items
    }
    source_by_uid = {
        f"nte-{item['kind']}-{item.get('uid_slot')}-{item.get('uid_serial')}": item
        for item in official_items
    }
    entries = []
    tape = request.state.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, Mapping):
        entries.append(("core", tape))
    entries.extend(("module", item) for item in request.state.get(ROLE_EQUIPPED_DRIVES) or ())
    if not entries:
        raise ValueError("此槽位尚未配置装备，请先保存配装")
    engine = None
    equipment = []
    for kind, item in entries:
        _check_cancel(cancelled)
        if not isinstance(item, Mapping):
            raise ValueError("配装装备格式异常")
        area = 15 if kind == "core" else int(request.shape_areas.get(str(item.get(EQUIP_SHAPE_ID)), 3))
        score = item.get(EQUIP_SCORE)
        if item.get("virtual"):
            score = 0.0
        elif score is None:
            if engine is None:
                engine = ScoringEngine(config_dir=request.config_dir, roles_db={})
            if kind == "core":
                score = score_tape_stats(
                    engine, main_stat_name=str(item.get(EQUIP_MAIN_STATS) or ""),
                    sub_stat_names=item.get(EQUIP_SUB_STATS) or {}, weights=request.weights,
                    quality=str(item.get(EQUIP_QUALITY) or "Gold"),
                    main_weights=request.main_weights, main_value=item.get("main_value"),
                )
            else:
                score = score_drive_stats(
                    engine, sub_stat_names=item.get(EQUIP_SUB_STATS) or {}, area=area,
                    weights=request.weights, quality=str(item.get(EQUIP_QUALITY) or "Gold"),
                )
        score = float(score)
        if not math.isfinite(score):
            raise ValueError("装备评分格式异常")
        raw = raw_by_uid.get(str(item.get(EQUIP_UID)), {})
        item_id = str(raw.get("item_id") or "")
        icon = catalog.inventory_item_icon(kind, item_id) if item_id else None
        if icon is None and item.get("item_icon_path"):
            candidate = Path(item["item_icon_path"])
            icon = candidate if candidate.is_file() else None
        main_stats = []
        if kind == "core" and item.get(EQUIP_MAIN_STATS) and item.get("main_value") is not None:
            label = str(item[EQUIP_MAIN_STATS])
            main_stats.append(_equipment_stat(label, item["main_value"], highlights.main_stat(label, core=True)))
        elif kind == "core" and raw:
            for stat in raw.get("main_stats") or ():
                property_id = str(stat.get("property_id") or "")
                label = str((detail.get("attributes", {}).get(property_id) or {}).get("display_name_zh") or property_id)
                main_stats.append(_stat(label, _number(float(stat["value"]), bool(stat.get("percent"))),
                                        highlights.main_stat(property_id, core=kind == "core"), property_id))
        # Unlock state uses the original slot snapshot, never the max-level projection.
        source = source_by_uid.get(str(item.get(EQUIP_UID)), {})
        level = source.get("level")
        locked_ids = {
            share_property_id(str(stat.get("property_id") or ""))
            for index, stat in enumerate(source.get("sub_stats") or ())
            if level is not None and index >= int(level) // 5
        }
        equipment.append(ShareEquipment(
            name=(str(item.get(EQUIP_SET_NAME) or "空幕") if kind == "core" else
                  item_names.get(item_id) or _drive_name(str(item.get(EQUIP_SHAPE_ID) or ""), request.shape_areas)),
            kind="空幕" if kind == "core" else "驱动", score=score,
            grade=str(item.get(EQUIP_GRADE) or allocation_grade(score, area)), icon=icon,
            main_stats=tuple(main_stats),
            sub_stats=tuple(_equipment_stat(str(label), value, highlights.sub_stat(str(label)),
                                           locked=share_property_id(str(label)) in locked_ids)
                            for label, value in list((item.get(EQUIP_SUB_STATS) or {}).items())[:4]),
            virtual=bool(item.get("virtual")),
        ))
    notes = ["角色面板按当前养成配置；装备主属性沿用配装页满级投影。", "模板 / 配置标签代表计算输入，不等同于游戏实测养成。"]
    if not attributes:
        notes.insert(0, "最终面板数据未齐备；本图仅展示已确认的配装与评分。")
    if any(item.virtual for item in equipment):
        notes.append("包含虚拟占位装备，已在卡片标注；其评分为 0。")
    frozen_scores = bool(request.state.get("_sqlite_assignment_scores_complete"))
    return LoadoutSharePanel(
        role_name=request.role_name, slot_name=request.slot_name, score=request.score,
        grade=loadout_total_grade(request.score), score_source="方案冻结评分" if frozen_scores else "当前基础权重评分",
        cultivation=cultivation, attributes=attributes, skills=skills,
        fork_name=str(fork.get("name_zh") or "未配置弧盘"), fork_description=fork_description,
        fork_icon=catalog.fork_icon(fork_id) if fork_id else None,
        portrait=catalog.character_art(request.character_id) if request.character_id is not None else None,
        avatar=catalog.character_icon(request.character_id) if request.character_id is not None else None,
        equipment=tuple(equipment), notes=tuple(notes),
        element_icon=share_asset("elements", str((detail.get("character") or {}).get("element_type") or "")),
        fork_stats=fork_stats, fork_level=profile.get("fork_level"),
        fork_refinement=profile.get("fork_refinement_level"),
        likeability_level=profile.get("likeability_level"),
        loadout_kind="游戏配装" if request.state.get("_game_mode") else "计算配装",
    )


def _drive_name(shape_id: str, shape_areas: Mapping[str, int]) -> str:
    """Legacy/virtual slots resolve a known shape's area, never expose its code."""
    area = len(SHAPE_FOOTPRINTS.get(shape_id, ())) or int(shape_areas.get(shape_id, 0))
    numeral = {1: "Ⅰ", 2: "Ⅱ", 3: "Ⅲ", 4: "Ⅳ", 5: "Ⅴ", 6: "Ⅵ"}.get(area)
    return f"{numeral}型驱动" if numeral else "驱动"
