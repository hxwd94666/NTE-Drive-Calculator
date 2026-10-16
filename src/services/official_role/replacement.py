# 生成并保存官方角色当前方案的单件装备替换候选。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from pathlib import Path
from math import isfinite
from typing import Any, Mapping, Callable

from src.domain.equipment.loadout_plan_scores import (
    assignment_score_key,
    exact_assignment_score_total,
)
from src.domain.recommended_weights import base_weight_character_id
from src.services.equipment.equipment_level_projection_service import project_equipment_items_to_max_level
from src.domain.equipment.virtual_equipment import is_virtual_equipment_assignment
from src.integrations.static_catalog_release import static_catalog_read_unit
from src.domain.static_catalog import StaticCatalogRelease
from src.services.official_role.inventory_contexts import load_role_replacement_detail
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.official_role.static_source import frozen_static_source
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_IS_CHANGED,
    EQUIP_UID,
)
from src.services.official_role.attributes import _role_panel_damage_inputs
from src.services.official_role.marginal_scoring import calculate_official_role_final_weights, calculate_official_role_hidden_equipment_score, calculate_official_role_margins, prefetch_official_role_hidden_equipment_scores

from src.services.official_role.attributes import _context_calculation_items, _total_direct_damage

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
    *, static_database_path: str | Path, static_schema_version: int,
    static_release: StaticCatalogRelease,
) -> list[dict[str, Any]]:
    """Rank same-slot SQLite inventory replacements by final-weight score.

    Replacement is intentionally limited to a saved SQLite loadout: it retains
    the stored grid coordinates and never writes an ad-hoc JSON equipment list.
    A module must keep both its official shape and suit, so applying the result
    cannot silently invalidate the saved board or set constraint.
    """

    static_database_path, static_schema_version = frozen_static_source(
        detail, static_database_path=static_database_path, static_schema_version=static_schema_version,
        static_release=static_release,
    )
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    if not (context_key == "saved" or context_key.startswith("saved:")) or not context.get("plan"):
        return []
    if bool((context.get("plan") or {}).get("allocation_locked")):
        return []
    return replacement_candidates_for_frozen_context(
        detail, context_key, target,
        static_database_path=static_database_path, static_schema_version=static_schema_version,
        static_release=static_release,
    )


def replacement_candidates_for_frozen_context(
    detail: Mapping[str, Any],
    context_key: str,
    target: Mapping[str, Any],
    *, static_database_path: str | Path, static_schema_version: int,
    static_release: StaticCatalogRelease,
) -> list[dict[str, Any]]:
    """Rank compatible replacements without assuming or mutating a saved plan."""

    static_database_path, static_schema_version = frozen_static_source(
        detail, static_database_path=static_database_path, static_schema_version=static_schema_version,
        static_release=static_release,
    )
    with static_catalog_read_unit(static_database_path, expected_schema_version=static_schema_version,
                                  expected_release=static_release) as read_unit:
        values = _project_replacement_values(detail, context_key, target, read_unit)
    return _rank_replacement_values(detail, context_key, target, values)


def prepare_official_role_replacement(
    user_database_path, asset_root, detail, context_key, target, *,
    static_database_path, static_schema_version, static_release,
):
    """lazy补池与曲线投影共用一次hash，纯排序在SQLite/pin物理关闭后执行。"""
    frozen_static_source(detail, static_database_path=static_database_path,
                         static_schema_version=static_schema_version, static_release=static_release)
    with static_catalog_read_unit(static_database_path, expected_schema_version=static_schema_version,
                                  expected_release=static_release) as read_unit:
        enriched = load_role_replacement_detail(
            user_database_path, static_database_path, asset_root, detail, context_key,
            static_schema_version=static_schema_version, static_release=static_release, static_read_unit=read_unit,
        )
        context = (enriched.get("equipment_contexts") or {}).get(context_key) or {}
        values = (None if bool((context.get("plan") or {}).get("allocation_locked"))
                  else _project_replacement_values(enriched, context_key, target, read_unit))
    return enriched, _rank_replacement_values(enriched, context_key, target, values)



def prepare_saved_plan_replacement(user_database_path, *, static_database_path, asset_root,
                                   shared_database_path, role_name, item_kind, uid, plan_id=None):
    """配装点击只在一个owner内加载详情和投影，不先load_detail再二次hash。"""
    from src.services.official_role.page import load_official_role_detail
    with UserDataDao(user_database_path) as user_dao:
        plan = (user_dao.get_loadout_plan(int(plan_id)) if plan_id is not None
                else user_dao.get_active_loadout_plan_for_role(role_name))
    if not isinstance(plan, dict) or plan.get("character_id") is None:
        return None
    if plan.get("allocation_locked"):
        return {"locked": True}
    with static_catalog_read_unit(static_database_path) as read_unit:
        detail = load_official_role_detail(
            user_database_path, int(plan["character_id"]), static_database_path=static_database_path,
            asset_root=asset_root, shared_database_path=shared_database_path, static_read_unit=read_unit,
        )
        contexts = detail.get("equipment_contexts") or {}
        context_key = next((key for key, context in contexts.items()
                            if int((context.get("plan") or {}).get("plan_id") or 0)
                            == int(plan.get("plan_id") or 0)), None)
        if context_key is None:
            return None
        kind = "module" if item_kind == "drive" else "core"
        target = next((item for item in contexts[context_key].get("items") or ()
                       if str(item.get("kind") or "") == kind
                       and f"nte-{kind}-{item.get('uid_slot')}-{item.get('uid_serial')}" == str(uid)), None)
        if target is None:
            return None
        values = _project_replacement_values(detail, context_key, target, read_unit)
    # 排序独立于SQLite和拒写pin，弹窗只接收冻结纯值。
    return {"detail": detail, "context_key": context_key, "target": dict(target),
            "candidates": _rank_replacement_values(detail, context_key, target, values)}


