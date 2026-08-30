# 集中维护游戏资料库杂项域的固定详情字段白名单。
"""Presentation metadata for bounded miscellaneous catalog details."""

from __future__ import annotations


BASE_FIELD_KEYS = {
    "equipment_item": (
        "item_id", "kind", "quality", "name_zh", "geometry_id", "geometry_enum",
        "grid_count", "suit_id", "suit_type_enum", "max_level", "strength_pack_id",
        "icon_path", "plan_icon_path", "is_guide_item",
    ),
    "equipment_suit": ("suit_id", "name_zh", "icon_path"),
    "equipment_shape": (
        "shape_id", "cell_count", "first_grid_delta_x", "first_grid_delta_y",
    ),
    "equipment_attribute": (
        "attribute_id", "display_name_zh", "filter_name_zh",
        "random_attribute_name_zh", "attribute_type", "show_percent", "score", "icon_path",
    ),
    "equipment_curve": (
        "curve_id", "interpolation_mode", "pre_infinity_extrapolation",
        "post_infinity_extrapolation", "default_value",
    ),
    "equipment_buff_curve": (
        "curve_id", "interpolation_mode", "pre_infinity_extrapolation",
        "post_infinity_extrapolation", "default_value",
    ),
    "equipment_modify_pack": ("modify_pack_id", "conditions"),
    "equipment_plan": (
        "character_id", "character_name_zh", "core_item_id", "core_name_zh",
        "core_level", "module_level", "reference_score", "background_path",
        "character_image_path",
    ),
    "graduation_template": (
        "character_id", "source_kind", "fork_id", "fork_level",
        "fork_refinement_level", "core_suit_id", "core_main_property_id",
        "drive_area", "extra_shape_count", "benchmark_damage", "generated_at_utc",
    ),
    "gameplay_ability": (
        "ability_id", "name_zh", "gameplay_ability_path", "icon_path",
        "extended_icon_path", "is_stolen",
    ),
    "skill_damage": (
        "damage_id", "ability_id", "damage_type", "damage_source_category",
        "charge_add", "unbal_value", "heterochrome_add", "fixed_crit_rate",
        "atk_rate_base", "def_rate_base", "hp_rate_base", "story_balance_ge_rate",
        "attack_break_level", "override_breakable_damage", "breakable_damage",
        "override_breakable_impulse", "breakable_impulse",
        "override_vehicle_breakable_impulse", "vehicle_breakable_impulse",
        "modifier_atk_rate_base_coefficient", "ability_relation_status",
        "same_name_gameplay_effect_relation_status",
    ),
    "gameplay_effect": (
        "gameplay_effect_index", "gameplay_effect_id", "class_path", "asset_path",
    ),
    "buff": (
        "definition_id", "asset_path", "definition_kind", "owner_character_id",
        "duration_policy", "duration_magnitude", "period", "stacking_type",
        "stack_limit_count",
    ),
    "combat_effect": (
        "effect_definition_id", "owner_kind", "owner_id", "effect_kind",
        "activation_kind", "description_zh", "formula_version",
    ),
    "combat_curve": (
        "curve_table_asset_path", "curve_id", "interpolation_mode", "default_value",
        "pre_infinity_extrapolation", "post_infinity_extrapolation",
    ),
    "combat_level_curve": (
        "curve_id", "damage_kind", "reaction_type", "source_effect_id",
        "interpolation_mode", "mapping_status",
    ),
    "reaction": (
        "reaction_type", "element_type_1", "element_type_2",
        "default_damage_effect_id",
    ),
    "combat_constant": (
        "constant_id", "source_time", "value", "unit", "description_zh",
    ),
    "gameplay_tag": ("tag_name", "source_asset_path", "property_path"),
    "roguelike_modifier": (
        "modifier_id", "conditions", "owner_resolution_status",
    ),
    "blueprint": (
        "asset_path", "asset_name", "asset_type", "asset_kind", "character_id",
    ),
    "montage": (
        "asset_path", "duration_seconds", "blend_in_seconds", "blend_out_seconds",
        "frame_rate_numerator", "frame_rate_denominator",
    ),
}
