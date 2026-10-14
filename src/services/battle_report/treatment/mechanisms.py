# 从冻结动作派生治疗事件，再由同域消费者生成伤害Buff；不改原始轴。
"""Formal treatment-event adapters for fixed-axis battle replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from dataclasses import dataclass

from src.domain.battle_report.timeline.events import BattleAnalysisHit, BattleInferredAction, BattleTreatmentEvent
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence, BattleInferredBuffInterval
from src.services.battle_report.treatment.audited import (
    BattleAuditedTreatmentAdapterService,
)
from src.services.battle_report.timeline.clock import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


TREATMENT_EVENT_MODEL_VERSION = "battle-treatment-event-v2"

_ONEIROI_ID = 1075
_ONEIROI_E_NOTIFY_OFFSET_US = 498_799
_ONEIROI_QTE_NOTIFY_OFFSET_US = 784_675
_ONEIROI_Q_NOTIFY_OFFSET_US = 1_925_956
_ONEIROI_Q_PERIOD_US = 1_000_000
_ONEIROI_Q_PERIOD_COUNT = 14
_ONEIROI_E_FRAGMENT_WINDOW_US = 2_000_000
_ONEIROI_AWAKEN2_COOLDOWN_US = 5_000_000


def _active_time(
    raw_time_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    # 治疗周期与消费者续期共享活动时钟，暂停段不制造额外治疗。
    return project_timeline_time_us(
        raw_time_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_time(
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


def _character(
    build: Mapping[str, Any] | None,
    character_id: int,
) -> Mapping[str, Any] | None:
    for row in (build or {}).get("characters") or ():
        if int(row.get("character_id") or 0) == character_id:
            return row
    return None


def _dedupe_oneiroi_e_actions(
    actions: Sequence[BattleInferredAction],
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[BattleInferredAction, ...]:
    result: list[BattleInferredAction] = []
    previous_active_us: int | None = None
    for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
        if action.input_gesture == "hold":
            continue
        active_us = _active_time(action.start_us, time_stop_intervals)
        if (
            previous_active_us is not None
            and active_us - previous_active_us < _ONEIROI_E_FRAGMENT_WINDOW_US
        ):
            continue
        result.append(action)
        previous_active_us = active_us
    return tuple(result)


def _event(
    action: BattleInferredAction,
    *,
    suffix: str,
    relative_time_us: int,
    treatment_kind: str,
    basis: str,
    is_periodic: bool = False,
    application_tick: int | None = None,
) -> BattleTreatmentEvent:
    return BattleTreatmentEvent(
        event_id=f"treatment:{action.action_id}:{suffix}",
        relative_time_us=relative_time_us,
        source_character_id=action.character_id,
        source_character_name=action.character_name,
        source_action_id=action.action_id,
        treatment_kind=treatment_kind,
        target_scope="team",
        evidence_kind="formal_skill_animation",
        confidence="中",
        evidence_event_ids=action.evidence_event_ids,
        inference_basis=basis,
        is_periodic=is_periodic,
        application_tick=application_tick,
    )


class BattleTreatmentEventService:
    """Derive treatment occurrences before any Buff consumer is evaluated."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        hits: Sequence[BattleAnalysisHit] = (),
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        state_buff_intervals: Sequence[BattleInferredBuffInterval] = (),
        zankou_effect_three_recover_ratio: float | None = None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        results: list[BattleTreatmentEvent] = []
        if _character(build, _ONEIROI_ID) is not None:
            oneiroi_actions = tuple(
                row for row in actions if row.character_id == _ONEIROI_ID
            )
            results.extend(cls._oneiroi_instant_events(
                oneiroi_actions,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ))
            results.extend(cls._oneiroi_q_period_events(
                oneiroi_actions,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ))
            results.extend(cls._oneiroi_awaken_two_events(
                build=build,
                actions=actions,
                time_stop_intervals=time_stop_intervals,
            ))
        results.extend(BattleAuditedTreatmentAdapterService.infer(
            build=build,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            state_buff_intervals=state_buff_intervals,
            zankou_effect_three_recover_ratio=(
                zankou_effect_three_recover_ratio
            ),
        ))
        return tuple(sorted(
            results,
            key=lambda row: (row.relative_time_us, row.event_id),
        ))

    @staticmethod
    def _oneiroi_instant_events(
        actions: Sequence[BattleInferredAction],
        *,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        e_actions = _dedupe_oneiroi_e_actions(
            tuple(row for row in actions if row.input_kind == "E"),
            time_stop_intervals,
        )
        candidates = (
            *(
                (row, _ONEIROI_E_NOTIFY_OFFSET_US, "oneiroi_e_tap", "E.1")
                for row in e_actions
            ),
            *(
                (row, _ONEIROI_QTE_NOTIFY_OFFSET_US, "oneiroi_qte", "QTE.2")
                for row in actions
                if row.input_kind == "QTE"
            ),
            *(
                (row, _ONEIROI_Q_NOTIFY_OFFSET_US, "oneiroi_q", "UltraSkill.1")
                for row in actions
                if row.input_kind == "Q"
            ),
        )
        results: list[BattleTreatmentEvent] = []
        for action, offset_us, kind, notify_name in candidates:
            event_time_us = action.start_us + offset_us
            if event_time_us >= battle_end_us:
                continue
            results.append(_event(
                action,
                suffix="instant",
                relative_time_us=event_time_us,
                treatment_kind=kind,
                basis=(
                    f"伊洛伊正式技能在动画事件 {notify_name} 施加治疗；"
                    "事件时点由动作起点与正式动画 Notify 偏移派生。"
                ),
            ))
        return tuple(results)

    @staticmethod
    def _oneiroi_q_period_events(
        actions: Sequence[BattleInferredAction],
        *,
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        results: list[BattleTreatmentEvent] = []
        for action in actions:
            if action.input_kind != "Q":
                continue
            application_us = action.start_us + _ONEIROI_Q_NOTIFY_OFFSET_US
            if application_us >= battle_end_us:
                continue
            application_active_us = _active_time(
                application_us,
                time_stop_intervals,
            )
            for ordinal in range(1, _ONEIROI_Q_PERIOD_COUNT + 1):
                event_time_us = _raw_time(
                    application_active_us + ordinal * _ONEIROI_Q_PERIOD_US,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= battle_end_us:
                    break
                results.append(_event(
                    action,
                    suffix=f"period:{ordinal}",
                    relative_time_us=event_time_us,
                    treatment_kind="oneiroi_q_period",
                    basis=(
                        "伊洛伊 Q 持续治疗正式持续 14 秒、周期 1 秒且施加时不立即执行；"
                        "周期与持续时间使用扣除时停的活动时钟。"
                    ),
                    is_periodic=True,
                    application_tick=ordinal,
                ))
        return tuple(results)

    @staticmethod
    def _oneiroi_awaken_two_events(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = _character(build, _ONEIROI_ID)
        if character is None:
            return ()
        profile = character.get("profile") or {}
        profile = profile if isinstance(profile, Mapping) else {}
        selected = {
            str(value).casefold()
            for value in profile.get("selected_awaken_effect_ids") or ()
        }
        if bool(profile.get("awakening_selection_initialized")):
            enabled = "effect2" in selected
        else:
            enabled = int(
                character.get("awakening_level")
                or profile.get("awakening_level")
                or 0
            ) >= 2
        if not enabled:
            return ()
        source_name = str(character.get("observed_name") or "伊洛伊")
        last_active_us: int | None = None
        results: list[BattleTreatmentEvent] = []
        for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
            if action.character_id == _ONEIROI_ID or action.input_kind != "E":
                continue
            active_us = _active_time(action.start_us, time_stop_intervals)
            if (
                last_active_us is not None
                and active_us - last_active_us < _ONEIROI_AWAKEN2_COOLDOWN_US
            ):
                continue
            last_active_us = active_us
            results.append(BattleTreatmentEvent(
                event_id=f"treatment:oneiroi:awaken2:{action.action_id}",
                relative_time_us=action.start_us,
                source_character_id=_ONEIROI_ID,
                source_character_name=source_name,
                source_action_id=action.action_id,
                treatment_kind="oneiroi_awaken2_team",
                target_scope="team",
                evidence_kind="confirmed_other_character_e",
                confidence="低",
                evidence_event_ids=action.evidence_event_ids,
                inference_basis=(
                    "伊洛伊二觉按当前项目已确认口径由其他角色实际 E 动作触发，"
                    "5 个有效战斗秒冷却；三种随机召唤均只生成一次全队治疗，"
                    "不按召唤攻击 hit 数复制。正式导出未暴露完整调度时点，"
                    "暂锚定到触发 E 动作起点。"
                ),
                amount_basis="伊洛伊结算时攻击力 × 120%；当前事件量保持未知。",
            ))
        return tuple(results)


TREATMENT_BUFF_MODEL_VERSION = "battle-treatment-buff-v1"

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
    # 没有已确认人物/弧盘基础攻击来源时保持未知，禁止当作零属性。
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
        end_us = min(battle_end_us, _raw_time(
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
            evidence_action_ids=tuple(dict.fromkeys(value for value in chain.action_ids if value)),
            evidence_event_ids=tuple(dict.fromkeys(value for value in chain.event_ids if value)),
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
