# 定义战报服务与展示层共享的不可变领域值。
"""Immutable battle-report values shared by services and presentation code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import src.domain.battle_counterfactual as battle_counterfactual
from src.domain.battle_buff_counterfactual import BattleBuffCounterfactualResult
from src.domain.battle_target import (
    BattleSelectedTargetProfile,
    BattleTargetInstanceResolution,
)


@dataclass(frozen=True, slots=True)
class BattleCharacterSummary:
    character_id: int
    name: str
    hits: int
    damage: float
    dps: float
    damage_share_percent: float
    hits_taken: int = 0
    damage_taken: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleSkillSummary:
    character_id: int
    character_name: str
    name: str
    category: str
    hits: int
    damage: float
    damage_share_percent: float
    ability_name: str | None = None
    gameplay_effect_name: str | None = None
    is_follow_up: bool = False


@dataclass(frozen=True, slots=True)
class BattleAbyssHalfSummary:
    half: str
    duration_seconds: float
    total_damage: float
    total_dps: float
    characters: tuple[BattleCharacterSummary, ...]
    skills: tuple[BattleSkillSummary, ...]


@dataclass(frozen=True, slots=True)
class BattleAbyssSummary:
    detected: bool = False
    floor: int | None = None
    active_half: str | None = None
    success: bool = False
    first_half: BattleAbyssHalfSummary | None = None
    second_half: BattleAbyssHalfSummary | None = None


@dataclass(frozen=True, slots=True)
class BattleQualitySummary:
    source: str = "unknown"
    packet_count: int = 0
    packets_with_hits: int = 0
    hit_count: int = 0
    outgoing_hits: int = 0
    incoming_hits: int = 0
    unknown_direction_hits: int = 0
    unknown_character_count: int = 0
    unknown_character_hits: int = 0
    unmapped_skill_rows: int = 0
    unmapped_skill_hits: int = 0
    unmapped_gameplay_effect_count: int = 0


@dataclass(frozen=True, slots=True)
class BattleSummary:
    duration_seconds: float
    dps_time_mode: str
    total_damage: float
    total_dps: float
    total_damage_taken: float
    total_hits: int
    characters: tuple[BattleCharacterSummary, ...]
    skills: tuple[BattleSkillSummary, ...]
    abyss: BattleAbyssSummary
    quality: BattleQualitySummary
    max_hp_reduction: float = 0.0
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class BattleCaptureState:
    phase: str
    message: str
    running: bool
    summary: BattleSummary | None = None
    error: str | None = None
    error_code: str | None = None
    persistence_status: str = "not_requested"
    battle_record_id: int | None = None
    retention_kind: Literal["auto", "manual"] | None = None


@dataclass(frozen=True, slots=True)
class BattleSummaryPersistenceOutcome:
    status: Literal["saved", "skipped_empty", "discarded_stale"]
    battle_record_id: int | None = None
    pruned_battle_record_ids: tuple[int, ...] = ()
    retention_kind: Literal["auto", "manual"] | None = None


@dataclass(frozen=True, slots=True)
class StoredBattleSummary:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    saved_at_utc: str
    detail_scope: Literal["current", "first", "second"]
    summary: BattleSummary
    nte_core_version: str | None = None
    nte_core_protocol_version: int | None = None
    nte_core_data_version: str | None = None
    nte_core_executable_sha256: str | None = None
    analysis_start_us: int | None = None
    analysis_end_us: int | None = None
    analysis_character_id: int | None = None


@dataclass(frozen=True, slots=True)
class BattleReportHistoryEntry:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    saved_at_utc: str
    combat_context_kind: Literal["abyss", "non_abyss"]
    abyss_floor: int | None
    has_first_half: bool
    has_second_half: bool
    character_ids: tuple[int, ...]
    total_damage: float
    total_dps: float
    duration_seconds: float
    total_hits: int
    capability_level: str
    source_kind: str
    environment_name: str = ""
    environment_source: Literal["", "user_confirmed", "inferred"] = ""
    environment_confidence: str = ""


@dataclass(frozen=True, slots=True)
class BattleRetentionMutation:
    battle_record_id: int
    retention_kind: Literal["auto", "manual"]
    changed: bool
    pruned_battle_record_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DamageCompositionEntry:
    key: str
    label: str
    damage: float
    share_percent: float
    total_share_percent: float = 0.0


@dataclass(frozen=True, slots=True)
class RoleDamageComposition:
    character_id: int
    character_name: str
    total_damage: float
    entries: tuple[DamageCompositionEntry, ...]
    share_percent: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleDamageComposition:
    roles: tuple[RoleDamageComposition, ...]
    other_total_damage: float
    other_share_percent: float
    other_entries: tuple[DamageCompositionEntry, ...]
    system_total_damage: float = 0.0
    system_share_percent: float = 0.0
    system_entries: tuple[DamageCompositionEntry, ...] = ()
    pending_topple_attribution: bool = False
    unresolved_topple_attribution: bool = False


@dataclass(frozen=True, slots=True)
class BattleAnalysisHit:
    event_id: str
    sequence: int
    relative_time_us: int
    character_id: int | None
    character_name: str
    skill_name: str
    damage_name: str
    damage_component: str
    attack_type: str
    damage_attribute: str
    target_id: str
    target_name: str
    damage: float
    direction: str
    is_follow_up: bool
    classification: str
    ability_id: str = ""
    gameplay_effect_id: str = ""
    scope_half: str = ""
    target_hp_before: float | None = None
    target_hp_after: float | None = None
    target_max_hp: float | None = None
    raw_damage: float | None = None
    overkill_damage: float | None = None
    damage_correction_kind: str = ""
    damage_correction_confidence: str = ""
    damage_correction_basis: str = ""
    damage_overlap_correction: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleInferredAction:
    """Derived action window backed by one or more immutable damage hits."""

    action_id: str
    character_id: int
    character_name: str
    action_name: str
    input_kind: str
    input_sequence: str
    start_us: int
    end_us: int
    hits: int
    damage: float
    identity_confidence: str
    timing_confidence: str
    inference_basis: str
    evidence_event_ids: tuple[str, ...]
    gameplay_effect_ids: tuple[str, ...]
    input_gesture: Literal["tap", "hold"] = "tap"
    input_start_us: int | None = None
    input_end_us: int | None = None
    hold_damage_mode: Literal["none", "during_hold", "after_hold"] = "none"


@dataclass(frozen=True, slots=True)
class BattleTreatmentEvent:
    """One source-side treatment occurrence derived without mutating raw hits."""

    event_id: str
    relative_time_us: int
    source_character_id: int
    source_character_name: str = ""
    source_action_id: str = ""
    treatment_kind: str = ""
    target_scope: str = "team"
    evidence_kind: str = "formal_skill"
    confidence: str = "中"
    evidence_event_ids: tuple[str, ...] = ()
    inference_basis: str = ""
    target_character_ids: tuple[int, ...] = ()
    raw_healing_amount: float | None = None
    effective_healing_amount: float | None = None
    is_periodic: bool = False
    application_tick: int | None = None
    pauses_during_time_stop: bool = True
    amount_basis: str = ""


@dataclass(frozen=True, slots=True)
class BattleInferredInput:
    """Low-confidence input projection backed by one inferred action."""

    input_event_id: str
    action_id: str
    device_kind: Literal["mouse", "keyboard"]
    display_text: str
    character_id: int
    character_name: str
    start_us: int
    end_us: int
    is_character_switch: bool
    timing_confidence: str
    hold_damage_mode: Literal["none", "during_hold", "after_hold"] = "none"


@dataclass(frozen=True, slots=True)
class BattleTimelineDamageGroup:
    """One stable skill/channel damage window with immutable hit references."""

    group_id: str
    character_id: int | None
    character_name: str
    direction: str
    channel_key: str
    channel_label: str
    damage_name: str
    source_skill_name: str
    ability_id: str
    start_us: int
    end_us: int
    hits: int
    damage: float
    evidence_event_ids: tuple[str, ...]
    detail_lines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleRangeRoleSummary:
    character_id: int
    character_name: str
    hits: int
    damage: float
    dps: float
    share_percent: float
    raw_damage: float = 0.0
    max_hp_reduction_damage: float = 0.0
    max_hp_reduction_events: int = 0


@dataclass(frozen=True, slots=True)
class BattleRangeSkillSummary:
    character_id: int | None
    character_name: str
    skill_name: str
    damage_name: str
    classification: str
    hits: int
    damage: float
    share_percent: float
    ability_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleTargetSummary:
    target_id: str
    target_name: str
    hits: int
    damage: float
    first_hp: float | None
    last_hp: float | None
    max_hp: float | None
    max_hp_reduction: float = 0.0
    max_hp_reduction_damage: float = 0.0
    effective_damage: float = 0.0
    estimated_max_hp_reduction_damage: float = 0.0
    scope_half: str = ""
    initial_hp: float | None = None
    terminal_hp: float | None = None
    observed_hp_loss: float = 0.0
    unexplained_hp_delta: float = 0.0


@dataclass(frozen=True, slots=True)
class BattleTargetCondition:
    """One saved encounter plus the legacy primary-target display profile."""

    target_name: str
    enemy_level: float
    scene: str
    defense_reduction: float
    vulnerability: float
    resistances: tuple[tuple[str, float], ...]
    source_kind: str = "user_confirmed"
    enemy_defense_base: float | None = None
    enemy_defense_up: float = 0.0
    enemy_defense_add: float = 0.0
    enemy_topple_limit: float = 50.0
    environment_kind: str = "manual"
    environment_ref: str = ""
    environment_name: str = ""
    selected_target_ids: tuple[str, ...] = ()
    primary_target_id: str = ""
    difficulty_id: int | None = None
    feast_options: tuple[tuple[str, str], ...] = ()
    witch_buff_id: str = ""
    witch_buff_name_zh: str = ""
    witch_buff_property_id: str = ""
    witch_buff_value: float | None = None
    witch_buff_is_percent: bool = False
    selected_target_profiles: tuple["BattleSelectedTargetProfile", ...] = ()
    resolved_monster_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleMaxHpReductionEvent:
    """Observed target-max-HP transition with separately inferred attribution."""

    event_id: str
    target_id: str
    target_name: str
    observed_at_us: int
    old_max_hp: float
    new_max_hp: float
    max_hp_reduction: float
    hp_before_settlement: float
    hp_ratio_before: float
    effective_hp_loss: float
    source_character_id: int | None
    source_character_name: str
    mechanic_kind: str
    mechanic_name: str
    source_skill_name: str
    evidence_event_ids: tuple[str, ...]
    attribution_confidence: str
    calculation_confidence: str
    inference_basis: str
    evidence_kind: str = "observed"
    included_in_effective_damage: bool = True
    scope_half: str = ""


@dataclass(frozen=True, slots=True)
class BattleBuffModifierEvidence:
    """One normalized modifier carried by an inferred Buff interval."""

    property_id: str
    modifier_operation: str
    magnitude_kind: str
    magnitude_value: float | None
    calculation_asset_path: str
    value_confidence: str
    modifier_group_ordinal: int = 0
    application_requirement_asset_path: str = ""
    source_require_tags: tuple[str, ...] = ()
    source_ignore_tags: tuple[str, ...] = ()
    target_require_tags: tuple[str, ...] = ()
    target_ignore_tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleInferredBuffInterval:
    """Deletable Buff-state inference; never an observed runtime fact."""

    interval_id: str
    buff_asset_path: str
    buff_name: str
    source_effect_definition_id: str
    source_kind: str
    source_character_id: int
    source_character_name: str
    target_scope: str
    start_us: int
    end_us: int
    stacks: int
    duration_policy: str
    state_confidence: str
    value_confidence: str
    inference_basis: str
    trigger_event_type: str
    evidence_action_ids: tuple[str, ...]
    evidence_event_ids: tuple[str, ...]
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = ""
    stack_limit_count: int = 1
    target_id: str = ""


@dataclass(frozen=True, slots=True)
class BattleProjectedBuffModifier:
    """One safe additive modifier projected onto a concrete damage hit."""

    property_id: str
    additive_value: float
    interval_ids: tuple[str, ...]
    buff_names: tuple[str, ...]
    confidence: str
    target_scope: str = "self"


@dataclass(frozen=True, slots=True)
class BattleBuffProjectionDecision:
    """Per-interval decision explaining whether one hit consumed the evidence."""

    interval_id: str
    buff_name: str
    status: Literal["applied", "not_applied", "unresolved"]
    applied_property_ids: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BattleHitBuffProjection:
    """Derived per-hit Buff input; excluded evidence remains explainable."""

    event_id: str
    modifiers: tuple[BattleProjectedBuffModifier, ...]
    applied_interval_ids: tuple[str, ...]
    excluded_interval_ids: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    confidence: str
    decisions: tuple[BattleBuffProjectionDecision, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleCharacterStat:
    property_id: str
    label: str
    value: float
    is_percent: bool


@dataclass(frozen=True, slots=True)
class BattleCharacterSourceStat:
    """One frozen formula input before role-panel sources are combined."""

    source_group: str
    source_name: str
    property_id: str
    label: str
    value: float
    is_percent: bool


@dataclass(frozen=True, slots=True)
class BattleCharacterBaseline:
    character_id: int
    character_name: str
    source: str
    stats: tuple[BattleCharacterStat, ...]
    character_level: float = 80.0
    source_stats: tuple[BattleCharacterSourceStat, ...] = ()
    inherent_hp: float | None = None
    source_max_hp: float | None = None
    enabled_team_passive_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleSkillDamageEvidence:
    """One level-resolved static damage row used by hit replay."""

    event_id: str
    damage_id: str
    ability_id: str
    damage_attribute: str
    damage_source_category: str
    fixed_crit_rate: float
    scaling_property_id: str
    scaling_multiplier: float
    multiplier_coefficient: float
    effective_skill_level: int
    evidence_basis: str
    source_character_id: int | None = None
    formula_kind: str = "skill"
    level_multiplier: float | None = None
    state_multiplier: float = 1.0
    state_multiplier_label: str = ""
    state_multiplier_basis: str = ""
    state_confidence: str = ""
    dot_final_multiplier: float = 1.0
    dot_final_multiplier_basis: str = ""
    critical_policy: Literal["character", "fixed", "disabled", "unknown"] = "character"
    skill_final_multiplier: float = 1.0
    skill_final_multiplier_basis: str = ""


@dataclass(frozen=True, slots=True)
class BattleHitReplayFactor:
    """Named factor retained for explaining one replay candidate."""

    factor_id: str
    label: str
    value: float
    evidence_basis: str
    formula: str = ""
    terms: tuple["BattleHitReplayTerm", ...] = ()


@dataclass(frozen=True, slots=True)
class BattleHitReplayTerm:
    """One source-addressable value inside a replay factor."""

    term_id: str
    property_id: str
    label: str
    value: float
    source_group: str
    source_name: str
    is_percent: bool
    evidence_basis: str


@dataclass(frozen=True, slots=True)
class BattleHitReplayResult:
    """Observed hit compared with non-critical and critical formula candidates."""

    event_id: str
    observed_damage: float
    non_critical_damage: float | None
    critical_damage: float | None
    selected_damage: float | None
    selected_error_percent: float | None
    critical_state: Literal[
        "critical",
        "non_critical",
        "not_applicable",
        "ambiguous",
        "unreplayable",
    ]
    confidence: str
    factors: tuple[BattleHitReplayFactor, ...]
    missing_evidence: tuple[str, ...] = ()
    formula_type: str = "未分类"
    critical_rate: float | None = None
    expected_damage: float | None = None
    corrected_expected_damage: float | None = None
    signed_error_percent: float | None = None
    critical_policy: Literal[
        "character", "fixed", "disabled", "unknown"
    ] = "unknown"
    reported_damage: float | None = None
    observed_damage_source: Literal[
        "reported_hit",
        "reported_hit_before_overkill",
        "target_hp_transition_remainder",
    ] = "reported_hit"
    observed_damage_basis: str = ""
    # 正式重放解析出的公式属性；用于原始逐击属性缺失或被通用标签污染时。
    formula_damage_attribute: str = ""


@dataclass(frozen=True, slots=True)
class BattleInferredCharacterFact:
    fact_id: str
    character_id: int
    fact_kind: str
    fact_value: str
    source_gameplay_effect_id: str
    confidence: str
    evidence_event_ids: tuple[str, ...]
    model_version: str
    inference_basis: str


@dataclass(frozen=True, slots=True)
class BattleAnalysisSnapshot:
    battle_record_id: int
    capability_level: str
    axis_complete: bool
    formula_model_version: str
    name_mapping_version: str
    action_inference_version: str
    timeline_projection_version: str
    battle_start_us: int
    battle_end_us: int
    timeline_end_us: int
    range_start_us: int
    range_end_us: int
    duration_seconds: float
    total_damage: float
    total_dps: float
    timeline_hits: tuple[BattleAnalysisHit, ...]
    inferred_actions: tuple[BattleInferredAction, ...]
    inferred_inputs: tuple[BattleInferredInput, ...]
    timeline_damage_groups: tuple[BattleTimelineDamageGroup, ...]
    hits: tuple[BattleAnalysisHit, ...]
    roles: tuple[BattleRangeRoleSummary, ...]
    skills: tuple[BattleRangeSkillSummary, ...]
    targets: tuple[BattleTargetSummary, ...]
    baselines: tuple[BattleCharacterBaseline, ...]
    treatment_events: tuple[BattleTreatmentEvent, ...] = ()
    treatment_event_model_version: str = ""
    timeline_buff_intervals: tuple[BattleInferredBuffInterval, ...] = ()
    buff_intervals: tuple[BattleInferredBuffInterval, ...] = ()
    buff_inference_version: str = ""
    buff_attribute_projection_version: str = ""
    outer_realm_buff_model_version: str = ""
    time_stop_intervals: tuple[tuple[int | None, int | None], ...] = ()
    observed_time_stop_intervals: tuple[tuple[int | None, int | None], ...] = ()
    time_stop_source_kind: str = "none"
    time_stop_confidence: str = ""
    time_stop_inference_basis: str = ""
    time_stop_projection_version: str = ""
    timeline_max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = ()
    max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = ()
    max_hp_reduction_damage: float = 0.0
    effective_damage: float = 0.0
    effective_dps: float = 0.0
    target_vital_model_version: str = ""
    target_identity_mode: str = "unknown"
    timeline_estimated_max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = ()
    estimated_max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = ()
    estimated_max_hp_reduction_damage: float = 0.0
    target_condition: BattleTargetCondition | None = None
    target_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()
    target_instance_resolutions: tuple[BattleTargetInstanceResolution, ...] = ()
    target_instance_mapping_required: bool = False
    hit_replays: tuple[BattleHitReplayResult, ...] = ()
    hit_replay_model_version: str = ""
    buff_counterfactuals: tuple[BattleBuffCounterfactualResult, ...] = ()
    buff_counterfactual_model_version: str = ""
    passive_counterfactuals: tuple[BattleBuffCounterfactualResult, ...] = ()
    passive_counterfactual_model_version: str = ""
    detected_environment_kind: str = ""
    detected_environment_ref: str = ""
    detected_environment_name: str = ""
    detected_environment_difficulty_id: int | None = None
    detected_environment_options: tuple[tuple[str, str], ...] = ()
    detected_outer_realm_floor: int | None = None
    target_identity_inference_source: str = ""
    target_identity_inference_confidence: str = ""
    target_identity_inference_basis: str = ""
    target_identity_inference_ambiguous: bool = False
    target_identity_inference_alternatives: tuple[str, ...] = ()
    damage_correction_total: float = 0.0
    timeline_damage_correction_total: float = 0.0
    damage_overlap_correction_total: float = 0.0
    timeline_damage_overlap_correction_total: float = 0.0
    raw_total_damage: float = 0.0
    build_counterfactual: battle_counterfactual.BattleBuildCounterfactual | None = None
    inferred_character_facts: tuple[BattleInferredCharacterFact, ...] = ()


EMPTY_BATTLE_CAPTURE_STATE = BattleCaptureState(
    phase="stopped",
    message="尚未开始战报采集。",
    running=False,
)


def active_abyss_half(summary: BattleSummary) -> BattleAbyssHalfSummary | None:
    """Return the currently active half without making UI code parse labels."""

    active = (summary.abyss.active_half or "").lower()
    if "ascending" in active or "first" in active or "上" in active:
        return summary.abyss.first_half
    if "descending" in active or "second" in active or "下" in active:
        return summary.abyss.second_half
    return None
