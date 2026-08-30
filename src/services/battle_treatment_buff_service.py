# 将统一治疗事件投影为正式的角色技能与被动 Buff 区间。
"""Treatment-event consumers for fixed-axis Buff replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


TREATMENT_BUFF_MODEL_VERSION = "battle-treatment-buff-v1"

_ONEIROI_ID = 1075
_ONEIROI_E_ATTACK_DURATION_US = 20_000_000
_ONEIROI_E_ATTACK_RATIO = 0.20
_ONEIROI_E_ATTACK_CAP = 500.0
_ONEIROI_PASSIVE_DURATION_US = 20_000_000
_ONEIROI_EFFECT5_ATTACK_RATIO = 0.15
_ONEIROI_EFFECT5_ATTACK_CAP = 500.0


@dataclass(slots=True)
class _TreatmentChain:
    start_us: int
    end_active_us: int
    action_ids: list[str]
    event_ids: list[str]


def _character(
    build: Mapping[str, Any] | None,
    character_id: int,
) -> Mapping[str, Any] | None:
    return next((
        row for row in (build or {}).get("characters") or ()
        if int(row.get("character_id") or 0) == character_id
    ), None)


def _stage(character: Mapping[str, Any]) -> int:
    profile = character.get("profile") or {}
    return int(
        character.get("breakthrough_stage")
        or profile.get("breakthrough_stage")
        or 0
    )


def _base_attack(character: Mapping[str, Any]) -> float | None:
    values = tuple(
        float(row.get("value") or 0.0)
        for row in character.get("stats") or ()
        if str(row.get("property_id") or "") == "AtkBase"
        and str(row.get("source_group") or "") in {"character", "fork"}
    )
    return sum(values) if values else None


def _effect_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile") or {}
    profile = profile if isinstance(profile, Mapping) else {}
    selected = {
        str(value).casefold()
        for value in profile.get("selected_awaken_effect_ids") or ()
    }
    if bool(profile.get("awakening_selection_initialized")):
        return effect_id.casefold() in selected
    ordinal = int(effect_id.removeprefix("Effect") or 0)
    return int(
        character.get("awakening_level")
        or profile.get("awakening_level")
        or 0
    ) >= ordinal


def _active_time(
    raw_time_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_time_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_expiry(
    active_time_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        active_time_us,
        battle_start_us=0,
        battle_end_us=battle_end_us,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
        prefer_interval_end=True,
    )


def _chains(
    events: Sequence[BattleTreatmentEvent],
    *,
    duration_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[_TreatmentChain, ...]:
    chains: list[_TreatmentChain] = []
    for event in sorted(events, key=lambda row: (row.relative_time_us, row.event_id)):
        active_us = _active_time(event.relative_time_us, time_stop_intervals)
        proposed_end_us = active_us + duration_us
        if chains and active_us <= chains[-1].end_active_us:
            chains[-1].end_active_us = proposed_end_us
            if event.source_action_id:
                chains[-1].action_ids.append(event.source_action_id)
            chains[-1].event_ids.append(event.event_id)
            continue
        chains.append(_TreatmentChain(
            start_us=event.relative_time_us,
            end_active_us=proposed_end_us,
            action_ids=[event.source_action_id] if event.source_action_id else [],
            event_ids=[event.event_id],
        ))
    return tuple(chains)


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _modifier(
    property_id: str,
    value: float,
    *,
    magnitude_kind: str,
    calculation_asset_path: str,
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind=magnitude_kind,
        magnitude_value=value,
        calculation_asset_path=calculation_asset_path,
        value_confidence="高",
    )


def _intervals(
    *,
    events: Sequence[BattleTreatmentEvent],
    duration_us: int,
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    interval_prefix: str,
    buff_asset_path: str,
    buff_name: str,
    source_effect_definition_id: str,
    source_character_name: str,
    target_scope: str,
    trigger_event_type: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    inference_basis: str,
) -> tuple[BattleInferredBuffInterval, ...]:
    results: list[BattleInferredBuffInterval] = []
    for ordinal, chain in enumerate(_chains(
        events,
        duration_us=duration_us,
        time_stop_intervals=time_stop_intervals,
    )):
        end_us = min(battle_end_us, _raw_expiry(
            chain.end_active_us,
            battle_end_us=battle_end_us,
            intervals=time_stop_intervals,
        ))
        if end_us <= chain.start_us:
            continue
        results.append(BattleInferredBuffInterval(
            interval_id=f"buff:treatment:{interval_prefix}:{ordinal}",
            buff_asset_path=buff_asset_path,
            buff_name=buff_name,
            source_effect_definition_id=source_effect_definition_id,
            source_kind="formal_treatment_consumer",
            source_character_id=_ONEIROI_ID,
            source_character_name=source_character_name,
            target_scope=target_scope,
            start_us=chain.start_us,
            end_us=end_us,
            stacks=1,
            duration_policy="HasDuration",
            state_confidence="中",
            value_confidence="高",
            inference_basis=inference_basis,
            trigger_event_type=trigger_event_type,
            evidence_action_ids=_unique(chain.action_ids),
            evidence_event_ids=_unique(chain.event_ids),
            modifiers=modifiers,
            stacking_type="AggregateByTarget",
            stack_limit_count=1,
        ))
    return tuple(results)


class BattleTreatmentBuffService:
    """Materialize only consumers backed by an emitted treatment event."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        treatment_events: Sequence[BattleTreatmentEvent],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = _character(build, _ONEIROI_ID)
        if character is None:
            return ()
        source_name = str(character.get("observed_name") or "伊洛伊")
        events = tuple(
            row for row in treatment_events
            if row.source_character_id == _ONEIROI_ID
        )
        results: list[BattleInferredBuffInterval] = []
        if _stage(character) >= 4:
            results.extend(_intervals(
                events=events,
                duration_us=_ONEIROI_PASSIVE_DURATION_US,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                interval_prefix="oneiroi-passive-2",
                buff_asset_path=(
                    "/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/"
                    "PassiveEffect/Buff_Oneiroi075_Passive2_DamUP"
                ),
                buff_name="交感性神经系统",
                source_effect_definition_id=(
                    "character_passive:1075:GA_Oneiroi_Passive_2"
                ),
                source_character_name=source_name,
                target_scope="team",
                trigger_event_type="BUFF_EVENT_TREATMENT",
                modifiers=(_modifier(
                    "DefIgnore",
                    0.05,
                    magnitude_kind="formal_character_passive",
                    calculation_asset_path=(
                        "/Game/Blueprints/Abilities/Calculation/Oneiroi/"
                        "Calc_Oneiroi_Passive2_DefIgnore"
                    ),
                ),),
                inference_basis=(
                    "正式被动监听 BUFF_EVENT_TREATMENT；每次来源侧治疗事件刷新"
                    "全队 20 秒无视防御，满血或零有效治疗仍视为治疗事件。"
                ),
            ))
        base_attack = _base_attack(character)
        e_events = tuple(
            row for row in events if row.treatment_kind == "oneiroi_e_tap"
        )
        if base_attack is not None and e_events:
            attack_add = min(
                base_attack * _ONEIROI_E_ATTACK_RATIO,
                _ONEIROI_E_ATTACK_CAP,
            )
            results.extend(_intervals(
                events=e_events,
                duration_us=_ONEIROI_E_ATTACK_DURATION_US,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                interval_prefix="oneiroi-e-attack",
                buff_asset_path=(
                    "/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/"
                    "Buff/Buff_Oneiroi_Skill_AtkUP"
                ),
                buff_name="伊洛伊 E：全队攻击力",
                source_effect_definition_id="character_skill:1075:GA_Oneiroi_Skill",
                source_character_name=source_name,
                target_scope="team",
                trigger_event_type="BUFF_EVENT_TREATMENT|oneiroi_e_tap",
                modifiers=(_modifier(
                    "AtkAdd",
                    attack_add,
                    magnitude_kind="formal_source_atk_base_calculation",
                    calculation_asset_path=(
                        "/Game/DataTable/Skill/GlobalCharacterData/"
                        "DT_OneiroiEffectFigure:Oneiroi_Skill_AtkAdd"
                    ),
                ),),
                inference_basis=(
                    "伊洛伊点按 E 的正式治疗事件同时施加全队固定攻击力："
                    "min(来源基础攻击力 × 20%, 500)，持续 20 秒。"
                ),
            ))
        if (
            base_attack is not None
            and events
            and _effect_enabled(character, "Effect5")
        ):
            effect5_attack_add = min(
                base_attack * _ONEIROI_EFFECT5_ATTACK_RATIO,
                _ONEIROI_EFFECT5_ATTACK_CAP,
            )
            results.extend(_intervals(
                events=events,
                duration_us=_ONEIROI_PASSIVE_DURATION_US,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                interval_prefix="oneiroi-effect-5",
                buff_asset_path=(
                    "/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/"
                    "PassiveEffect/Buff_Oneiroi075_Passive2_LV4_DamUP"
                ),
                buff_name="清晰",
                source_effect_definition_id="character_awaken:1075:Effect5",
                source_character_name=source_name,
                target_scope="team",
                trigger_event_type="BUFF_EVENT_TREATMENT",
                modifiers=(_modifier(
                    "AtkAdd",
                    effect5_attack_add,
                    magnitude_kind="formal_source_atk_base_calculation",
                    calculation_asset_path=(
                        "/Game/Blueprints/Abilities/Calculation/Oneiroi/"
                        "Calc_Oneiroi_Passive2_AtkAddLV4"
                    ),
                ),),
                inference_basis=(
                    "伊洛伊五觉在每次正式治疗事件时刷新全队固定攻击力："
                    "min(来源基础攻击力 × 15%, 500)，持续 20 秒。"
                ),
            ))
        return tuple(sorted(
            results,
            key=lambda row: (row.start_us, row.end_us, row.buff_asset_path),
        ))