def _project_replacement_values(detail, context_key, target, read_unit):
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    raw_items = tuple(context.get("items") or ())
    if not raw_items:
        return None
    target_kind = str(target.get("kind") or "")
    target_virtual = bool(target.get("virtual"))
    target_geometry = str(target.get("geometry") or "")
    target_suit = target.get("suit_id")
    eligible = []
    for candidate in context.get("replacement_items") or detail.get("replacement_items") or ():
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
        return None

    # 满级投影只能打开该次冻结来源，不能由环境解析另一个默认库。
    static_dao = read_unit.dao
    projected = project_equipment_items_to_max_level(
        (*raw_items, *eligible), static_dao,
    )
    return context, raw_items, projected


def _rank_replacement_values(detail, context_key, target, values):
    if values is None:
        return []
    context, raw_items, projected = values
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
    prefetch_official_role_hidden_equipment_scores(full_level_detail, [projected_target, *projected_candidates],
        property_weights=final_weights["property_weights"], main_property_weights=final_weights["main_property_weights"])
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
            "current_items": tuple(dict(item) for item in items),
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
    context_key: str = "saved",
    replacement_score: float | None = None,
    current_assignment_scores: Mapping[str, float] | None = None,
    checkpoint: Callable[[], None] | None = None,
    commit_context=None, expected_account_id: str | None = None,
    postcommit_diagnostic: Callable[[str], None] | None = None,
) -> int:
    """Persist one accepted saved-plan replacement as the next active plan."""

    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    plan = context.get("plan")
    if not isinstance(plan, Mapping) or plan.get("source_snapshot_id") is None:
        raise ValueError("请先保存一套配装方案，再使用替换优化")
    original_assignments = []
    assignments = []
    replaced = False
    replacement_uid = (
        int(replacement.get("uid_serial") or 0), int(replacement.get("uid_slot") or 0)
    )
    for source in plan.get("assignments") or ():
        original_assignment = dict(source.get("raw_assignment") or source)
        original_assignments.append(original_assignment)
        assignment = dict(original_assignment)
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
        raise ValueError("目标装备不属于当前配装方案")
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
    if current_assignment_scores is None or replacement_score is None:
        raise ValueError("替换保存需要完整的当前基础评分，请重新打开替换优化后重试")
    # 新方案整体采用本次基础口径，原方案及其历史冻结分保持不变。
    assignment_scores = {}
    for row in original_assignments:
        key = assignment_score_key(row)
        if is_virtual_equipment_assignment(row):
            assignment_scores[key] = 0.0
        elif key in current_assignment_scores:
            assignment_scores[key] = float(current_assignment_scores[key])
        else:
            raise ValueError("当前基础评分不完整，请重新打开替换优化后重试")
    assignment_scores.pop(target_display_uid)
    assignment_scores[replacement_display_uid] = float(replacement_score)
    if any(not isfinite(score) or score < 0 for score in assignment_scores.values()):
        raise ValueError("当前基础评分数值异常，请重新打开替换优化后重试")
    saved_score = exact_assignment_score_total(assignments, assignment_scores)
    if saved_score is None:
        raise ValueError("替换方案评分不完整，请重新打开替换优化后重试")
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
    payload["base_scoring"] = {
        "weight_source_character_id": base_weight_character_id(int(plan["character_id"])),
        "property_weights": dict(detail.get("property_weights") or {}),
        "main_property_weights": dict(detail.get("main_property_weights") or {}),
    }
    role_name = str((detail.get("character") or {}).get("name_zh") or plan["character_id"])
    # 保存只消费已物化分数；在原提交位置复核当前release，不往payload写token。
    path, schema = frozen_static_source(
        detail, static_database_path=detail.get("static_database_path"),
        static_schema_version=detail.get("static_schema_version"), static_release=detail.get("static_release"),
    )
    if checkpoint is not None:
        checkpoint()
    saved_plan_id = None
    try:
        with (static_catalog_read_unit(path, expected_schema_version=schema,
                                      expected_release=detail["static_release"]),
              UserDataDao(user_database_path) as user_dao):
            if expected_account_id is not None and user_dao.profile()["account_id"] != expected_account_id:
                raise ValueError("保存数据库不属于本次冻结账号")
            if checkpoint is not None:
                checkpoint()
            save_kwargs = {
                "name": f"替换优化：{role_name}", "assignments": assignments,
                "source_snapshot_id": int(plan["source_snapshot_id"]),
                "status": "incomplete" if any(is_virtual_equipment_assignment(row) for row in assignments) else "saved",
                "score": saved_score, "payload": payload,
            }
            slot_id = context.get("slot_id") or plan.get("slot_id")
            if slot_id is not None:
                saved_plan_id = int(user_dao.save_replacement_plan_to_slot(
                    int(slot_id), **save_kwargs, expected_plan_id=int(plan["plan_id"]),
                    checkpoint=checkpoint, commit_context=commit_context,
                ))
            else:
                # 带任务gate的正式GUI选择必须有稳定slot；缺失不写入旧活动方案分支。
                if checkpoint is not None or commit_context is not None:
                    raise ValueError("所选方案缺少稳定配装槽位，请刷新后重试")
                saved_plan_id = user_dao.replace_active_loadout_plans([{
                    **save_kwargs, "character_id": int(plan["character_id"]),
                }])[0]
    except Exception as exc:
        if saved_plan_id is None:
            raise
        # read_unit退出还会校验manifest；真实commit后收尾诊断不冒充事务失败。
        if postcommit_diagnostic is not None:
            postcommit_diagnostic(str(exc))
    return int(saved_plan_id)
