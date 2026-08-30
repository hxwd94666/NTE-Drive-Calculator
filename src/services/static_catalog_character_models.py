# 游戏资料库角色域的 Qt 无关不可变 DTO。
"""Immutable character catalog contracts shared by service, controller and UI."""

from __future__ import annotations

from dataclasses import dataclass


class StaticCatalogProjectionError(RuntimeError):
    """A normalized static row cannot be projected without losing evidence."""


@dataclass(frozen=True, slots=True)
class CatalogDataset:
    dataset_id: str
    game_version: str | None
    schema_version: int
    importer_version: int
    built_at_utc: str


@dataclass(frozen=True, slots=True)
class CatalogSource:
    table_name: str
    row_id: int | None = None
    row_key: str | None = None
    relative_path: str | None = None
    content_sha256: str | None = None
    file_sha256: str | None = None
    payload_available: bool = False


@dataclass(frozen=True, slots=True)
class CatalogGap:
    field_key: str
    label: str
    status: str
    reason: str


@dataclass(frozen=True, slots=True)
class CharacterSummary:
    character_id: int
    name_zh: str
    element_type: str | None
    element_label: str
    group_type: str | None
    actor_path: str | None
    mainland_show_time: str | None
    logical_character_key: str | None
    canonical_character_id: int | None
    classification: str | None
    growth_count: int
    skill_count: int
    awakening_count: int
    has_graduation: bool
    source: CatalogSource


@dataclass(frozen=True, slots=True)
class CharacterPage:
    dataset: CatalogDataset
    query: str
    offset: int
    limit: int
    total: int
    items: tuple[CharacterSummary, ...]


@dataclass(frozen=True, slots=True)
class GrowthPoint:
    level: int
    breakthrough_stage: int
    state: str
    hp_base: float
    atk_base: float
    def_base: float
    player_pack_source: CatalogSource
    level_curve_source: CatalogSource
    breakthrough_source: CatalogSource | None


@dataclass(frozen=True, slots=True)
class GrowthPage:
    character_id: int
    offset: int
    limit: int
    total: int
    items: tuple[GrowthPoint, ...]


@dataclass(frozen=True, slots=True)
class BreakthroughStage:
    level: int
    stage: int
    before: GrowthPoint
    after: GrowthPoint
    cost_status: str = "unavailable"
    cost_reason: str = "发行静态库尚未保存人物突破阶段到材料数量与方斯的关系"


@dataclass(frozen=True, slots=True)
class LikeabilityProperty:
    property_id: str
    display_name: str
    value: float
    modifier_operation: str
    show_percent: bool
    source: CatalogSource


@dataclass(frozen=True, slots=True)
class LikeabilityBonus:
    required_level: int
    modify_data_id: str
    properties: tuple[LikeabilityProperty, ...]
    role_source: CatalogSource
    modifier_source: CatalogSource


@dataclass(frozen=True, slots=True)
class StructuredEffectField:
    path: str
    value_json: str


@dataclass(frozen=True, slots=True)
class SkillLevelBonus:
    skill_id: str
    level_delta: int


@dataclass(frozen=True, slots=True)
class AwakeningEffect:
    effect_id: str
    ordinal: int
    awaken_type: str
    title_zh: str | None
    description_zh: str | None
    icon_path: str | None
    structured_effects: tuple[StructuredEffectField, ...]
    gameplay_effect_ids: tuple[str, ...]
    buff_definition_ids: tuple[str, ...]
    skill_level_bonuses: tuple[SkillLevelBonus, ...]
    source: CatalogSource


@dataclass(frozen=True, slots=True)
class CostItem:
    item_id: str
    quantity: float
    hidden_amount: bool


@dataclass(frozen=True, slots=True)
class SkillLevel:
    level: int
    required_breakthrough_stage: int
    required_awaken_level: int
    costs: tuple[CostItem, ...]


@dataclass(frozen=True, slots=True)
class SkillDescription:
    ordinal: int
    description_type: str | None
    title_zh: str | None
    description_zh: str | None
    short_description_zh: str | None
    unlock_id: str | None
    unlock_description_zh: str | None


@dataclass(frozen=True, slots=True)
class SkillLevelHint:
    ordinal: int
    name_id: str | None
    description_zh: str | None
    value_description_zh: str | None
    global_curve_id: str | None
    source_type: str | None
    damage_effect_ids: tuple[str, ...]
    defense_effect_ids: tuple[str, ...]
    health_effect_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillDamageItem:
    damage_id: str
    damage_type: str


