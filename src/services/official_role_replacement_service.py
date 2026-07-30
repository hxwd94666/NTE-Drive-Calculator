# 生成并保存官方角色当前方案的单件装备替换候选。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_UID,
)
from src.services.official_role_attribute_service import _role_panel_damage_inputs
from src.services.official_role_scoring_service import (
    calculate_official_role_final_weights,
    calculate_official_role_hidden_equipment_score,
    calculate_official_role_margins,
)

from src.services.official_role_attribute_service import (
    _context_calculation_items,
    _total_direct_damage,
)

def _same_inventory_item(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_uid = left.get("uid") or {}
    right_uid = right.get("uid") or {}
    if left_uid and right_uid and isinstance(left_uid, Mapping) and isinstance(right_uid, Mapping):
        return (
            int(left_uid.get("serial") or 0), int(left_uid.get("slot") or 0)
        ) == (
            int(right_uid.get("serial") or 0), int(right_uid.get("slot") or 0)
        )
    if all(key in left for key in ("uid_serial", "uid_slot")) and all(
        key in right for key in ("uid_serial", "uid_slot")
    ):
        return (
            int(left.get("uid_serial") or 0), int(left.get("uid_slot") or 0)
        ) == (
            int(right.get("uid_serial") or 0), int(right.get("uid_slot") or 0)
        )
    return left is right


def calculate_official_role_item_gain(
    detail: Mapping[str, Any], context_key: str, item: Mapping[str, Any],
) -> dict[str, float] | None:
    """Measure one core/module by removing it from the same frozen equipment context."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    remaining = tuple(
        candidate
        for candidate in _context_calculation_items(context)
        if not _same_inventory_item(candidate, item)
    )
    baseline_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: {
                **context,
                "items": remaining,
                "calculation_items": remaining,
            },
        },
    }
    baseline_inputs = _role_panel_damage_inputs(baseline_detail, context_key)
    if not baseline_inputs:
        return None
    damage = _total_direct_damage(inputs)
    baseline_damage = _total_direct_damage(baseline_inputs)
    if baseline_damage <= 0:
        return None
    return {
        "damage": damage,
        "baseline_damage": baseline_damage,
        "gain_percent": (damage / baseline_damage - 1.0) * 100.0,
    }


def replacement_candidates_for_official_role(
    detail: Mapping[str, Any], context_key: str, target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Rank same-slot SQLite inventory replacements by final-weight score.

    Replacement is intentionally limited to a saved SQLite loadout: it retains
    the stored grid coordinates and never writes an ad-hoc JSON equipment list.
    A module must keep both its official shape and suit, so applying the result
    cannot silently invalidate the saved board or set constraint.
    """

    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    if context_key != "saved" or not context.get("plan"):
        return []
    if bool((context.get("plan") or {}).get("allocation_locked")):
        return []
    raw_items = tuple(context.get("items") or ())
    if not raw_items:
        return []
    target_kind = str(target.get("kind") or "")
    target_virtual = bool(target.get("virtual"))
    target_geometry = str(target.get("geometry") or "")
    target_suit = target.get("suit_id")
    eligible = []
    for candidate in detail.get("replacement_items") or ():
        if candidate.get("allocation_reserved"):
            continue
        if _same_inventory_item(candidate, target):
            continue
        if str(candidate.get("kind") or "") != target_kind:
            continue
        if any(_same_inventory_item(candidate, equipped) for equipped in raw_items):
            continue
        if target_kind == "module" and (
            str(candidate.get("geometry") or "") != target_geometry
            or (
                not target_virtual
                and candidate.get("suit_id") != target_suit
            )
        ):
            continue
        if (
            target_kind == "core"
            and not target_virtual
            and candidate.get("suit_id") != target_suit
        ):
            continue
        eligible.append(candidate)
    if not eligible:
        return []

    with StaticGameDataDao() as static_dao:
        projected = project_equipment_items_to_max_level(
            (*raw_items, *eligible),
            static_dao,
        )
    items = tuple(projected[:len(raw_items)])
    projected_candidates = projected[len(raw_items):]
    projected_target = next(
        (item for item in items if _same_inventory_item(item, target)),
        dict(target),
    )
    full_level_context = {
        **context,
        "items": items,
        "calculation_items": items,
    }
    full_level_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: full_level_context,
        },
    }
    baseline = calculate_official_role_margins(full_level_detail, context_key)
    baseline_damage = float((baseline or {}).get("damage") or 0.0)
    final_weights = calculate_official_role_final_weights(
        full_level_detail,
        context_key,
        margins=baseline,
        base_property_weights=detail.get("property_weights") or {},
        base_main_property_weights=detail.get("main_property_weights") or {},
    )
    current_gain = calculate_official_role_item_gain(
        full_level_detail,
        context_key,
        projected_target,
    )
    current_direct_damage_score = (
        float(current_gain["gain_percent"]) if current_gain else None
    )
    current_score = calculate_official_role_hidden_equipment_score(
        full_level_detail,
        projected_target,
        property_weights=final_weights["property_weights"],
        main_property_weights=final_weights["main_property_weights"],
    )
    ranked: list[dict[str, Any]] = []
    for candidate in projected_candidates:
        replaced = tuple(
            candidate if _same_inventory_item(item, projected_target) else item
            for item in items
        )
        candidate_detail = {
            **full_level_detail,
            "equipment_contexts": {
                **(full_level_detail.get("equipment_contexts") or {}),
                context_key: {
                    **full_level_context,
                    "items": replaced,
                    "calculation_items": replaced,
                },
            },
        }
        margins = calculate_official_role_margins(candidate_detail, context_key)
        damage = float((margins or {}).get("damage") or 0.0)
        candidate_gain = calculate_official_role_item_gain(
            candidate_detail,
            context_key,
            candidate,
        )
        ranked.append({
            "item": dict(candidate),
            "current_item": dict(projected_target),
            "baseline_damage": baseline_damage,
            "damage": damage,
            "current_direct_damage_score": current_direct_damage_score,
            "current_score": current_score,
            "score": calculate_official_role_hidden_equipment_score(
                candidate_detail,
                candidate,
                property_weights=final_weights["property_weights"],
                main_property_weights=final_weights["main_property_weights"],
            ),
            "direct_damage_score": (
                float(candidate_gain["gain_percent"]) if candidate_gain else None
            ),
            "gain_percent": (
                (damage / baseline_damage - 1.0) * 100.0 if baseline_damage > 0 else 0.0
            ),
        })
    return sorted(
        ranked,
        key=lambda row: (
            -float(row["score"]),
            -float(row.get("damage") or 0.0),
        ),
    )


