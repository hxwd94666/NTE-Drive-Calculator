# 冻结词条配装结果与各已保存槽位之间的装备差异。
"""Qt-free saved-slot comparison projections for weighted allocation results."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Sequence

from src.domain.equipment.scoring.allocation_rating import allocation_grade
from src.optimizer.scoring import ScoringEngine
from src.services.allocation.results.comparison_scoring import (
    FrozenComparisonScorer,
    freeze_comparison_scorers,
    score_comparison_batches,
    persist_comparison_diff,
)
from src.services.allocation.inputs.inventory import legacy_stat_name
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
)
from src.domain.equipment.loadouts.virtual_equipment import (
    grid_count_from_geometry,
    is_virtual_equipment_assignment,
    normalized_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.domain.equipment.geometry.shapes import OFFICIAL_SHAPE_LABELS


@dataclass(frozen=True, slots=True)
class WeightedLoadoutComparison:
    slot_id: int
    slot_name: str
    slot_key: str
    old_items: tuple[dict[str, Any], ...]
    diff: Mapping[str, Any]
    scorer: FrozenComparisonScorer | None = field(default=None, repr=False, compare=False)


def _uid(kind: str, slot: int, serial: int) -> str:
    prefix = "module" if kind == "module" else "core"
    return f"nte-{prefix}-{int(slot)}-{int(serial)}"


def _quality(value: Any) -> str:
    return {
        "orange": "Gold",
        "gold": "Gold",
        "purple": "Purple",
        "blue": "Blue",
    }.get(str(value or "Gold").casefold(), str(value or "Gold"))


def _shape_key(value: Any) -> str:
    return str(value or "").removeprefix("EquipmentGeometry_").casefold()


def _shape_name(value: Any, shape_names: Mapping[str, str]) -> str:
    geometry = str(value or "")
    normalized_names = {
        _shape_key(shape_id): str(label)
        for shape_id, label in {
            **OFFICIAL_SHAPE_LABELS,
            **dict(shape_names),
        }.items()
    }
    return normalized_names.get(
        _shape_key(geometry),
        geometry.removeprefix("EquipmentGeometry_") or "驱动",
    )


def _stat_map(stats: Any, labels: Mapping[str, str]) -> dict[str, float]:
    return {
        labels.get(str(stat.get("property_id") or ""), str(stat.get("property_id") or "")):
        float(stat.get("value") or 0.0) * (100.0 if stat.get("percent") else 1.0)
        for stat in stats or ()
        if str(stat.get("property_id") or "")
    }


def _old_item_snapshot(
    item: Mapping[str, Any],
    assignment: Mapping[str, Any],
    *,
    score: float | None,
    labels: Mapping[str, str],
    suit_names: Mapping[str, str],
    shape_names: Mapping[str, str],
) -> dict[str, Any]:
    kind = str(item.get("kind") or assignment.get("kind") or "")
    area = (
        15
        if kind == "core"
        else int(item.get("grid_count") or 0)
        or grid_count_from_geometry(item.get("geometry"))
    )
    quality = _quality(item.get("quality"))
    uid = _uid(kind, assignment["uid_slot"], assignment["uid_serial"])
    common = {
        EQUIP_UID: uid,
        EQUIP_TYPE: "tape" if kind == "core" else "drive",
        EQUIP_QUALITY: quality,
        EQUIP_SCORE_AREA: area,
        EQUIP_AREA: area,
        EQUIP_SUB_STATS: _stat_map(item.get("sub_stats"), labels),
        "virtual": bool(item.get("virtual")),
    }
    if score is not None:
        common[EQUIP_SCORE] = float(score)
        common[EQUIP_GRADE] = allocation_grade(float(score), area) if area else "D"
    else:
        common["comparison_score_unavailable"] = "评分资料不足"
    if kind == "core":
        main_stats = _stat_map(item.get("main_stats"), labels)
        main_name = next(iter(main_stats), "未知主词条")
        set_name = suit_names.get(str(item.get("suit_id") or ""), str(item.get("suit_id") or "空空幕"))
        return {
            **common,
            EQUIP_SET_NAME: set_name,
            EQUIP_MAIN_STATS: main_name,
            EQUIP_DISPLAY_NAME: f"{set_name}-{main_name}",
        }
    shape_name = _shape_name(item.get("geometry"), shape_names)
    return {
        **common,
        EQUIP_SHAPE_ID: shape_name,
        EQUIP_DISPLAY_NAME: shape_name,
    }


def _new_item_snapshots(context: Any, option: Any, labels: Mapping[str, str], suit_names: Mapping[str, str], shape_names: Mapping[str, str]) -> tuple[dict[str, Any], ...]:
    candidates = {candidate.uid: candidate for candidate in context.candidates}
    rows = []
    for assignment in option.assignments:
        candidate = candidates.get(assignment.uid)
        kind = str(assignment.kind)
        area = 15 if kind == "core" else int(assignment.grid_count or getattr(candidate, "grid_count", 0) or 0)
        quality = _quality(getattr(candidate, "quality", "orange"))
        sub_stats = {
            labels.get(str(stat.property_id), str(stat.property_id)):
            float(stat.value) * (100.0 if stat.percent else 1.0)
            for stat in (getattr(candidate, "sub_stats", ()) or ())
        }
        common = {
            EQUIP_UID: _uid(kind, assignment.uid[0], assignment.uid[1]),
            EQUIP_TYPE: "tape" if kind == "core" else "drive",
            EQUIP_QUALITY: quality,
            EQUIP_SCORE: float(assignment.score),
            EQUIP_SCORE_AREA: area,
            EQUIP_AREA: area,
            EQUIP_GRADE: allocation_grade(float(assignment.score), area) if area else "D",
            EQUIP_SUB_STATS: sub_stats,
            "virtual": bool(getattr(assignment, "virtual", False)),
        }
        if kind == "core":
            main = next(iter(getattr(candidate, "main_stats", ()) or ()), None)
            main_name = labels.get(str(getattr(main, "property_id", "") or ""), str(getattr(main, "property_id", "") or "未知主词条"))
            set_id = str(getattr(candidate, "suit_id", None) or assignment.suit_id or "")
            set_name = suit_names.get(set_id, set_id or "空空幕")
            main_value = float(main.value) * (100 if main.percent else 1) if main is not None else None
            rows.append({**common, EQUIP_SET_NAME: set_name, EQUIP_MAIN_STATS: main_name,
                         "main_value": main_value, EQUIP_DISPLAY_NAME: f"{set_name}-{main_name}"})
        else:
            shape_name = _shape_name(assignment.geometry, shape_names)
            rows.append({**common, EQUIP_SHAPE_ID: shape_name, EQUIP_DISPLAY_NAME: shape_name})
    return tuple(rows)


def _diff(old_items: Sequence[Mapping[str, Any]], new_items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    old = {str(item[EQUIP_UID]): dict(item) for item in old_items}
    new = {str(item[EQUIP_UID]): dict(item) for item in new_items}
    added = set(new) - set(old)
    removed = set(old) - set(new)
    return {
        DIFF_CHANGED: bool(old and (added or removed)),
        DIFF_ADDED_UIDS: added if old else set(),
        DIFF_ADDED: tuple(new[uid] for uid in new if uid in added) if old else (),
        DIFF_REMOVED: tuple(old[uid] for uid in old if uid in removed),
    }


def freeze_role_loadout_comparisons(user_dao, static_dao, character_id, scorer, *, checkpoint=lambda: None, selected_slot_id=None, slots=None):
    """单角色公开入口仍消费同一批处理，不保留另一套评分实现。"""
    selected = list(user_dao.list_loadout_slots(int(character_id))) if slots is None else list(slots)
    selected = [slot for slot in selected if selected_slot_id is None or int(slot["slot_id"]) == int(selected_slot_id)]
    if not selected:
        return ()
    return freeze_loadout_comparison_batches(
        user_dao, static_dao, [(scorer, selected)], checkpoint=checkpoint,
    )[0]


def freeze_loadout_comparison_batches(user_dao, static_dao, batches, *, checkpoint=lambda: None):
    """按方案自身来源批量取件，再跨角色合批调用公共 Rust 评分。"""
    batches = list(batches)
    if not batches or not any(slots for _, slots in batches):
        return [() for _ in batches]
    checkpoint()
    suits = {str(row["suit_id"]): str(row.get("name_zh") or row["suit_id"]) for row in static_dao.list_suits()}
    shapes = {
        str(row["shape_id"]): str(row.get("legacy_shape_id") or row.get("legacy_label") or row["shape_id"]).removeprefix("EquipmentGeometry_")
        for row in static_dao.list_shapes()
    }
    source_uids = {}
    for _scorer, slots in batches:
        for slot in slots:
            checkpoint()
            plan = slot.get("current_plan") or {}
            source_id = plan.get("source_snapshot_id")
            if source_id is not None:
                requested = source_uids.setdefault(source_id, set())
                requested.update((int(row["uid_serial"]), int(row["uid_slot"]))
                                 for row in plan.get("assignments") or ()
                                 if not is_virtual_equipment_assignment(normalized_equipment_assignment(row)))
    inventories = {}
    for source_id, uids in source_uids.items():
        checkpoint()
        inventories[source_id] = {
            (int(row["uid_serial"]), int(row["uid_slot"])): row
            for row in (user_dao.list_inventory_items(int(source_id), uids=uids) if uids else ())
        }
    prepared_batches, score_batches = [], []
    for scorer, slots in batches:
        prepared_slots, score_entries = [], {}
        for slot in slots:
            checkpoint()
            plan = slot.get("current_plan") or {}
            assignments = tuple(plan.get("assignments") or ())
            source_id = plan.get("source_snapshot_id")
            inventory = inventories.get(source_id, {})
            main_values = (plan.get("payload") or {}).get("tape_main_values") or {}
            prepared_slots.append((slot, plan, assignments, inventory, main_values))
            for assignment in assignments:
                checkpoint()
                normalized = normalized_equipment_assignment(assignment)
                item = virtual_equipment_inventory_item(normalized) if is_virtual_equipment_assignment(normalized) else inventory.get((int(assignment["uid_serial"]), int(assignment["uid_slot"])))
                uid = _uid(str(assignment.get("kind") or ""), assignment["uid_slot"], assignment["uid_serial"])
                if item is not None:
                    score_entries.setdefault((source_id, uid, main_values.get(uid)), (item, main_values.get(uid)))
        prepared_batches.append((scorer, prepared_slots, score_entries))
        score_batches.append((scorer, tuple(score_entries.values())))
    all_scores = score_comparison_batches(score_batches, checkpoint=checkpoint)
    results = []
    for (scorer, prepared_slots, score_entries), scores in zip(prepared_batches, all_scores):
        if not prepared_slots:
            results.append(())
            continue
        attributes = dict(scorer.names)
        scoring_metadata = scorer.metadata()
        score_cache = dict(zip(score_entries, scores))
        comparisons = []
        for slot, plan, assignments, inventory, main_values in prepared_slots:
            source_id = plan.get("source_snapshot_id")
            old_items = []
            for assignment in assignments:
                normalized = normalized_equipment_assignment(assignment)
                item = virtual_equipment_inventory_item(normalized) if is_virtual_equipment_assignment(normalized) else inventory.get((int(assignment["uid_serial"]), int(assignment["uid_slot"])))
                checkpoint()
                uid = _uid(str(assignment.get("kind") or ""), assignment["uid_slot"], assignment["uid_serial"])
                score_key = (source_id, uid, main_values.get(uid))
                score = score_cache.get(score_key)
                snapshot = _old_item_snapshot(item or normalized, assignment, score=score,
                                              labels=attributes, suit_names=suits, shape_names=shapes)
                if snapshot.get(EQUIP_TYPE) == "tape":
                    value = main_values.get(uid)
                    if value is None:
                        base = scorer.engine.stat_catalog.tape_main_values.get(snapshot.get(EQUIP_MAIN_STATS))
                        value = float(base) * scorer.engine.quality_map.get(snapshot.get(EQUIP_QUALITY), 1) if base is not None else None
                    snapshot["main_value"] = value
                old_items.append(snapshot)
            comparisons.append(WeightedLoadoutComparison(
                slot_id=int(slot["slot_id"]),
                slot_name=str(slot.get("slot_name") or slot["slot_id"]),
                slot_key=str(slot.get("slot_key") or ""),
                old_items=tuple(old_items),
                scorer=scorer,
                diff=dict(_diff(old_items, ()), comparison_version=1, score_basis="calculation_weights",
                          scoring=scoring_metadata, baseline_plan_id=plan.get("plan_id"),
                          baseline_snapshot_id=source_id, baseline_slot_id=int(slot["slot_id"])),
            ))
        results.append(tuple(comparisons))
    checkpoint()
    return results


def freeze_weighted_loadout_comparisons(user_dao: Any, static_dao: Any, context: Any, options: Sequence[Any], *, checkpoint=lambda: None, slot_ids_by_character=None, static_release=None) -> dict[int, tuple[WeightedLoadoutComparison, ...]]:
    """Freeze the solver's base-weight policy, including blacklist normalization."""
    checkpoint()
    engine = ScoringEngine(stat_catalog=context.scoring_rules.to_stat_catalog(), roles_db={})
    attributes = static_dao.list_equipment_attributes()
    names = {row.property_id: legacy_stat_name(row.property_id) or row.scoring_name
             for row in context.attributes}
    requests = []
    for role in context.roles:
        checkpoint()
        slots = list(user_dao.list_loadout_slots(int(role.character_id)))
        selected_slot = (slot_ids_by_character[int(role.character_id)]
                         if slot_ids_by_character is not None else None)
        if selected_slot is not None:
            slots = [slot for slot in slots if int(slot["slot_id"]) == int(selected_slot)]
        zeros = (tuple(names.get(pid, pid) for pid in role.substat_blacklist)
                 if role.blacklist_zero_weight and context.allocation_strategy == "role_priority" else ())
        requests.append((role, slots, dict(
            character_id=role.character_id,
            weights={names.get(pid, pid): weight for pid, weight in role.effective_property_weights},
            main_weights={names.get(pid, pid): weight for pid, weight in role.effective_main_property_weights},
            zero_weight_stats=zeros,
        )))
    selected = [(role, slots, spec) for role, slots, spec in requests if slots]
    scorers = freeze_comparison_scorers(engine, [spec for _, _, spec in selected],
        attributes=attributes, dataset_id=context.static_dataset.dataset_id,
        static_release=static_release, checkpoint=checkpoint)
    frozen = freeze_loadout_comparison_batches(user_dao, static_dao,
        [(scorer, slots) for scorer, (_, slots, _) in zip(scorers, selected)], checkpoint=checkpoint)
    rows = {int(role.character_id): () for role, _, _ in requests}
    for (role, _, _), comparisons in zip(selected, frozen):
        rows[int(role.character_id)] = comparisons
        for row in comparisons:
            row.diff["calculation_snapshot_id"] = context.snapshot.snapshot_id
    return refresh_weighted_loadout_comparisons(rows, context, options) if options else rows