@dataclass(frozen=True, slots=True)
class CharacterSkill:
    skill_id: str
    name_zh: str | None
    ability_type: str
    ability_index: int
    show_detail_info: bool
    gameplay_tag: str | None
    gameplay_effect_path: str | None
    gameplay_ability_path: str | None
    reapply_after_revive: bool
    icon_path: str | None
    extended_icon_path: str | None
    levels: tuple[SkillLevel, ...]
    descriptions: tuple[SkillDescription, ...]
    level_hints: tuple[SkillLevelHint, ...]
    damage_items: tuple[SkillDamageItem, ...]
    ability_source: CatalogSource
    effect_source: CatalogSource | None


@dataclass(frozen=True, slots=True)
class CultivationStage:
    ordinal: int
    character_level: int
    fork_level: int
    core_item_id: str
    core_level: int
    equipment_level: int
    recommended_skills: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True, slots=True)
class CultivationGuide:
    s_score: float
    a_score: float
    icon_path: str | None
    recommend_attribute_jump_id: str | None
    fork_recommendations: tuple[tuple[str, str, str | None], ...]
    attribute_recommendations: tuple[tuple[str, str], ...]
    stages: tuple[CultivationStage, ...]
    source: CatalogSource


@dataclass(frozen=True, slots=True)
class GraduationTemplate:
    source_kind: str
    fork_id: str | None
    fork_name_zh: str | None
    fork_level: int | None
    fork_refinement_level: int | None
    core_suit_id: str | None
    core_suit_name_zh: str | None
    core_main_property_id: str | None
    core_main_property_name_zh: str | None
    drive_area: int
    extra_shape_count: int
    benchmark_damage: float
    generated_at_utc: str
    fork_paths: tuple[str, ...]
    fork_source: CatalogSource | None
    core_main_stats: tuple["BuildProperty", ...] = ()
    drive_template_stats: tuple["BuildProperty", ...] = ()


@dataclass(frozen=True, slots=True)
class BuildProperty:
    property_id: str
    display_name: str | None
    value: float | None = None
    show_percent: bool = False


@dataclass(frozen=True, slots=True)
class EquipmentPlanModule:
    ordinal: int
    item_id: str
    display_name: str | None
    shape_id: str | None
    grid_count: int
    anchor_row: int | None
    anchor_column: int | None
    occupied_cells: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class CharacterEquipmentPlan:
    core_item_id: str
    core_level: int
    module_level: int
    cells: tuple[tuple[int, int, int | None], ...]
    modules: tuple[EquipmentPlanModule, ...]
    core_attributes: tuple[BuildProperty, ...]
    recommended_attributes: tuple[BuildProperty, ...]


@dataclass(frozen=True, slots=True)
class CharacterShapeBonus:
    shape_label: str
    shape_grid_count: int
    properties: tuple[BuildProperty, ...]


@dataclass(frozen=True, slots=True)
class CharacterWeightRecommendation:
    properties: tuple[tuple[BuildProperty, float, float], ...]


@dataclass(frozen=True, slots=True)
class CombatLink:
    relationship_kind: str
    binding_kind: str
    input_id: str | None
    ability_id: str | None
    ability_asset_path: str | None
    event_tag: str | None
    gameplay_effect_id: str | None
    gameplay_effect_index: int | None
    effect_asset_path: str | None
    gameplay_effect_class_path: str | None
    target_type_asset_path: str | None
    buff_definition_id: str | None
    buff_definition_kind: str | None
    duration_policy: str | None
    stacking_type: str | None
    stack_limit_count: int | None
    source: CatalogSource


@dataclass(frozen=True, slots=True)
class CombatLinkPage:
    character_id: int
    offset: int
    limit: int
    total: int
    items: tuple[CombatLink, ...]


@dataclass(frozen=True, slots=True)
class CharacterDetail:
    dataset: CatalogDataset
    character: CharacterSummary
    name_text_table: str | None
    name_text_key: str | None
    annotation_source: str | None
    breakthroughs: tuple[BreakthroughStage, ...]
    likeability: LikeabilityBonus | None
    awakenings: tuple[AwakeningEffect, ...]
    skills: tuple[CharacterSkill, ...]
    cultivation: CultivationGuide | None
    graduation: GraduationTemplate | None
    equipment_plan: CharacterEquipmentPlan | None
    shape_bonus: CharacterShapeBonus | None
    recommended_weights: CharacterWeightRecommendation | None
    growth_count: int
    combat_link_count: int
    gaps: tuple[CatalogGap, ...]
