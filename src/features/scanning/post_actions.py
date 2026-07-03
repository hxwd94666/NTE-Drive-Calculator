# 管理全量扫描后的弃置和锁定状态同步规则。
"""Post-scan equipment state rules used by full inventory scans."""

from __future__ import annotations

import copy
from typing import Any

from src.models.equipment import BaseEquipment, Drive, Tape
from src.optimizer.scoring import ScoringEngine


GRADE_ORDER = ["ACE", "SSS", "SS", "S", "A", "B", "C", "D"]
EQUIPMENT_STATES = {"normal", "locked", "discarded"}
DEFAULT_EXCLUDED_SHAPE_IDS = {"H_2", "V_2"}
DEFAULT_EXCLUDED_SET_NAMES = {"音速蓝刺猬", "音速索尼克"}

DEFAULT_POST_ACTION_CONFIG: dict[str, Any] = {
    "discard": {
        "enabled": False,
        "grade": "S",
        "role_scope": "all",
        "quality_scope": "gold_purple",
        "type_scope": "all",
        "shape_ids": None,
        "set_names": None,
        "on_locked": "skip",
        "on_discarded": "normal",
    },
    "lock": {
        "enabled": False,
        "grade": "SSS",
        "role_scope": "all",
        "quality_scope": "gold_purple",
        "type_scope": "all",
        "shape_ids": None,
        "set_names": None,
        "on_locked": "skip",
        "on_discarded": "normal",
    },
}


def default_post_action_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_POST_ACTION_CONFIG)


def merge_post_action_config(raw: dict | None) -> dict[str, Any]:
    config = default_post_action_config()
    if not isinstance(raw, dict):
        return config
    for module_name in ("discard", "lock"):
        module = raw.get(module_name)
        if isinstance(module, dict):
            config[module_name].update({key: value for key, value in module.items() if key in config[module_name]})
    return normalize_post_action_config(config)


def normalize_post_action_config(config: dict[str, Any]) -> dict[str, Any]:
    config = merge_post_action_config(config) if set(config.keys()) != {"discard", "lock"} else copy.deepcopy(config)
    for module_name, default_module in DEFAULT_POST_ACTION_CONFIG.items():
        module = config.setdefault(module_name, copy.deepcopy(default_module))
        module["enabled"] = bool(module.get("enabled", False))
        if module.get("grade") not in GRADE_ORDER:
            module["grade"] = default_module["grade"]
        if module.get("role_scope") not in {"all", "selected"}:
            module["role_scope"] = default_module["role_scope"]
        if module.get("quality_scope") not in {"all", "gold", "gold_purple"}:
            module["quality_scope"] = default_module["quality_scope"]
        if module.get("type_scope") not in {"all", "drive", "tape"}:
            module["type_scope"] = default_module["type_scope"]
        for key in ("shape_ids", "set_names"):
            values = module.get(key)
            if values is None:
                module[key] = None
            elif isinstance(values, list):
                module[key] = [str(value) for value in values if str(value)]
            else:
                module[key] = default_module[key]
        for key in ("on_locked", "on_discarded"):
            if module.get(key) not in {"skip", "normal"}:
                module[key] = default_module[key]
    return config


def post_actions_enabled(config: dict[str, Any] | None) -> bool:
    config = merge_post_action_config(config)
    return bool(config["discard"]["enabled"] or config["lock"]["enabled"])


def validate_post_action_config(config: dict[str, Any] | None, selected_roles: list[str] | None = None) -> str | None:
    config = merge_post_action_config(config)
    selected_roles = selected_roles or []
    discard = config["discard"]
    lock = config["lock"]
    if discard["enabled"] and lock["enabled"]:
        if GRADE_ORDER.index(lock["grade"]) >= GRADE_ORDER.index(discard["grade"]):
            return "锁定阈值必须高于弃置阈值。"
    for title, module in (("弃置模块", discard), ("锁定模块", lock)):
        if module["enabled"] and module.get("role_scope") == "selected" and not selected_roles:
            return f"{title} 已选择“所选角色”，请先在第二步选择至少一个角色。"
    return None


def _role_names_for_scope(scoring: ScoringEngine, module_config: dict, selected_roles: list[str] | None) -> list[str]:
    if module_config.get("role_scope") == "selected":
        return [role for role in (selected_roles or []) if role in scoring.roles_db]
    return list(scoring.roles_db.keys())


def _quality_matches(item: BaseEquipment, quality_scope: str) -> bool:
    if quality_scope == "gold":
        return item.quality == "Gold"
    if quality_scope == "gold_purple":
        return item.quality in {"Gold", "Purple"}
    return True


def _type_matches(item: BaseEquipment, type_scope: str) -> bool:
    if type_scope == "drive":
        return isinstance(item, Drive)
    if type_scope == "tape":
        return isinstance(item, Tape)
    return True


def _range_value_matches(value: str, selected_values: list[str] | None, default_excluded_values: set[str]) -> bool:
    if selected_values is None:
        return value not in default_excluded_values
    return value in set(selected_values)


