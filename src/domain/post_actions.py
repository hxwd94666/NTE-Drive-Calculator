# 定义扫描后装备状态计划及遗留评分上下文，不在此派发游戏写操作。
"""按完整扫描输入生成状态计划；权限、派发和后验确认由外部操作 owner 负责。"""

from __future__ import annotations

import copy
import math
from typing import Any

from src.models.equipment import BaseEquipment, Drive, Tape
from src.optimizer.scoring import ScoringEngine
from src.domain.equipment.matching import EquipmentMatchContext
from src.utils.set_name import normalize_set_display_name


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
    "required_sub_stats": [],
    # 从词条池选取 1–4 个命中数；必含词条另行核对，不能扩大匹配池。
    "sub_match": 4,
    "quality_scope": "gold_purple",
    "shape_ids": None,
    "set_names": None,
}

DEFAULT_POST_ACTION_CONFIG: dict[str, Any] = {
    "server_region": "default",
    "discard": {
        "enabled": False,
        "grade": "S",
        "discard_unmatched": False,
        "role_scope": "all",
        "selected_character_ids": [],
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
        "selected_character_ids": [],
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
        # 旧共享范围仅在模块字段缺失时复制；显式空列表也属于本模块的选择。
        if not isinstance(module, dict) or "selected_character_ids" not in module:
            config[module_name]["selected_character_ids"] = copy.deepcopy(raw.get("selected_character_ids", []))
    if isinstance(raw.get("preserve_rules"), list):
        config["preserve_rules"] = [copy.deepcopy(rule) for rule in raw["preserve_rules"] if isinstance(rule, dict)]
    config["preserve_rules"].extend(_legacy_custom_keep_rules(raw.get("custom_keep")))
    return normalize_post_action_config(config)


def normalize_post_action_config(config: dict[str, Any]) -> dict[str, Any]:
    config = merge_post_action_config(config) if not {"discard", "lock"}.issubset(set(config.keys())) else copy.deepcopy(config)
    if config.get("server_region") not in {"default", "hmt"}:
        config["server_region"] = "default"
    legacy_ids = config.pop("selected_character_ids", [])
    for module_name in ("discard", "lock"):
        default_module = DEFAULT_POST_ACTION_CONFIG[module_name]
        module = config.setdefault(module_name, copy.deepcopy(default_module))
        selected_ids: list[int] = []
        raw_ids = module.get("selected_character_ids", legacy_ids)
        for value in raw_ids if isinstance(raw_ids, list) else []:
            try:
                character_id = int(value)
            except (TypeError, ValueError):
                continue
            if character_id > 0 and character_id not in selected_ids:
                selected_ids.append(character_id)
        module["selected_character_ids"] = selected_ids
        module["enabled"] = bool(module.get("enabled", False))
        if module_name == "discard":
            module["discard_unmatched"] = module.get("discard_unmatched") is True
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
    if rule.get("action") not in {"keep", "lock", "discard"}:
        rule["action"] = "keep"
    if rule.get("quality_scope") not in {"all", "gold", "gold_purple"}:
        rule["quality_scope"] = "gold_purple"
    for key in ("main_stats", "sub_stats", "required_sub_stats", "shape_ids", "set_names"):
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
    rule["sub_match"] = _normalize_sub_match(rule.get("sub_match"), len(rule["sub_stats"]))
    return rule


def _normalize_sub_match(value: Any, selected_count: int = 0) -> int:
    """Normalize legacy values and current match counts to the 1-4 range."""
    if value == "all":
        # Preserve legacy semantics for old rules without retaining an invalid
        # UI option: all selected stats, capped by equipment's four sub stats.
        return max(1, min(4, int(selected_count or 4)))
    if value == "any":
        return 1
    try:
        return max(1, min(int(value), 4))
    except (TypeError, ValueError):
        return 4


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
                    "sub_match": min(4, len(sub_stats)) if sub_stats and minimum >= len(sub_stats) else max(1, min(minimum, 4)),
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
        if (
            module["enabled"]
            and module.get("role_scope") == "selected"
            and not module["selected_character_ids"]
            and not selected_roles
        ):
            return f"{title} 已选择“指定角色”，请在管理界面选择至少一个评估角色。"
    for index, rule in enumerate(config["preserve_rules"], start=1):
        if not rule.get("enabled"):
            continue
        title = rule.get("name") or f"处理规则 {index}"
        if rule["item_type"] == "drive" and not rule["sub_stats"]:
            return f"{title}：驱动规则至少选择一个副词条。"
        if rule["item_type"] == "tape" and not (rule["main_stats"] or rule["sub_stats"]):
            return f"{title}：卡带规则至少选择主词条或副词条。"
        selected_count = len(rule["sub_stats"])
        required_count = len(rule.get("required_sub_stats", []))
        match_count = _normalize_sub_match(rule.get("sub_match"), selected_count)
        if not set(rule.get("required_sub_stats", [])).issubset(rule["sub_stats"]):
            return f"{title}：必须包含的副词条必须同时在副词条命中池中。"
        if (selected_count or required_count) and selected_count < match_count:
            return f"{title}：副词条池数量少于命中数量。"
        if required_count >= match_count:
            return f"{title}：必须包含的副词条数量必须少于命中数量。"
    return None


def _role_names_for_scope(scoring: ScoringEngine, module_config: dict, selected_roles: list[str] | None) -> list[str]:
    if module_config.get("role_scope") == "selected":
        return [role for role in (selected_roles or []) if scoring is not None and role in scoring.roles_db]
    return list(scoring.roles_db.keys()) if scoring is not None else []



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
        selected_sets = module_config.get("set_names")
        item_set = normalize_set_display_name(getattr(item, "set_name", ""))
        if selected_sets is None:
            return item_set not in {
                normalize_set_display_name(value)
                for value in DEFAULT_EXCLUDED_SET_NAMES
            }
        return item_set in {
            normalize_set_display_name(value)
            for value in selected_sets
        }
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


def preserve_rule_matches_item(item: BaseEquipment, rule: dict) -> bool:
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
    required_sub_stats = set(rule.get("required_sub_stats") or [])
    # 必含词条须属于已选匹配池；手工或损坏配置也不得扩大处理规则。
    if not required_sub_stats.issubset(selected_sub_stats):
        return False
    item_sub_stats = set((getattr(item, "sub_stats", {}) or {}).keys())
    if not required_sub_stats.issubset(item_sub_stats):
        return False
    if not selected_sub_stats:
        return True
    match_count = len(item_sub_stats & selected_sub_stats)
    required_count = _normalize_sub_match(rule.get("sub_match"), len(selected_sub_stats))
    return match_count >= required_count


def _preserve_rule_detail(item: BaseEquipment, rules: list[dict]) -> dict[str, Any]:
    matched = [rule for rule in rules if preserve_rule_matches_item(item, rule)]
    actions = {rule.get("action") for rule in matched}
    return {
        "enabled": bool(rules),
        "matched": [rule.get("name") or "未命名规则" for rule in matched],
        # 列表顺序只用于展示；动作优先级唯一在此定义，不采用 first-hit。
        "action": next((action for action in ("lock", "discard", "keep") if action in actions), None),
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
        if any(preserve_rule_matches_item(item, rule) for rule in preserve_rules):
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
    score_context: EquipmentMatchContext | None,
) -> tuple[list[str], str]:
    if score_context is None or score_context.errors_for_roles(role_names):
        return [], "incomplete"
    allowed = {match.role for match in score_context.for_item(item)}
    filtered = [role for role in role_names if role in allowed]
    return filtered, "matched_usable_roles" if filtered else "unmatched"


def _role_score_pairs_for_item(
    item: BaseEquipment,
    role_names: list[str],
    score_context: EquipmentMatchContext | None = None,
) -> tuple[list[tuple[str, float]], str]:
    usable_roles, match_mode = _usable_role_names(item, role_names, score_context)
    role_scores = getattr(item, "role_scores", {}) or {}
    if any(role not in role_scores or type(role_scores[role]) not in (float, int)
           or not math.isfinite(role_scores[role]) for role in usable_roles):
        return [], "incomplete"
    return [(role, float(role_scores[role])) for role in usable_roles], match_mode


def _best_score_detail(
    item: BaseEquipment,
    role_names: list[str],
    scoring: ScoringEngine,
    score_context: EquipmentMatchContext | None = None,
) -> dict[str, Any]:
    pairs, match_mode = _role_score_pairs_for_item(item, role_names, score_context)
    best_role = ""
    best_score = None
    if pairs:
        best_role, best_score = max(pairs, key=lambda pair: pair[1])
    return {
        "score": best_score,
        "grade": _grade_for_score(scoring, item, best_score) if pairs else None,
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
    score_context: EquipmentMatchContext | None = None,
) -> tuple[str | None, dict[str, Any]]:
    detail = {"module": "discard", "enabled": bool(module_config.get("enabled")), "result": None}
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        detail["reason"] = "module_or_filter_not_matched"
        return None, detail
    if not _state_action_allowed(current_state, module_config):
        detail["reason"] = "state_policy_skip"
        return None, detail
    if score_context is not None and score_context.missing_character_ids and module_config.get("role_scope") == "all":
        # 全角色范围缺失旧配置角色时结论不完整；明确选择的其他角色不因此被扩大保护。
        detail["reason"] = "evaluation_incomplete"
        return None, detail
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        detail["reason"] = "evaluation_incomplete" if scoring is None else "no_scope_roles"
        return None, detail
    detail.update(_best_score_detail(item, role_names, scoring, score_context))
    if detail["match_mode"] == "incomplete":
        detail["reason"] = "evaluation_incomplete"
        return None, detail
    if detail["match_mode"] == "unmatched":
        detail["reason"] = "unmatched"
        if module_config.get("discard_unmatched") is True:
            detail.update(result="discarded", reason="unmatched_explicit_discard")
            return "discarded", detail
        return None, detail
    grade = str(detail["grade"])
    detail["threshold"] = module_config["grade"]
    is_low = GRADE_ORDER.index(grade) >= GRADE_ORDER.index(
        str(module_config["grade"])
    )
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
    score_context: EquipmentMatchContext | None = None,
) -> tuple[str | None, dict[str, Any]]:
    detail = {"module": "lock", "enabled": bool(module_config.get("enabled")), "result": None}
    if not module_config.get("enabled") or not _module_matches_item(item, module_config):
        detail["reason"] = "module_or_filter_not_matched"
        return None, detail
    if not _state_action_allowed(current_state, module_config):
        detail["reason"] = "state_policy_skip"
        return None, detail
    if score_context is not None and score_context.missing_character_ids and module_config.get("role_scope") == "all":
        detail["reason"] = "evaluation_incomplete"
        return None, detail
    role_names = _role_names_for_scope(scoring, module_config, selected_roles)
    if not role_names:
        detail["reason"] = "evaluation_incomplete" if scoring is None else "no_scope_roles"
        return None, detail
    detail.update(_best_score_detail(item, role_names, scoring, score_context))
    if detail["match_mode"] in {"incomplete", "unmatched"}:
        detail["reason"] = "evaluation_incomplete" if detail["match_mode"] == "incomplete" else "unmatched"
        return None, detail
    grade = str(detail["grade"])
    detail["threshold"] = module_config["grade"]
    is_high = GRADE_ORDER.index(grade) <= GRADE_ORDER.index(
        str(module_config["grade"])
    )
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
    selected_roles: list[str] | dict[str, list[str]] | None = None,
    score_context: EquipmentMatchContext | None = None,
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
    selected_roles: list[str] | dict[str, list[str]] | None = None,
    score_context: EquipmentMatchContext | None = None,
    *, incomplete_scopes: frozenset[str] = frozenset(),
) -> tuple[str, dict[str, Any]]:
    """先规则锁与评分锁，再规则弃置；评分解锁、规则保留与评分弃置维持既有次序。"""
    current_state = current_state if current_state in EQUIPMENT_STATES else "normal"
    config = merge_post_action_config(config)
    preserve_detail = _preserve_rule_detail(item, config["preserve_rules"])
    if preserve_detail["action"] == "lock":
        return "locked", {"preserve": preserve_detail}
    lock_roles = selected_roles.get("lock", []) if isinstance(selected_roles, dict) else selected_roles
    discard_roles = selected_roles.get("discard", []) if isinstance(selected_roles, dict) else selected_roles
    lock_result, lock_detail = _lock_module_result(item, current_state, config["lock"],
        None if "lock" in incomplete_scopes else scoring, lock_roles, score_context)
    details = {"preserve": preserve_detail, "lock": lock_detail}
    if lock_result == "locked":
        return "locked", details
    if preserve_detail["action"] == "discard":
        # 直接弃置不依赖评分阈值，但仍遵守用户的既有游戏状态跳过策略。
        # 显式准许处理锁定时直接给最终目标，不要求先解锁、再运行第二轮。
        allowed = _state_action_allowed(current_state, config["discard"])
        preserve_detail["reason"] = "rule_discard" if allowed else "state_policy_skip"
        return "discarded" if allowed else (lock_result or current_state), details
    if current_state == "locked" and lock_result == "normal":
        return "normal", details
    if preserve_detail["action"] == "keep":
        return "normal" if current_state == "discarded" else current_state, details
    discard_result, discard_detail = _discard_module_result(item, current_state, config["discard"],
        None if "discard" in incomplete_scopes else scoring, discard_roles, score_context)
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
    selected_roles: list[str] | dict[str, list[str]] | None = None,
    score_context: EquipmentMatchContext | None = None,
    *, evaluation_summary: dict[str, int] | None = None,
    incomplete_scopes: frozenset[str] = frozenset(),
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
            incomplete_scopes=incomplete_scopes,
        )
        if evaluation_summary is not None:
            # 统计实际走到的评分决策，一件装备跨两模块最多计一次；不为汇总再次评分。
            modes = {part.get("reason") for name, part in detail.items() if name in {"discard", "lock"}}
            for key, reasons in (
                ("post_action_unmatched_count", {"unmatched", "unmatched_explicit_discard"}),
                ("post_action_incomplete_count", {"evaluation_incomplete"}),
            ):
                if modes & reasons:
                    evaluation_summary[key] = evaluation_summary.get(key, 0) + 1
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
