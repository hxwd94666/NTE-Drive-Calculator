# 管理全量扫描后的弃置和锁定状态同步规则。
"""Post-scan equipment state rules used by full inventory scans."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from src.models.equipment import BaseEquipment, Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.solver.orchestrator import NTEPipelineOrchestrator


GRADE_ORDER = ["ACE", "SSS", "SS", "S", "A", "B", "C", "D"]
EQUIPMENT_STATES = {"normal", "locked", "discarded"}
DEFAULT_EXCLUDED_SHAPE_IDS = {"H_2", "V_2"}
DEFAULT_EXCLUDED_SET_NAMES = {"音速蓝刺猬", "音速索尼克"}

DEFAULT_PRESERVE_RULE: dict[str, Any] = {
    "enabled": True,
    "name": "",
    "item_type": "tape",
    "action": "keep",
    "main_stats": [],
    "sub_stats": [],
    # "all" means every selected sub stat must match; otherwise 1/2/3 means
    # at least that many of the selected sub stats must match.
    "sub_match": "all",
    "quality_scope": "gold_purple",
    "shape_ids": None,
    "set_names": None,
}

DEFAULT_POST_ACTION_CONFIG: dict[str, Any] = {
    "server_region": "default",
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
    "preserve_rules": [],
}


def default_post_action_config() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_POST_ACTION_CONFIG)


def merge_post_action_config(raw: dict | None) -> dict[str, Any]:
    config = default_post_action_config()
    if not isinstance(raw, dict):
        return config
    if raw.get("server_region") in {"default", "hmt"}:
        config["server_region"] = raw["server_region"]
    for module_name in ("discard", "lock"):
        module = raw.get(module_name)
        if isinstance(module, dict):
            config[module_name].update({key: value for key, value in module.items() if key in config[module_name]})
    if isinstance(raw.get("preserve_rules"), list):
        config["preserve_rules"] = [copy.deepcopy(rule) for rule in raw["preserve_rules"] if isinstance(rule, dict)]
    config["preserve_rules"].extend(_legacy_custom_keep_rules(raw.get("custom_keep")))
    return normalize_post_action_config(config)


def normalize_post_action_config(config: dict[str, Any]) -> dict[str, Any]:
    config = merge_post_action_config(config) if not {"discard", "lock"}.issubset(set(config.keys())) else copy.deepcopy(config)
    if config.get("server_region") not in {"default", "hmt"}:
        config["server_region"] = "default"
    for module_name in ("discard", "lock"):
        default_module = DEFAULT_POST_ACTION_CONFIG[module_name]
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
    normalized_rules = []
    for raw_rule in config.get("preserve_rules", []) if isinstance(config.get("preserve_rules"), list) else []:
        rule = _normalize_preserve_rule(raw_rule)
        if rule is not None:
            normalized_rules.append(rule)
    config["preserve_rules"] = normalized_rules
    return config


def post_actions_enabled(config: dict[str, Any] | None) -> bool:
    config = merge_post_action_config(config)
    return bool(
        config["discard"]["enabled"]
        or config["lock"]["enabled"]
        or any(rule.get("enabled") for rule in config["preserve_rules"])
    )


def _normalize_preserve_rule(raw_rule: Any) -> dict[str, Any] | None:
    if not isinstance(raw_rule, dict):
        return None
    rule = copy.deepcopy(DEFAULT_PRESERVE_RULE)
    rule.update({key: value for key, value in raw_rule.items() if key in rule})
    rule["enabled"] = bool(rule.get("enabled", True))
    rule["name"] = str(rule.get("name") or "").strip()
    if rule.get("item_type") not in {"drive", "tape"}:
        rule["item_type"] = "tape"
    if rule.get("action") not in {"keep", "lock"}:
        rule["action"] = "keep"
    rule["sub_match"] = _normalize_sub_match(rule.get("sub_match"))
    if rule.get("quality_scope") not in {"all", "gold", "gold_purple"}:
        rule["quality_scope"] = "gold_purple"
    for key in ("main_stats", "sub_stats", "shape_ids", "set_names"):
        values = rule.get(key)
        if key in {"shape_ids", "set_names"} and values is None:
            rule[key] = None
        elif isinstance(values, list):
            rule[key] = [str(value).strip() for value in values if str(value).strip()]
        else:
            rule[key] = []
    if rule["item_type"] == "drive":
        rule["main_stats"] = []
        if not rule["sub_stats"]:
            return None
    elif not (rule["main_stats"] or rule["sub_stats"]):
        return None
    return rule


def _normalize_sub_match(value: Any) -> str | int:
    """Normalize both legacy all/any values and the current match counts."""
    if value == "all":
        return "all"
    if value == "any":
        return 1
    try:
        return max(1, min(int(value), 3))
    except (TypeError, ValueError):
        return "all"


def _legacy_custom_keep_rules(raw_custom_keep: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_custom_keep, dict):
        return []
    migrated = []
    for item_type, rule_key, title in (("tape", "tape_rules", "卡带"), ("drive", "drive_rules", "驱动")):
        for index, raw_rule in enumerate(raw_custom_keep.get(rule_key, []) or [], start=1):
            if not isinstance(raw_rule, dict):
                continue
            sub_stats = [str(value).strip() for value in raw_rule.get("sub_stats", []) if str(value).strip()]
            main_stats = [str(value).strip() for value in raw_rule.get("main_stats", []) if str(value).strip()]
            if item_type == "tape" and not (main_stats or sub_stats):
                continue
            if item_type == "drive" and not sub_stats:
                continue
            minimum = raw_rule.get("minimum_sub_matches", 1)
            try:
                minimum = int(minimum)
            except (TypeError, ValueError):
                minimum = 1
            migrated.append(
                {
                    "enabled": True,
                    "name": f"迁移的{title}规则 {index}",
                    "item_type": item_type,
                    "action": "lock" if raw_rule.get("action") == "lock" else "keep",
                    "main_stats": main_stats if item_type == "tape" else [],
                    "sub_stats": sub_stats,
                    "sub_match": "all" if sub_stats and minimum >= len(sub_stats) else max(1, min(minimum, 3)),
                    "quality_scope": "all",
                    "shape_ids": None,
                    "set_names": None,
                }
            )
    return migrated


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
    for index, rule in enumerate(config["preserve_rules"], start=1):
        if not rule.get("enabled"):
            continue
        title = rule.get("name") or f"预留规则 {index}"
        if rule["item_type"] == "drive" and not rule["sub_stats"]:
            return f"{title}：驱动规则至少选择一个副词条。"
        if rule["item_type"] == "tape" and not (rule["main_stats"] or rule["sub_stats"]):
            return f"{title}：卡带规则至少选择主词条或副词条。"
    return None


def _role_names_for_scope(scoring: ScoringEngine, module_config: dict, selected_roles: list[str] | None) -> list[str]:
    if module_config.get("role_scope") == "selected":
        return [role for role in (selected_roles or []) if role in scoring.roles_db]
    return list(scoring.roles_db.keys())


@dataclass
class PostActionScoreContext:
    strict: bool = False
    drive_roles_by_shape: dict[str, set[str]] = field(default_factory=dict)
    tape_roles_by_set: dict[str, set[str]] = field(default_factory=dict)
    role_sets: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config_dir(cls, config_dir: str | None) -> "PostActionScoreContext":
        try:
            orchestrator = NTEPipelineOrchestrator(config_dir=str(config_dir or "config"))
            role_names = list(orchestrator.roles_db.keys())
            blueprints = orchestrator.solve_blueprints(role_names)
        except Exception:
            return cls(strict=False)

        drive_roles_by_shape: dict[str, set[str]] = {}
        tape_roles_by_set: dict[str, set[str]] = {}
        role_sets: dict[str, str] = {}
        for role_name, role_data in orchestrator.roles_db.items():
            try:
                target_set = orchestrator._resolve_set_name(role_data.get("default_set", ""))
            except Exception:
                continue
            role_sets[role_name] = target_set
            tape_roles_by_set.setdefault(target_set, set()).add(role_name)

            for blueprint in blueprints.get(role_name, []) or []:
                shape_ids = list(blueprint.get("set_pieces", []) or []) + list(blueprint.get("extra_pieces", []) or [])
                for shape_id in shape_ids:
                    if shape_id:
                        drive_roles_by_shape.setdefault(str(shape_id), set()).add(role_name)

        return cls(
            strict=bool(drive_roles_by_shape or tape_roles_by_set),
            drive_roles_by_shape=drive_roles_by_shape,
            tape_roles_by_set=tape_roles_by_set,
            role_sets=role_sets,
        )


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


def _preserve_rule_matches_item(item: BaseEquipment, rule: dict) -> bool:
    if not rule.get("enabled"):
        return False
    if rule.get("item_type") == "drive" and not isinstance(item, Drive):
        return False
    if rule.get("item_type") == "tape" and not isinstance(item, Tape):
        return False
    if not _quality_matches(item, rule.get("quality_scope", "gold_purple")):
        return False
    if not _type_range_matches(item, rule):
        return False
    main_stats = set(rule.get("main_stats") or [])
    if main_stats and str(getattr(item, "main_stats", "") or "") not in main_stats:
        return False
    selected_sub_stats = set(rule.get("sub_stats") or [])
    if not selected_sub_stats:
        return True
    item_sub_stats = set((getattr(item, "sub_stats", {}) or {}).keys())
    match_count = len(item_sub_stats & selected_sub_stats)
    sub_match = _normalize_sub_match(rule.get("sub_match"))
    required_count = len(selected_sub_stats) if sub_match == "all" else min(int(sub_match), len(selected_sub_stats))
    return match_count >= required_count


def _preserve_rule_detail(item: BaseEquipment, rules: list[dict]) -> dict[str, Any]:
    matched = [rule for rule in rules if _preserve_rule_matches_item(item, rule)]
    actions = {rule.get("action") for rule in matched}
    return {
        "enabled": bool(rules),
        "matched": [rule.get("name") or "未命名规则" for rule in matched],
        "action": "lock" if "lock" in actions else "keep" if "keep" in actions else None,
    }


def summarize_post_action_filtering(
    parsed_items: list[tuple[int, BaseEquipment, str]],
    config: dict[str, Any],
) -> dict[str, int]:
    config = merge_post_action_config(config)
    enabled_modules = [
        module for module in (config["discard"], config["lock"])
        if module.get("enabled")
    ]
    summary = {
        "post_action_parsed_count": len(parsed_items),
        "post_action_candidate_count": 0,
        "post_action_quality_filtered_count": 0,
        "post_action_type_filtered_count": 0,
        "post_action_type_range_filtered_count": 0,
        "preserve_rule_matched_count": 0,
    }
    preserve_rules = [rule for rule in config["preserve_rules"] if rule.get("enabled")]
    if not enabled_modules and not preserve_rules:
        return summary

    for _index, item, _current_state in parsed_items:
        if any(_preserve_rule_matches_item(item, rule) for rule in preserve_rules):
            summary["post_action_candidate_count"] += 1
            summary["preserve_rule_matched_count"] += 1
            continue
        if any(_module_matches_item(item, module) for module in enabled_modules):
            summary["post_action_candidate_count"] += 1
            continue
        if not enabled_modules:
            continue
        if any(
            _quality_matches(item, module.get("quality_scope", "all"))
            and _type_matches(item, module.get("type_scope", "all"))
            and not _type_range_matches(item, module)
            for module in enabled_modules
        ):
            summary["post_action_type_range_filtered_count"] += 1
            continue
        if any(
            _quality_matches(item, module.get("quality_scope", "all"))
            and not _type_matches(item, module.get("type_scope", "all"))
            for module in enabled_modules
        ):
            summary["post_action_type_filtered_count"] += 1
            continue
        summary["post_action_quality_filtered_count"] += 1
    return summary


def _state_action_allowed(current_state: str, module_config: dict) -> bool:
    if current_state == "locked" and module_config.get("on_locked") == "skip":
        return False
    if current_state == "discarded" and module_config.get("on_discarded") == "skip":
        return False
    return True


def _grade_for_score(scoring: ScoringEngine, item: BaseEquipment, score: float) -> str:
    return scoring.get_grade_tag(float(score or 0.0), int(getattr(item, "area", 1) or 1))


def _usable_role_names(
    item: BaseEquipment,
    role_names: list[str],
    score_context: PostActionScoreContext | None,
) -> tuple[list[str], str]:
    if not score_context or not score_context.strict:
        return role_names, "fallback_all_roles"
    allowed: set[str]
    if isinstance(item, Drive):
        allowed = score_context.drive_roles_by_shape.get(str(getattr(item, "shape_id", "") or ""), set())
    elif isinstance(item, Tape):
        allowed = score_context.tape_roles_by_set.get(str(getattr(item, "set_name", "") or ""), set())
    else:
        allowed = set()
    filtered = [role for role in role_names if role in allowed]
    return filtered, "matched_usable_roles"


def _role_score_pairs_for_item(
    item: BaseEquipment,
    role_names: list[str],
    score_context: PostActionScoreContext | None = None,
) -> tuple[list[tuple[str, float]], str]:
    usable_roles, match_mode = _usable_role_names(item, role_names, score_context)
    role_scores = getattr(item, "role_scores", {}) or {}
    return [(role, float(role_scores.get(role, 0.0) or 0.0)) for role in usable_roles], match_mode


def _best_score_detail(
    item: BaseEquipment,
    role_names: list[str],
    scoring: ScoringEngine,
    score_context: PostActionScoreContext | None = None,
) -> dict[str, Any]:
    pairs, match_mode = _role_score_pairs_for_item(item, role_names, score_context)
    best_role = ""
    best_score = 0.0
    if pairs:
        best_role, best_score = max(pairs, key=lambda pair: pair[1])
    return {
        "score": best_score,
        "grade": _grade_for_score(scoring, item, best_score),
        "role": best_role,
        "eligible_roles": len(pairs),
        "match_mode": match_mode,
    }


def _discard_module_result(
    item: BaseEquipment,
    current_state: str,
    module_config: dict,
    scoring: ScoringEngine,
    selected_roles: list[str] | None,
    score_context: PostActionScoreContext | None = None,
) -> tuple[str | None, dict[str, Any]]:
    detail = {"module": "discard", "enabled": bool(module_config.get("enabled")), "result": None}
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        detail["reason"] = "module_or_filter_not_matched"
        return None, detail
    if not _state_action_allowed(current_state, module_config):
        detail["reason"] = "state_policy_skip"
        return None, detail
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        detail["reason"] = "no_scope_roles"
        return None, detail
    detail.update(_best_score_detail(item, role_names, scoring, score_context))
    grade = detail["grade"]
    detail["threshold"] = module_config["grade"]
    is_low = GRADE_ORDER.index(grade) >= GRADE_ORDER.index(module_config["grade"])
    if is_low:
        detail["result"] = "discarded"
        detail["reason"] = "grade_below_or_equal_threshold"
        return "discarded", detail
    if current_state == "discarded":
        detail["result"] = "normal"
        detail["reason"] = "discarded_item_no_longer_matches"
        return "normal", detail
    detail["reason"] = "grade_kept"
    return None, detail


def _lock_module_result(
    item: BaseEquipment,
    current_state: str,
    module_config: dict,
    scoring: ScoringEngine,
    selected_roles: list[str] | None,
    score_context: PostActionScoreContext | None = None,
) -> tuple[str | None, dict[str, Any]]:
    detail = {"module": "lock", "enabled": bool(module_config.get("enabled")), "result": None}
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        detail["reason"] = "module_or_filter_not_matched"
        return None, detail
    if not _state_action_allowed(current_state, module_config):
        detail["reason"] = "state_policy_skip"
        return None, detail
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        detail["reason"] = "no_scope_roles"
        return None, detail
    detail.update(_best_score_detail(item, role_names, scoring, score_context))
    grade = detail["grade"]
    detail["threshold"] = module_config["grade"]
    is_high = GRADE_ORDER.index(grade) <= GRADE_ORDER.index(module_config["grade"])
    if is_high:
        detail["result"] = "locked"
        detail["reason"] = "grade_above_or_equal_threshold"
        return "locked", detail
    if current_state == "locked":
        detail["result"] = "normal"
        detail["reason"] = "locked_item_no_longer_matches"
        return "normal", detail
    detail["reason"] = "grade_not_high_enough"
    return None, detail


def target_state_for_item(
    item: BaseEquipment,
    current_state: str,
    config: dict[str, Any],
    scoring: ScoringEngine,
    selected_roles: list[str] | None = None,
    score_context: PostActionScoreContext | None = None,
) -> str:
    return evaluate_target_state_for_item(
        item,
        current_state,
        config,
        scoring,
        selected_roles,
        score_context,
    )[0]


def evaluate_target_state_for_item(
    item: BaseEquipment,
    current_state: str,
    config: dict[str, Any],
    scoring: ScoringEngine,
    selected_roles: list[str] | None = None,
    score_context: PostActionScoreContext | None = None,
) -> tuple[str, dict[str, Any]]:
    current_state = current_state if current_state in EQUIPMENT_STATES else "normal"
    config = merge_post_action_config(config)
    preserve_detail = _preserve_rule_detail(item, config["preserve_rules"])
    if preserve_detail["action"] == "lock":
        return "locked", {"preserve": preserve_detail}
    lock_result, lock_detail = _lock_module_result(item, current_state, config["lock"], scoring, selected_roles, score_context)
    details = {"preserve": preserve_detail, "lock": lock_detail}
    if lock_result == "locked":
        return "locked", details
    if current_state == "locked" and lock_result == "normal":
        return "normal", details
    if preserve_detail["action"] == "keep":
        return "normal" if current_state == "discarded" else current_state, details
    discard_result, discard_detail = _discard_module_result(item, current_state, config["discard"], scoring, selected_roles, score_context)
    details["discard"] = discard_detail
    if discard_result == "discarded":
        return "discarded", details
    if current_state == "discarded" and discard_result == "normal":
        return "normal", details
    return current_state, details


def build_state_changes(
    parsed_items: list[tuple[int, BaseEquipment, str]],
    config: dict[str, Any],
    scoring: ScoringEngine,
    selected_roles: list[str] | None = None,
    score_context: PostActionScoreContext | None = None,
) -> list[dict[str, Any]]:
    changes = []
    for index, item, current_state in parsed_items:
        target_state, detail = evaluate_target_state_for_item(
            item,
            current_state,
            config,
            scoring,
            selected_roles,
            score_context,
        )
        if target_state != current_state:
            changes.append(
                {
                    "index": int(index),
                    "current_state": current_state,
                    "target_state": target_state,
                    "item_type": item.item_type,
                    "quality": item.quality,
                    "uid": item.uid,
                    "shape_id": getattr(item, "shape_id", ""),
                    "set_name": getattr(item, "set_name", ""),
                    "sub_stats": dict(getattr(item, "sub_stats", {}) or {}),
                    "main_stats": getattr(item, "main_stats", ""),
                    "decision": detail,
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