def _type_range_matches(item: BaseEquipment, module_config: dict) -> bool:
    if isinstance(item, Drive):
        return _range_value_matches(
            str(getattr(item, "shape_id", "") or ""),
            module_config.get("shape_ids"),
            DEFAULT_EXCLUDED_SHAPE_IDS,
        )
    if isinstance(item, Tape):
        return _range_value_matches(
            str(getattr(item, "set_name", "") or ""),
            module_config.get("set_names"),
            DEFAULT_EXCLUDED_SET_NAMES,
        )
    return True


def _module_matches_item(item: BaseEquipment, module_config: dict) -> bool:
    return (
        _quality_matches(item, module_config.get("quality_scope", "all"))
        and _type_matches(
            item,
            module_config.get("type_scope", "all"),
        )
        and _type_range_matches(item, module_config)
    )


def _state_action_allowed(current_state: str, module_config: dict) -> bool:
    if current_state == "locked" and module_config.get("on_locked") == "skip":
        return False
    if current_state == "discarded" and module_config.get("on_discarded") == "skip":
        return False
    return True


def _grade_for_score(scoring: ScoringEngine, item: BaseEquipment, score: float) -> str:
    return scoring.get_grade_tag(float(score or 0.0), int(getattr(item, "area", 1) or 1))


def _scores_for_roles(item: BaseEquipment, role_names: list[str]) -> list[float]:
    role_scores = getattr(item, "role_scores", {}) or {}
    return [float(role_scores.get(role, 0.0) or 0.0) for role in role_names]


def _discard_module_result(
    item: BaseEquipment,
    current_state: str,
    module_config: dict,
    scoring: ScoringEngine,
    selected_roles: list[str] | None,
) -> str | None:
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        return None
    if not _state_action_allowed(current_state, module_config):
        return None
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        return None
    scores = _scores_for_roles(item, role_names)
    grade = _grade_for_score(scoring, item, max(scores) if scores else 0.0)
    is_low = GRADE_ORDER.index(grade) >= GRADE_ORDER.index(module_config["grade"])
    if is_low:
        return "discarded"
    if current_state == "discarded":
        return "normal"
    return None


def _lock_module_result(
    item: BaseEquipment,
    current_state: str,
    module_config: dict,
    scoring: ScoringEngine,
    selected_roles: list[str] | None,
) -> str | None:
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        return None
    if not _state_action_allowed(current_state, module_config):
        return None
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        return None
    scores = _scores_for_roles(item, role_names)
    grade = _grade_for_score(scoring, item, max(scores) if scores else 0.0)
    is_high = GRADE_ORDER.index(grade) <= GRADE_ORDER.index(module_config["grade"])
    if is_high:
        return "locked"
    if current_state == "locked":
        return "normal"
    return None


def target_state_for_item(
    item: BaseEquipment,
    current_state: str,
    config: dict[str, Any],
    scoring: ScoringEngine,
    selected_roles: list[str] | None = None,
) -> str:
    current_state = current_state if current_state in EQUIPMENT_STATES else "normal"
    config = merge_post_action_config(config)
    lock_result = _lock_module_result(item, current_state, config["lock"], scoring, selected_roles)
    discard_result = _discard_module_result(item, current_state, config["discard"], scoring, selected_roles)
    if lock_result == "locked":
        return "locked"
    if discard_result == "discarded":
        return "discarded"
    if current_state == "locked" and lock_result == "normal":
        return "normal"
    if current_state == "discarded" and discard_result == "normal":
        return "normal"
    return current_state


def build_state_changes(
    parsed_items: list[tuple[int, BaseEquipment, str]],
    config: dict[str, Any],
    scoring: ScoringEngine,
    selected_roles: list[str] | None = None,
) -> list[dict[str, Any]]:
    changes = []
    for index, item, current_state in parsed_items:
        target_state = target_state_for_item(item, current_state, config, scoring, selected_roles)
        if target_state != current_state:
            changes.append(
                {
                    "index": int(index),
                    "current_state": current_state,
                    "target_state": target_state,
                    "item_type": item.item_type,
                    "quality": item.quality,
                    "uid": item.uid,
                }
            )
    return changes


def summarize_state_changes(changes: list[dict[str, Any]], applied_count: int | None = None) -> dict[str, int]:
    summary = {
        "post_action_target_count": len(changes),
        "post_action_applied_count": int(applied_count if applied_count is not None else len(changes)),
        "discard_set_count": 0,
        "discard_clear_count": 0,
        "lock_set_count": 0,
        "lock_clear_count": 0,
    }
    for change in changes:
        current = change.get("current_state")
        target = change.get("target_state")
        if target == "discarded":
            summary["discard_set_count"] += 1
        elif current == "discarded" and target == "normal":
            summary["discard_clear_count"] += 1
        elif target == "locked":
            summary["lock_set_count"] += 1
        elif current == "locked" and target == "normal":
            summary["lock_clear_count"] += 1
    return summary
