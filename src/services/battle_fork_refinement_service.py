# 将已人工确认的弧盘精炼规则投影为固定轴 Buff 区间。
"""Confirmed fork-refinement rules kept separate from generic Buff imports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_damage_stack_buff_service import (
    demon_blade_damage_stack_rules,
    infer_damage_stack_intervals,
)
from src.services.battle_fork_door_refinement_service import (
    BattleForkDoorRefinementService,
)
from src.services.battle_fork_state_refinement_service import (
    BattleForkStateRefinementService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


FORK_REFINEMENT_MODEL_VERSION = "battle-fork-refinement-v5"
WUSHOUTIEYU_Q_WINDOW_EVENT = "FORK_WUSHOUTIEYU_Q_BEGIN"
WUSHOUTIEYU_E_STACK_EVENT = "FORK_WUSHOUTIEYU_E_END_STACK"
GOLD_RECORD_QTE_STACK_EVENT = "FORK_GOLD_RECORD_QTE_ACTION_STACK"
DOOR_TREATMENT_SELF_EVENT = "FORK_DOOR_TREATMENT_SELF"
DOOR_TREATMENT_OTHERS_EVENT = "FORK_DOOR_TREATMENT_OTHERS"

_WUSHOUTIEYU_MARKER = "upgradestar_pack_fork_wushoutieyu"
_ARACHNE_MARKER = "upgradestar_pack_fork_arachne"
_DEMON_BLADE_MARKER = "upgradestar_pack_fork_demonblade"
_DOOR_MARKER = "upgradestar_pack_fork_door"
_GOLD_RECORD_MARKER = "upgradestar_pack_fork_goldrecord"
_AUDITED_MARKERS = frozenset({
    _WUSHOUTIEYU_MARKER,
    _ARACHNE_MARKER,
    _DEMON_BLADE_MARKER,
    _DOOR_MARKER,
    _GOLD_RECORD_MARKER,
})


ForkTreatmentEvent = BattleTreatmentEvent


@dataclass(slots=True)
class _ActiveChain:
    start_us: int
    start_active_us: int
    end_active_us: int
    action_ids: list[str]
    event_ids: list[str]


def _parameter(definition: Mapping[str, Any] | None, name_id: str) -> float | None:
    parameters = (definition or {}).get("parameters") or ()
    if isinstance(parameters, Mapping):
        value = parameters.get(name_id)
        return float(value) if isinstance(value, (int, float)) else None
    if not isinstance(parameters, Sequence) or isinstance(parameters, str):
        return None
    for row in parameters:
        if not isinstance(row, Mapping) or row.get("name_id") != name_id:
            continue
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _modifier(
    property_id: str,
    value: float,
    *,
    source_tag: str = "",
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        source_require_tags=((source_tag,) if source_tag else ()),
    )


def _make_rule(
    selected: Any,
    rule_factory: type[Any],
    *,
    suffix: str,
    target_path: str,
    target_name: str,
    target_scope: str,
    event_type: str,
    duration_seconds: float | None,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    stack_limit_count: int = 1,
) -> Any:
    effect_id = str(selected.effect_definition_id)
    return rule_factory(
        rule_id=f"{effect_id}:confirmed-fork:{suffix}",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_refinement",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=target_path,
        target_name=target_name,
        target_scope=target_scope,
        event_type=event_type,
        effect_type="ADD",
        duration_policy=("HasDuration" if duration_seconds else "Equipped"),
        duration_seconds=duration_seconds,
        stack_count=1,
        modifiers=modifiers,
        stacking_type="AggregateBySource",
        stack_limit_count=stack_limit_count,
    )


def _static_rule(
    selected: Any,
    rule_factory: type[Any],
    *,
    suffix: str,
    target_name: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
) -> Any:
    return _make_rule(
        selected,
        rule_factory,
        suffix=suffix,
        target_path=f"confirmed-fork:{suffix}",
        target_name=target_name,
        target_scope="self",
        event_type="STATIC_EQUIPPED_SOURCE",
        duration_seconds=None,
        modifiers=modifiers,
    )


def _rules_wushoutieyu(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    q_up = _parameter(selected.definition, "buff_wushoutieyu_Up")
    duration = _parameter(selected.definition, "buff_wushoutieyu_CD")
    e_stack = _parameter(selected.definition, "buff_wushoutieyu_Up2")
    lakshana = _parameter(selected.definition, "buff_wushoutieyu_UpLakshana")
    if None in {q_up, duration, e_stack, lakshana}:
        return ()
    return (
        _static_rule(
            selected,
            rule_factory,
            suffix="wushoutieyu-lakshana",
            target_name="焰魂狂飙：相属性异能增伤",
            modifiers=(_modifier("DamageUpLakshanaBase", lakshana),),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="wushoutieyu-q-window",
            target_path="confirmed-fork:wushoutieyu-q-window",
            target_name="焰魂狂飙：Q 后 E/Q 增伤",
            target_scope="self",
            event_type=WUSHOUTIEYU_Q_WINDOW_EVENT,
            duration_seconds=duration,
            modifiers=(
                _modifier("DamageUpGeneralBase", q_up, source_tag="State.Damage.Skill"),
                _modifier(
                    "DamageUpGeneralBase",
                    q_up,
                    source_tag="State.Damage.UltraSkill",
                ),
            ),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="wushoutieyu-e-stack",
            target_path="confirmed-fork:wushoutieyu-e-stack",
            target_name="焰魂狂飙：E 增伤层数",
            target_scope="self",
            event_type=WUSHOUTIEYU_E_STACK_EVENT,
            duration_seconds=duration,
            modifiers=(
                _modifier(
                    "DamageUpGeneralBase",
                    e_stack,
                    source_tag="State.Damage.Skill",
                ),
            ),
            stack_limit_count=2,
        ),
    )


def _rules_arachne(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    hp_up = _parameter(selected.definition, "buff_Arachne_Hp")
    damage_up = _parameter(selected.definition, "buff_Arachne_Up")
    duration = _parameter(selected.definition, "buff_Arachne_CD")
    if None in {hp_up, damage_up, duration}:
        return ()
    return (
        _static_rule(
            selected,
            rule_factory,
            suffix="arachne-hp",
            target_name="永恒圆舞曲：生命值",
            modifiers=(_modifier("HPMaxUp", hp_up),),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="arachne-q-window",
            target_path="confirmed-fork:arachne-q-window",
            target_name="永恒圆舞曲：心灵伤害",
            target_scope="self",
            event_type="EBuffEventType::BUFF_EVENT_Q_SKILL_BEGIN",
            duration_seconds=duration,
            modifiers=(_modifier("DamageUpPsychicallyBase", damage_up),),
        ),
    )


def _rules_demon_blade(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    crit = _parameter(selected.definition, "buff_DemonBlade_Crit")
    dynamic = demon_blade_damage_stack_rules(selected, rule_factory)
    if crit is None or not dynamic:
        return ()
    return (
        _static_rule(
            selected,
            rule_factory,
            suffix="demon-blade-crit",
            target_name="噬心诡刃：暴击率",
            modifiers=(_modifier("CritBase", crit),),
        ),
        *dynamic,
    )


def _rules_door(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Door_Atk")
    other_up = _parameter(selected.definition, "buff_Door_OtherUp")
    self_up = _parameter(selected.definition, "buff_Door_Up")
    duration = _parameter(selected.definition, "buff_Door_CD")
    if None in {attack, other_up, self_up, duration}:
        return ()
    return (
        _static_rule(
            selected,
            rule_factory,
            suffix="door-attack",
            target_name="错误的门：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="door-treatment-self",
            target_path="confirmed-fork:door-treatment-self",
            target_name="错误的门：自身灵属性异能增伤",
            target_scope="self",
            event_type=DOOR_TREATMENT_SELF_EVENT,
            duration_seconds=duration,
            modifiers=(_modifier("DamageUpNatureBase", self_up),),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="door-treatment-others",
            target_path="confirmed-fork:door-treatment-others",
            target_name="错误的门：其他队员通用增伤",
            target_scope="team_others",
            event_type=DOOR_TREATMENT_OTHERS_EVENT,
            duration_seconds=duration,
            modifiers=(_modifier("DamageUpGeneralBase", other_up),),
        ),
    )


def _rules_gold_record(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_GoldRecord_AtkUp")
    qte_crit = _parameter(selected.definition, "buff_GoldRecord_QteCritDmaUp")
    q_crit = _parameter(selected.definition, "buff_GoldRecord_QCritDmaUp")
    duration = _parameter(selected.definition, "buff_GoldRecord_CD")
    if None in {attack, qte_crit, q_crit, duration}:
        return ()
    return (
        _static_rule(
            selected,
            rule_factory,
            suffix="gold-record-static",
            target_name="远行者之声：攻击力与 QTE 暴击伤害",
            modifiers=(
                _modifier("AtkUp", attack),
                _modifier(
                    "CritDamageBase",
                    qte_crit,
                    source_tag="State.Damage.QTE",
                ),
            ),
        ),
        _make_rule(
            selected,
            rule_factory,
            suffix="gold-record-q-stack",
            target_path="confirmed-fork:gold-record-q-stack",
            target_name="远行者之声：Q 暴击伤害层数",
            target_scope="self",
            event_type=GOLD_RECORD_QTE_STACK_EVENT,
            duration_seconds=duration,
            modifiers=(
                _modifier(
                    "CritDamageBase",
                    q_crit,
                    source_tag="State.Damage.UltraSkill",
                ),
            ),
            stack_limit_count=3,
        ),
    )


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


def _interval(
    rule: Any,
    *,
    suffix: str,
    start_us: int,
    end_us: int,
    stacks: int,
    basis: str,
    action_ids: Sequence[str] = (),
    event_ids: Sequence[str] = (),
) -> BattleInferredBuffInterval | None:
    if end_us <= start_us or stacks <= 0:
        return None
    return BattleInferredBuffInterval(
        interval_id=f"buff:fork:{suffix}:{rule.rule_id}",
        buff_asset_path=rule.target_asset_path,
        buff_name=rule.target_name,
        source_effect_definition_id=rule.source_effect_definition_id,
        source_kind=rule.source_kind,
        source_character_id=rule.source_character_id,
        source_character_name=rule.source_character_name,
        target_scope=rule.target_scope,
        start_us=start_us,
        end_us=end_us,
        stacks=stacks,
        duration_policy=rule.duration_policy,
        state_confidence="中",
        value_confidence="高",
        inference_basis=basis,
        trigger_event_type=rule.event_type,
        evidence_action_ids=tuple(action_ids),
        evidence_event_ids=tuple(event_ids),
        modifiers=rule.modifiers,
        stacking_type=rule.stacking_type,
        stack_limit_count=rule.stack_limit_count,
    )


class BattleForkRefinementService:
    """Build and replay only manually audited fork-refinement semantics."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkStateRefinementService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        if _WUSHOUTIEYU_MARKER in effect_id:
            return _rules_wushoutieyu(selected, rule_factory)
        if _ARACHNE_MARKER in effect_id:
            return _rules_arachne(selected, rule_factory)
        if _DEMON_BLADE_MARKER in effect_id:
            return _rules_demon_blade(selected, rule_factory)
        if _DOOR_MARKER in effect_id:
            return _rules_door(selected, rule_factory)
        if _GOLD_RECORD_MARKER in effect_id:
            return _rules_gold_record(selected, rule_factory)
        return BattleForkStateRefinementService.rules_for_selected_effect(
            selected,
            rule_factory,
        )

    @classmethod
    def infer_specialized(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        treatment_events: Sequence[ForkTreatmentEvent] = (),
        critical_events: Sequence[Any] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = list(infer_damage_stack_intervals(
            rules,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(cls._infer_wushoutieyu(
            rules,
            actions=actions,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(cls._infer_gold_record(
            rules,
            actions=actions,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkDoorRefinementService.infer(
            rules,
            actions=actions,
            treatment_events=treatment_events,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkStateRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            critical_events=critical_events,
        ))
        return tuple(sorted(
            results,
            key=lambda row: (
                row.start_us,
                row.end_us,
                row.source_character_id,
                row.buff_asset_path,
            ),
        ))

    @classmethod
    def _infer_wushoutieyu(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results: list[BattleInferredBuffInterval] = []
        q_rules = [
            row for row in rules if row.event_type == WUSHOUTIEYU_Q_WINDOW_EVENT
        ]
        for q_rule in q_rules:
            e_rule = next((
                row for row in rules
                if row.source_character_id == q_rule.source_character_id
                and row.source_effect_definition_id
                == q_rule.source_effect_definition_id
                and row.event_type == WUSHOUTIEYU_E_STACK_EVENT
            ), None)
            if e_rule is None or q_rule.duration_seconds is None:
                continue
            role_actions = tuple(
                row for row in actions
                if row.character_id == q_rule.source_character_id
            )
            chains: list[_ActiveChain] = []
            duration_us = round(q_rule.duration_seconds * 1_000_000)
            for action in sorted(
                (row for row in role_actions if row.input_kind == "Q"),
                key=lambda row: (row.start_us, row.action_id),
            ):
                now_active = _active_time(action.start_us, time_stop_intervals)
                proposed_end = now_active + duration_us
                if chains and now_active < chains[-1].end_active_us:
                    chains[-1].action_ids.append(action.action_id)
                    chains[-1].event_ids.extend(action.evidence_event_ids)
                else:
                    chains.append(_ActiveChain(
                        start_us=action.start_us,
                        start_active_us=now_active,
                        end_active_us=proposed_end,
                        action_ids=[action.action_id],
                        event_ids=list(action.evidence_event_ids),
                    ))
            e_actions = tuple(sorted(
                (row for row in role_actions if row.input_kind == "E"),
                key=lambda row: (row.end_us, row.action_id),
            ))
            for chain_index, chain in enumerate(chains):
                chain_end = min(
                    battle_end_us,
                    _raw_expiry(
                        chain.end_active_us,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                    ),
                )
                main = _interval(
                    q_rule,
                    suffix=f"wushoutieyu-main:{chain_index}",
                    start_us=chain.start_us,
                    end_us=chain_end,
                    stacks=1,
                    basis=(
                        "Q 开始即建立 E/Q 增伤，触发它的本次 Q 也享受；"
                        "持续时间按扣时停时钟推进，窗口内再次 Q 不刷新。"
                    ),
                    action_ids=chain.action_ids,
                    event_ids=chain.event_ids,
                )
                if main is not None:
                    results.append(main)
                stack = 0
                segment_start: int | None = None
                evidence_action_ids: tuple[str, ...] = ()
                evidence_event_ids: tuple[str, ...] = ()
                for action in e_actions:
                    end_active = _active_time(action.end_us, time_stop_intervals)
                    if not chain.start_active_us <= end_active < chain.end_active_us:
                        continue
                    next_start = min(chain_end, action.end_us + 1)
                    previous = _interval(
                        e_rule,
                        suffix=(
                            f"wushoutieyu-stack:{chain_index}:"
                            f"{next_start}"
                        ),
                        start_us=(
                            segment_start
                            if segment_start is not None
                            else next_start
                        ),
                        end_us=next_start,
                        stacks=stack,
                        basis=(
                            "E 在技能实际结束后增加一层；本次 E 只读取"
                            "释放前层数，最多两层。"
                        ),
                        action_ids=evidence_action_ids,
                        event_ids=evidence_event_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                    stack = min(e_rule.stack_limit_count, stack + 1)
                    segment_start = next_start
                    evidence_action_ids = (action.action_id,)
                    evidence_event_ids = action.evidence_event_ids
                final = _interval(
                    e_rule,
                    suffix=f"wushoutieyu-stack:{chain_index}:final",
                    start_us=(
                        segment_start
                        if segment_start is not None
                        else chain_end
                    ),
                    end_us=chain_end,
                    stacks=stack,
                    basis=(
                        "E 在技能实际结束后增加一层；本次 E 只读取"
                        "释放前层数，最多两层。"
                    ),
                    action_ids=evidence_action_ids,
                    event_ids=evidence_event_ids,
                )
                if final is not None:
                    results.append(final)
        return tuple(results)

    @classmethod
    def _infer_gold_record(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results: list[BattleInferredBuffInterval] = []
        stack_rules = [
            row for row in rules if row.event_type == GOLD_RECORD_QTE_STACK_EVENT
        ]
        for rule in stack_rules:
            if rule.duration_seconds is None:
                continue
            role_actions = sorted(
                (
                    row for row in actions
                    if row.character_id == rule.source_character_id
                    and row.input_kind == "QTE"
                ),
                key=lambda row: (row.start_us, row.action_id),
            )
            duration_us = round(rule.duration_seconds * 1_000_000)
            stack = 0
            start_us: int | None = None
            expires_active_us: int | None = None
            action_ids: tuple[str, ...] = ()
            event_ids: tuple[str, ...] = ()
            segment = 0
            for action in role_actions:
                trigger_us = action.start_us
                now_active = _active_time(trigger_us, time_stop_intervals)
                if expires_active_us is not None and now_active >= expires_active_us:
                    expiry = _raw_expiry(
                        expires_active_us,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                    )
                    interval = _interval(
                        rule,
                        suffix=f"gold-record:{segment}",
                        start_us=(
                            start_us if start_us is not None else expiry
                        ),
                        end_us=min(battle_end_us, expiry),
                        stacks=stack,
                        basis=(
                            "装备者每次独立 QTE 开始事件只增加一层 Q 暴击伤害；"
                            "QTE 的逐击数量不复制叠层。"
                        ),
                        action_ids=action_ids,
                        event_ids=event_ids,
                    )
                    if interval is not None:
                        results.append(interval)
                    stack = 0
                    segment += 1
                elif start_us is not None:
                    interval = _interval(
                        rule,
                        suffix=f"gold-record:{segment}",
                        start_us=start_us,
                        end_us=trigger_us,
                        stacks=stack,
                        basis=(
                            "装备者每次独立 QTE 开始事件只增加一层 Q 暴击伤害；"
                            "QTE 的逐击数量不复制叠层。"
                        ),
                        action_ids=action_ids,
                        event_ids=event_ids,
                    )
                    if interval is not None:
                        results.append(interval)
                    segment += 1
                stack = min(rule.stack_limit_count, stack + 1)
                start_us = trigger_us
                expires_active_us = now_active + duration_us
                action_ids = (action.action_id,)
                event_ids = action.evidence_event_ids
            if start_us is not None and expires_active_us is not None:
                expiry = _raw_expiry(
                    expires_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                interval = _interval(
                    rule,
                    suffix=f"gold-record:{segment}:final",
                    start_us=start_us,
                    end_us=min(battle_end_us, expiry),
                    stacks=stack,
                    basis=(
                        "装备者每次独立 QTE 开始事件只增加一层 Q 暴击伤害；"
                        "QTE 的逐击数量不复制叠层；从该次 QTE 开始时生效。"
                    ),
                    action_ids=action_ids,
                    event_ids=event_ids,
                )
                if interval is not None:
                    results.append(interval)
        return tuple(results)