def select_frozen_comparison(rows, slot_id):
    if slot_id is None:
        return persist_comparison_diff(dict(comparison_version=1, score_basis="calculation_weights",
                                            baseline_slot_id=None, baseline_plan_id=None, baseline_snapshot_id=None))
    row = next((row for row in rows if row.slot_id == int(slot_id)), None)
    if row is None:
        raise RuntimeError("目标配装槽位已改变，请重新计算。")
    return persist_comparison_diff(row.diff)


def refresh_weighted_loadout_comparisons(comparisons: Mapping[int, Sequence[WeightedLoadoutComparison]], context: Any, options: Sequence[Any]) -> dict[int, tuple[WeightedLoadoutComparison, ...]]:
    """Rebuild diffs after an in-preview manual replacement."""

    option_map = {int(option.character_id): option for option in options}
    labels = {str(row.property_id): legacy_stat_name(row.property_id) or str(row.scoring_name)
              for row in getattr(context, "attributes", ())}
    result = {}
    for character_id, rows in comparisons.items():
        option = option_map.get(int(character_id))
        if option is None:
            continue
        new_items = _new_item_snapshots(context, option, labels, {}, {})
        result[int(character_id)] = tuple(replace(row, diff={**row.diff, **_diff(
            row.old_items,
            tuple(row.scorer.verify_result_score(dict(item)) for item in new_items) if row.scorer else new_items,
        )}) for row in rows)
    return result