def save_official_role_replacement(
    user_database_path: str | Path,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    replacement_score: float | None = None,
    current_score: float | None = None,
) -> int:
    """Persist one accepted saved-plan replacement as the next active plan."""

    context = (detail.get("equipment_contexts") or {}).get("saved") or {}
    plan = context.get("plan")
    if not isinstance(plan, Mapping) or plan.get("source_snapshot_id") is None:
        raise ValueError("请先保存一套 SQLite 配装方案，再使用替换优化")
    assignments = []
    replaced = False
    replacement_uid = (
        int(replacement.get("uid_serial") or 0), int(replacement.get("uid_slot") or 0)
    )
    for source in plan.get("assignments") or ():
        assignment = dict(source.get("raw_assignment") or source)
        source_uid = (int(source.get("uid_serial") or 0), int(source.get("uid_slot") or 0))
        target_uid = (int(target.get("uid_serial") or 0), int(target.get("uid_slot") or 0))
        if source_uid == target_uid:
            assignment.pop("virtual", None)
            assignment.pop("virtual_equipment", None)
            assignment.update({
                "uid_serial": replacement_uid[0],
                "uid_slot": replacement_uid[1],
                "kind": str(replacement.get("kind") or ""),
                "geometry": replacement.get("geometry"),
                "grid_count": replacement.get("grid_count"),
            })
            replaced = True
        assignments.append(assignment)
    if not replaced:
        raise ValueError("目标装备不属于当前 SQLite 配装方案")
    if len({(int(row.get("uid_serial") or 0), int(row.get("uid_slot") or 0)) for row in assignments}) != len(assignments):
        raise ValueError("替换装备已在当前方案中使用")
    target_kind = str(target.get("kind") or "")
    replacement_kind = str(replacement.get("kind") or "")
    target_display_uid = (
        f"nte-{target_kind}-{target.get('uid_slot')}-{target.get('uid_serial')}"
    )
    replacement_display_uid = (
        f"nte-{replacement_kind}-{replacement.get('uid_slot')}-{replacement.get('uid_serial')}"
    )
    payload = dict(plan.get("payload") or {})
    assignment_scores = dict(payload.get("assignment_scores") or {})
    previous_assignment_score = assignment_scores.pop(target_display_uid, None)
    if replacement_score is not None:
        assignment_scores[replacement_display_uid] = float(replacement_score)
    if previous_assignment_score is None:
        previous_assignment_score = current_score
    plan_score = plan.get("score")
    if (
        plan_score is not None
        and replacement_score is not None
        and previous_assignment_score is not None
    ):
        saved_score: float | None = (
            float(plan_score)
            - float(previous_assignment_score)
            + float(replacement_score)
        )
    else:
        # Do not ever write direct damage into the equipment-score column.
        # Retaining the prior verified score is safer than inventing a total.
        saved_score = float(plan_score) if plan_score is not None else None
    payload.update({
        "source": "official_role_replacement",
        "replaces_plan_id": plan.get("plan_id"),
        # Replacement is a change to an existing plan, never a new acquisition.
        # Keep the same display state in the SQLite saved-plan card and diff view.
        "changed_uids": [replacement_display_uid],
        "last_diff": {
            DIFF_CHANGED: True,
            DIFF_ADDED_UIDS: [replacement_display_uid],
            DIFF_ADDED: [{
                EQUIP_UID: replacement_display_uid,
                EQUIP_IS_CHANGED: True,
            }],
            DIFF_REMOVED: [{EQUIP_UID: target_display_uid}],
        },
    })
    if assignment_scores:
        payload["assignment_scores"] = assignment_scores
    role_name = str((detail.get("character") or {}).get("name_zh") or plan["character_id"])
    with UserDataDao(user_database_path) as user_dao:
        saved_plan_ids = user_dao.replace_active_loadout_plans([{
            "name": f"替换优化：{role_name}",
            "character_id": int(plan["character_id"]),
            "source_snapshot_id": int(plan["source_snapshot_id"]),
            "assignments": assignments,
            "status": (
                "incomplete"
                if any(is_virtual_equipment_assignment(row) for row in assignments)
                else "saved"
            ),
            "score": saved_score,
            "payload": payload,
        }])
    return saved_plan_ids[0]
