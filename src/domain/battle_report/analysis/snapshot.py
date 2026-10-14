# 组合冻结战报分析结果，只运输已确认事实与版本，不执行分析或读取数据库。
from __future__ import annotations

# 组合快照只依赖本域值模型；来源/算法版本与缺口随该次冻结输入传递。
from dataclasses import dataclass
import src.domain.battle_report.counterfactual.results as battle_counterfactual
from src.domain.battle_report.analysis.composition import BattleRangeRoleSummary, BattleRangeSkillSummary
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.domain.battle_report.build.inputs import BattleCharacterBaseline, BattleInferredCharacterFact
from src.domain.battle_report.counterfactual.results import BattleBuffCounterfactualResult
from src.domain.battle_report.replay.models import BattleHitReplayResult
from src.domain.battle_report.targets.profiles import (
    BattleMaxHpReductionEvent, BattleTargetCondition, BattleTargetInstanceResolution, BattleTargetSummary,
)
from src.domain.battle_report.timeline.events import (
    BattleAnalysisHit, BattleInferredAction, BattleInferredInput, BattleLinkoCoattackInference,
    BattleObservedTimeStopInterval, BattleTimelineDamageGroup, BattleTreatmentEvent,
)


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
    linko_coattack_inferences: tuple[BattleLinkoCoattackInference, ...] = ()
    linko_coattack_inference_version: str = ""
    linko_coattack_buff_model_version: str = ""
    treatment_events: tuple[BattleTreatmentEvent, ...] = ()
    treatment_event_model_version: str = ""
    timeline_buff_intervals: tuple[BattleInferredBuffInterval, ...] = ()
    buff_intervals: tuple[BattleInferredBuffInterval, ...] = ()
    buff_inference_version: str = ""
    buff_attribute_projection_version: str = ""
    outer_realm_buff_model_version: str = ""
    time_stop_intervals: tuple[tuple[int | None, int | None], ...] = ()
    observed_time_stop_intervals: tuple[tuple[int | None, int | None], ...] = ()
    observed_typed_time_stop_intervals: tuple[
        BattleObservedTimeStopInterval, ...
    ] = ()
    q_action_time_stop_intervals: tuple[tuple[int, int], ...] = ()
    type6_time_stop_intervals: tuple[tuple[int, int], ...] = ()
    inferred_linko_e_time_stop_intervals: tuple[tuple[int, int], ...] = ()
    has_unknown_time_stop_types: bool = False
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
