# 浅投影状态计算所需字段，复用冻结元组并避免复制展示文案和装备数据。
from __future__ import annotations

from collections.abc import Mapping


# 对应 buff_state_compute 的共享反序列化结构；保留全部必填字段和原始空值。
HIT_FIELDS = (
    "event_id", "sequence", "relative_time_us", "character_id", "skill_name",
    "damage_name", "damage_component", "attack_type", "damage_attribute", "target_id",
    "direction", "is_follow_up", "classification", "ability_id", "gameplay_effect_id", "scope_half",
)
ACTION_FIELDS = (
    "action_id", "character_id", "action_name", "input_kind", "start_us", "end_us",
    "evidence_event_ids", "gameplay_effect_ids",
)
RULE_FIELDS = (
    "source_character_id", "source_effect_definition_id", "target_asset_path", "event_type",
    "effect_type", "duration_policy", "duration_seconds", "cooldown_seconds", "stacking_type",
    "stack_limit_count", "stack_count", "application_requirement_asset_path",
)
EVENT_FIELDS = ("event_id", "relative_time_us", "source_character_id")
HP_EVENT_FIELDS = ("mechanic_kind", "evidence_kind", "max_hp_reduction", "evidence_event_ids")
ZANKOU_CONFIG_FIELDS = (
    "fantasy_duration_seconds", "reality_to_fantasy_retention_seconds",
    "fantasy_to_reality_retention_seconds",
)


def state_row(row, fields):
    return {key: getattr(row, key) for key in fields}


def state_rows(rows, fields):
    return tuple(state_row(row, fields) for row in rows)


def finalize_rows(intervals):
    return tuple({
        **state_row(row, ("start_us", "end_us", "source_character_id", "buff_asset_path")),
        "modifiers": tuple({"target_require_tags": modifier.target_require_tags}
                           for modifier in row.modifiers),
    } for row in intervals)


def fadia_character(row):
    def selected(value, fields):
        return {key: value[key] for key in fields if key in value}

    result = selected(row, ("awakening_level",))
    if "profile" in row:
        profile = row["profile"]
        result["profile"] = selected(profile, (
            "awakening_selection_initialized", "selected_awaken_effect_ids", "awakening_level",
        )) if isinstance(profile, Mapping) else profile
    if "stats" in row:
        stats = row["stats"]
        result["stats"] = tuple(selected(stat, ("source_group", "property_id", "value"))
                                if isinstance(stat, Mapping) else stat for stat in stats
                                ) if isinstance(stats, (tuple, list)) else stats
    return result
