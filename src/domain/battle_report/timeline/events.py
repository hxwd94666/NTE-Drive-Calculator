# 保存微秒时间轴的逐击原文证据和带来源的派生事件，两者保持分离。
from __future__ import annotations

# 时间戳与区间统一为微秒；派生事件只引用原始 event_id，不改写原生信封。
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleHitFieldEvidence:
    """分析 Core 给出的逐字段证据判断，与公式拟合结果分离。"""

    critical_state: Literal["critical", "non_critical", "not_applicable", "unknown"]
    critical_source: str
    critical_confidence: str
    damage_attribute_source: str
    damage_attribute_confidence: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleNativeHitEvidence:
    """无损保存采集信封 JSON；原始证据不是公式修正项。"""

    payload_json: str
    static_names: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BattleObservedTimeStopInterval:
    """一次采集时停区间；缺类型掩码保留旧版或压缩后的未知状态。"""

    start_us: int | None
    end_us: int | None
    pause_type_mask: int | None = None
    type_status: Literal["typed", "legacy_unknown", "compacted_unknown"] = (
        "legacy_unknown"
    )


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
    # 公式消费者上下文仅存在于派生副本，不改写 Core 原始逐击归属。
    is_formal_follow_up: bool = False
    target_has_weave: bool = False
    formula_context_kind: str = ""
    formula_context_confidence: str = ""
    formula_context_basis: str = ""
    native_evidence: BattleNativeHitEvidence | None = None
    field_evidence: BattleHitFieldEvidence | None = None


@dataclass(frozen=True, slots=True)
class BattleInferredAction:
    """以不可变逐击为证据的派生动作区间，不改写原始命中。"""

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
class BattleLinkoCoattackInference:
    """Versioned formula context inferred for one Linko-associated teammate QTE."""

    event_id: str
    trigger_kind: str
    action_character_id: int
    definition_owner_character_id: int
    panel_character_id: int
    skill_level_character_id: int
    skill_level_ability_id: str
    damage_attribute_source_character_id: int
    damage_attribute: str
    damage_attribute_source: str
    confidence: str
    inference_basis: str
    evidence_event_ids: tuple[str, ...]
    trigger_action_id: str = ""
    qte_action_id: str = ""
    raw_gap_us: int | None = None
    active_gap_us: int | None = None
    time_stop_source_kind: str = "none"
    time_stop_confidence: str = ""
    selection_pause_start_us: int | None = None
    selection_pause_end_us: int | None = None


@dataclass(frozen=True, slots=True)
class BattleTreatmentEvent:
    """从来源侧证据派生的一次治疗事件，原始逐击保持不变。"""

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
