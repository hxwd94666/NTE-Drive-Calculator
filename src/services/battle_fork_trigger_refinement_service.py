# 将第三批人工确认弧盘的技能触发与暴击叠层投影为固定轴规则。
"""Trigger-timed fork refinements that generic exported events cannot express."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_fork_periodic_refinement_service import (
    BattleForkPeriodicRefinementService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


FORK_TRIGGER_REFINEMENT_MODEL_VERSION = "battle-fork-trigger-refinement-v3"
BUTTERFLY_Q_EVENT = "FORK_BUTTERFLY_TRIGGER"
CASTLE_E_EVENT = "FORK_CASTLE_TRIGGER"
CROWBAR_E_EVENT = "FORK_CROWBAR_TRIGGER"
GOLD_WOOL_ACTION_EVENT = "FORK_GOLD_WOOL_TRIGGER"
KITE_E_EVENT = "FORK_KITE_TRIGGER"
KNIGHT_CANDY_CRIT_EVENT = "FORK_KNIGHT_CANDY_TRIGGER"
CASTLE_RUNTIME_DURATION_SECONDS = 50.0

_BUTTERFLY_MARKER = "upgradestar_pack_fork_butterfly"
_CASTLE_MARKER = "upgradestar_pack_fork_castle"
_CROWBAR_MARKER = "upgradestar_pack_fork_crowbar"
_GOLD_WOOL_MARKER = "upgradestar_pack_fork_goldwool"
_KITE_MARKER = "upgradestar_pack_fork_kite"
_KNIGHT_CANDY_MARKER = "upgradestar_pack_fork_knightcandy"
_AUDITED_MARKERS = frozenset({
    _BUTTERFLY_MARKER,
    _CASTLE_MARKER,
    _CROWBAR_MARKER,
    _GOLD_WOOL_MARKER,
    _KITE_MARKER,
    _KNIGHT_CANDY_MARKER,
})


@dataclass(frozen=True, slots=True)
class ForkCriticalEvent:
    """Critical-hit evidence supplied explicitly or by first-pass hit replay."""

    event_id: str
    relative_time_us: int
    source_character_id: int
    evidence_kind: str = "explicit"


@dataclass(slots=True)
class _RefreshChain:
    start_us: int
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
    source_require_tags: Sequence[str] = (),
    target_require_tags: Sequence[str] = (),
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        source_require_tags=tuple(source_require_tags),
        target_require_tags=tuple(target_require_tags),
    )


def _rule(
    selected: Any,
    rule_factory: type[Any],
    *,
    suffix: str,
    name: str,
    scope: str,
    event_type: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    duration_seconds: float | None = None,
    stack_limit_count: int = 1,
    cooldown_seconds: float | None = None,
    refresh_whole_stack: bool = False,
) -> Any:
    effect_id = str(selected.effect_definition_id)
    stacking = "AggregateBySource"
    if refresh_whole_stack:
        stacking += "|RefreshWholeStack"
    return rule_factory(
        rule_id=f"{effect_id}:confirmed-fork:{suffix}",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_refinement",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=f"confirmed-fork:{suffix}",
        target_name=name,
        target_scope=scope,
        event_type=event_type,
        effect_type="ADD",
        duration_policy=("HasDuration" if duration_seconds else "StateBound"),
        duration_seconds=duration_seconds,
        stack_count=1,
        modifiers=modifiers,
        stacking_type=stacking,
        stack_limit_count=stack_limit_count,
        cooldown_seconds=cooldown_seconds,
    )


def _rules_butterfly(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    nature = _parameter(selected.definition, "buff_Butterfly_DamageUpNatureBase")
    attachment = _parameter(selected.definition, "buff_Butterfly_attachedup")
    enhanced = _parameter(selected.definition, "buff_Butterfly_attachedup2")
    duration = _parameter(selected.definition, "buff_Butterfly_CD")
    if None in {nature, attachment, enhanced, duration}:
        return ()
    attachment_tag = ("State.Damage.Attachment",)
    return (
        _rule(
            selected,
            factory,
            suffix="butterfly-nature",
            name="现实避难所：灵属性伤害",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier("DamageUpNatureBase", nature),),
        ),
        _rule(
            selected,
            factory,
            suffix="butterfly-attachment-base",
            name="现实避难所：附着物基础增伤",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                attachment,
                source_require_tags=attachment_tag,
            ),),
        ),
        _rule(
            selected,
            factory,
            suffix="butterfly-attachment-q-delta",
            name="现实避难所：Q 期间附着物替换档",
            scope="self",
            event_type=BUTTERFLY_Q_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                enhanced - attachment,
                source_require_tags=attachment_tag,
            ),),
        ),
    )


def _rules_castle(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    heal_up = _parameter(selected.definition, "buff_Castle_HealUp")
    declared_duration = _parameter(selected.definition, "buff_Castle_CD")
    if None in {heal_up, declared_duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="castle-heal",
        name=(
            "扭曲之城的呼唤：E 后治疗效率"
            f"（说明参数 {declared_duration:g} 秒；当前运行时 50 秒）"
        ),
        scope="self",
        event_type=CASTLE_E_EVENT,
        duration_seconds=CASTLE_RUNTIME_DURATION_SECONDS,
        refresh_whole_stack=True,
        modifiers=(_modifier("HealUp", heal_up),),
    ),)


def _rules_crowbar(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    unbalance = _parameter(selected.definition, "buff_Crowbar_Unbal")
    duration = _parameter(selected.definition, "buff_Crowbar_CD")
    if None in {unbalance, duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="crowbar-unbalance",
        name="时间大盗：E 后失衡强度",
        scope="self",
        event_type=CROWBAR_E_EVENT,
        duration_seconds=duration,
        refresh_whole_stack=True,
        modifiers=(_modifier("UnbalIntensityAdd", unbalance),),
    ),)


def _rules_gold_wool(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    lakshana = _parameter(selected.definition, "buff_GoldWool_Up")
    crit_damage = _parameter(selected.definition, "buff_GoldWool_CritDamageUp")
    duration = _parameter(selected.definition, "buff_GoldWool_CD")
    if None in {lakshana, crit_damage, duration}:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="gold-wool-lakshana",
            name="众人追寻之物：相属性伤害",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier("DamageUpLakshanaBase", lakshana),),
        ),
        _rule(
            selected,
            factory,
            suffix="gold-wool-crit-damage",
            name="众人追寻之物：E/Q 暴击伤害",
            scope="self",
            event_type=GOLD_WOOL_ACTION_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier("CritDamageBase", crit_damage),),
        ),
    )


def _rules_kite(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Kite_AtkUp")
    lakshana = _parameter(selected.definition, "buff_Kite_Up")
    duration = _parameter(selected.definition, "buff_Kite_CD")
    if None in {attack, lakshana, duration}:
        return ()
    return (
        _rule(
            selected,
            factory,
            suffix="kite-attack",
            name="当心头顶：E 后攻击",
            scope="self",
            event_type=KITE_E_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="kite-delay-and-stain",
            name="当心头顶：延滞且浸染目标相伤",
            scope="unknown",
            event_type=KITE_E_EVENT,
            duration_seconds=duration,
            refresh_whole_stack=True,
            modifiers=(_modifier(
                "DamageUpLakshanaBase",
                lakshana,
                target_require_tags=(
                    "confirmed-target-state:delay",
                    "confirmed-target-state:stain",
                ),
            ),),
        ),
    )


def _rules_knight_candy(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    crit_damage = _parameter(
        selected.definition,
        "buff_KnightCandy_CritDamageUp",
    )
    duration = _parameter(selected.definition, "buff_KnightCandy_CD")
    if None in {crit_damage, duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="knight-candy-crit-stack",
        name="凶猛之绵：暴击后暴伤层数",
        scope="self",
        event_type=KNIGHT_CANDY_CRIT_EVENT,
        duration_seconds=duration,
        stack_limit_count=10,
        cooldown_seconds=0.3,
        refresh_whole_stack=True,
        modifiers=(_modifier("CritDamageBase", crit_damage),),
    ),)


def _active_time(
    raw_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_expiry(
    active_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        active_us,
        battle_start_us=0,
        battle_end_us=battle_end_us,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
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
        interval_id=f"buff:fork-trigger:{suffix}:{rule.rule_id}",
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


def _refresh_intervals(
    rule: Any,
    occurrences: Sequence[tuple[int, BattleInferredAction]],
    *,
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    basis: str,
) -> tuple[BattleInferredBuffInterval, ...]:
    if rule.duration_seconds is None:
        return ()
    duration_us = round(rule.duration_seconds * 1_000_000)
    chains: list[_RefreshChain] = []
    for start_us, action in sorted(
        occurrences,
        key=lambda row: (row[0], row[1].action_id),
    ):
        now_active = _active_time(start_us, time_stop_intervals)
        proposed_end = now_active + duration_us
        if chains and now_active < chains[-1].end_active_us:
            chains[-1].end_active_us = proposed_end
            chains[-1].action_ids.append(action.action_id)
            chains[-1].event_ids.extend(action.evidence_event_ids)
        else:
            chains.append(_RefreshChain(
                start_us=start_us,
                end_active_us=proposed_end,
                action_ids=[action.action_id],
                event_ids=list(action.evidence_event_ids),
            ))
    result = []
    for ordinal, chain in enumerate(chains):
        expiry = _raw_expiry(
            chain.end_active_us,
            battle_end_us=battle_end_us,
            intervals=time_stop_intervals,
        )
        interval = _interval(
            rule,
            suffix=f"refresh:{ordinal}",
            start_us=chain.start_us,
            end_us=min(battle_end_us, expiry),
            stacks=1,
            basis=basis,
            action_ids=chain.action_ids,
            event_ids=chain.event_ids,
        )
        if interval is not None:
            result.append(interval)
    return tuple(result)


class BattleForkTriggerRefinementService:
    """Build and replay the manually confirmed trigger-based fork batch."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkPeriodicRefinementService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        builders = (
            (_BUTTERFLY_MARKER, _rules_butterfly),
            (_CASTLE_MARKER, _rules_castle),
            (_CROWBAR_MARKER, _rules_crowbar),
            (_GOLD_WOOL_MARKER, _rules_gold_wool),
            (_KITE_MARKER, _rules_kite),
            (_KNIGHT_CANDY_MARKER, _rules_knight_candy),
        )
        for marker, builder in builders:
            if marker in effect_id:
                return builder(selected, rule_factory)
        return BattleForkPeriodicRefinementService.rules_for_selected_effect(
            selected,
            rule_factory,
        )

    @classmethod
    def infer_specialized(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit] = (),
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        critical_events: Sequence[ForkCriticalEvent] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results: list[BattleInferredBuffInterval] = []
        for rule in rules:
            role_actions = tuple(
                action for action in actions
                if action.character_id == rule.source_character_id
            )
            occurrences: tuple[tuple[int, BattleInferredAction], ...] = ()
            basis = ""
            if rule.event_type == BUTTERFLY_Q_EVENT:
                occurrences = tuple(
                    (action.start_us, action)
                    for action in role_actions if action.input_kind == "Q"
                )
                basis = (
                    "Q 开始立即把附着物增伤替换到强化档，触发 Q 期间的附着物"
                    "伤害享受；重复 Q 只刷新六秒窗口。"
                )
            elif rule.event_type in {CASTLE_E_EVENT, CROWBAR_E_EVENT, KITE_E_EVENT}:
                occurrences = tuple(
                    (action.end_us + 1, action)
                    for action in role_actions if action.input_kind == "E"
                )
                basis = (
                    "E 技能实际结束后一微秒生效，触发它的本次 E 不享受；"
                    "同名效果不可叠加，重复 E 只刷新持续时间。"
                )
            elif rule.event_type == GOLD_WOOL_ACTION_EVENT:
                occurrences = tuple(
                    (
                        action.start_us
                        if action.input_kind == "Q"
                        else action.end_us + 1,
                        action,
                    )
                    for action in role_actions
                    if action.input_kind in {"E", "Q"}
                )
                basis = (
                    "Q 开始立即生效且本次 Q 享受；E 实际结束后一微秒生效，"
                    "本次 E 不享受；任一触发都只刷新同一二十秒窗口。"
                )
            if occurrences:
                results.extend(_refresh_intervals(
                    rule,
                    occurrences,
                    battle_end_us=battle_end_us,
                    time_stop_intervals=time_stop_intervals,
                    basis=basis,
                ))
        results.extend(cls._infer_knight_candy(
            rules,
            critical_events=critical_events,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkPeriodicRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        return tuple(sorted(results, key=lambda row: (
            row.start_us,
            row.end_us,
            row.source_character_id,
            row.buff_asset_path,
        )))

    @classmethod
    def _infer_knight_candy(
        cls,
        rules: Sequence[Any],
        *,
        critical_events: Sequence[ForkCriticalEvent],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = []
        for rule in (
            row for row in rules
            if row.event_type == KNIGHT_CANDY_CRIT_EVENT
            and row.duration_seconds is not None
        ):
            cooldown_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
            duration_us = round(rule.duration_seconds * 1_000_000)
            events = []
            last_active_us: int | None = None
            for event in sorted(
                (
                    row for row in critical_events
                    if row.source_character_id == rule.source_character_id
                ),
                key=lambda row: (row.relative_time_us, row.event_id),
            ):
                now_active = _active_time(event.relative_time_us, time_stop_intervals)
                if last_active_us is not None and now_active - last_active_us < cooldown_us:
                    continue
                events.append((event, now_active))
                last_active_us = now_active
            stack = 0
            segment_start: int | None = None
            expiry_active_us: int | None = None
            evidence_ids: list[str] = []
            segment = 0
            for event, now_active in events:
                if expiry_active_us is not None and now_active >= expiry_active_us:
                    expiry = _raw_expiry(
                        expiry_active_us,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                    )
                    previous = _interval(
                        rule,
                        suffix=f"knight:{segment}",
                        start_us=segment_start if segment_start is not None else expiry,
                        end_us=min(battle_end_us, expiry),
                        stacks=stack,
                        basis=(
                            "消费逐击公式重放判定的暴击证据；新层在触发击后一微秒生效，"
                            "来源侧 0.3 秒冷却，最多十层且刷新整组十秒持续时间。"
                        ),
                        event_ids=evidence_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                    stack = 0
                    segment += 1
                    evidence_ids = []
                next_start = min(battle_end_us, event.relative_time_us + 1)
                if segment_start is not None and stack > 0:
                    previous = _interval(
                        rule,
                        suffix=f"knight:{segment}",
                        start_us=segment_start,
                        end_us=next_start,
                        stacks=stack,
                        basis=(
                            "消费逐击公式重放判定的暴击证据；新层在触发击后一微秒生效，"
                            "来源侧 0.3 秒冷却，最多十层且刷新整组十秒持续时间。"
                        ),
                        event_ids=evidence_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                    segment += 1
                stack = min(rule.stack_limit_count, stack + 1)
                segment_start = next_start
                expiry_active_us = now_active + duration_us
                evidence_ids.append(event.event_id)
            if segment_start is not None and expiry_active_us is not None:
                expiry = _raw_expiry(
                    expiry_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                final = _interval(
                    rule,
                    suffix=f"knight:{segment}:final",
                    start_us=segment_start,
                    end_us=min(battle_end_us, expiry),
                    stacks=stack,
                    basis=(
                        "消费逐击公式重放判定的暴击证据；新层在触发击后一微秒生效，"
                        "来源侧 0.3 秒冷却，最多十层且刷新整组十秒持续时间。"
                    ),
                    event_ids=evidence_ids,
                )
                if final is not None:
                    results.append(final)
        return tuple(results)
