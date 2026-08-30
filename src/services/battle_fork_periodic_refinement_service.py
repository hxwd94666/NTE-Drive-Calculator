# 将第四批人工确认弧盘的周期、随机与召唤语义投影为固定轴规则。
"""Periodic and derived-hit fork refinements kept outside shared inference."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)
from src.services.battle_fork_damage_completion_service import (
    BattleForkDamageCompletionService,
)


FORK_PERIODIC_REFINEMENT_MODEL_VERSION = "battle-fork-periodic-refinement-v1"
LUNAR_PHASE_Q_EVENT = "FORK_LUNAR_PHASE_Q_BEGIN"
MOTOR_CANDY_PERIODIC_EVENT = "FORK_MOTOR_CANDY_FOREGROUND_PERIODIC"
NEST_BIRD_Q_HIT_EVENT = "FORK_NEST_BIRD_Q_HIT_MARK"

_LUNAR_PHASE_MARKER = "upgradestar_pack_fork_lunarphase"
_MOTOR_CANDY_MARKER = "upgradestar_pack_fork_motorcandy"
_NAKUPEDA_MARKER = "upgradestar_pack_fork_nakupeda"
_NEST_BIRD_MARKER = "upgradestar_pack_fork_nestbird"
_PAPER_PLANE_MARKER = "upgradestar_pack_fork_paperplane"
_POLICE_RAT_MARKER = "upgradestar_pack_fork_policerat"
_AUDITED_MARKERS = frozenset({
    _LUNAR_PHASE_MARKER,
    _MOTOR_CANDY_MARKER,
    _NAKUPEDA_MARKER,
    _NEST_BIRD_MARKER,
    _PAPER_PLANE_MARKER,
    _POLICE_RAT_MARKER,
})


@dataclass(frozen=True, slots=True)
class NakupedaRandomSemantics:
    """Confirmed one-of-three Q outcome; the runtime choice is not fabricated."""

    cooldown_seconds: float
    outcome_probability: float
    lowest_hp_heal_max_hp_ratio: float
    owner_max_hp_shield_ratio: float
    shield_duration_seconds: float
    team_heal_own_max_hp_ratio: float
    applies_exactly_one_outcome: bool
    lowest_hp_tie_resolution: str


@dataclass(frozen=True, slots=True)
class NestBirdMarkSemantics:
    """Target-local Q-hit mark semantics for future incoming-damage replay."""

    outgoing_damage_additive: float
    duration_seconds: float
    applies_after_triggering_hit: bool
    refreshes_same_target: bool
    tracks_targets_independently: bool
    requires_observed_q_hit_target: bool


@dataclass(frozen=True, slots=True)
class PoliceRatDerivedHitSemantics:
    """Low-confidence assumptions explicitly approved pending controlled capture."""

    summon_duration_seconds: float
    cooldown_seconds: float
    attack_coefficient: float
    boss_damage_bonus: float
    derived_hit_can_crit: bool
    inherits_owner_damage_bonuses: bool
    uses_observed_axis_hits_only: bool
    confidence: str


@dataclass(frozen=True, slots=True)
class _ActiveSegment:
    start_us: int
    end_us: int
    active_character_id: int
    evidence_action_ids: tuple[str, ...]


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
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        source_require_tags=tuple(source_require_tags),
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
    stacking_type: str = "AggregateBySource",
) -> Any:
    effect_id = str(selected.effect_definition_id)
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
        stacking_type=stacking_type,
        stack_limit_count=stack_limit_count,
        cooldown_seconds=cooldown_seconds,
    )


def _static_rule(
    selected: Any,
    rule_factory: type[Any],
    *,
    suffix: str,
    name: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    scope: str = "self",
) -> Any:
    return _rule(
        selected,
        rule_factory,
        suffix=suffix,
        name=name,
        scope=scope,
        event_type="STATIC_EQUIPPED_SOURCE",
        modifiers=modifiers,
    )


def _rules_lunar_phase(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_LunarPhase_Atk")
    cosmos = _parameter(selected.definition, "buff_LunarPhase_Up")
    defence_ignore = _parameter(selected.definition, "buff_LunarPhase_DefIgnore")
    duration = _parameter(selected.definition, "buff_LunarPhase_CD")
    if None in {attack, cosmos, defence_ignore, duration}:
        return ()
    return (
        _static_rule(
            selected,
            factory,
            suffix="lunar-phase-attack",
            name="穿过胭红蜃景：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="lunar-phase-q-window",
            name="穿过胭红蜃景：Q 开始光属性增伤与无视防御",
            scope="self",
            event_type=LUNAR_PHASE_Q_EVENT,
            duration_seconds=duration,
            modifiers=(
                _modifier("DamageUpCosmosBase", cosmos),
                _modifier("DefIgnore", defence_ignore),
            ),
            stacking_type="AggregateByTarget|RefreshWholeStack",
        ),
    )


def _rules_motor_candy(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    period = _parameter(selected.definition, "buff_MotorCandy_CD")
    attack = _parameter(selected.definition, "buff_MotorCandy_AtkUp")
    if None in {period, attack}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="motor-candy-periodic-attack",
        name="极速之绵：前台每秒攻击层数",
        scope="self",
        event_type=MOTOR_CANDY_PERIODIC_EVENT,
        modifiers=(_modifier("AtkUp", attack),),
        stack_limit_count=5,
        cooldown_seconds=period,
    ),)


def _rules_nakupeda(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    hp = _parameter(selected.definition, "buff_Nakipeda_Hp")
    if hp is None:
        return ()
    return (_static_rule(
        selected,
        factory,
        suffix="nakupeda-hp",
        name="千金难买你开心：生命值",
        modifiers=(_modifier("HPMaxUp", hp),),
    ),)


def _rules_nest_bird(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    return (_rule(
        selected,
        factory,
        suffix="nest-bird-target-mark",
        name="面具下的泪：Q 命中目标造成伤害降低标记",
        scope="unknown",
        event_type=NEST_BIRD_Q_HIT_EVENT,
        duration_seconds=20.0,
        modifiers=(_modifier("DamageUpGeneralBase", -0.18),),
        stacking_type="AggregateByTarget|RefreshWholeStack",
    ),)


def _rules_paper_plane(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    nature = _parameter(selected.definition, "buff_PaperPlane_Up")
    if nature is None:
        return ()
    return (_static_rule(
        selected,
        factory,
        suffix="paper-plane-skill-nature",
        name="开始净空：E/Q 灵属性伤害",
        modifiers=(
            _modifier(
                "DamageUpNatureBase",
                nature,
                source_require_tags=("State.Damage.Skill",),
            ),
            _modifier(
                "DamageUpNatureBase",
                nature,
                source_require_tags=("State.Damage.UltraSkill",),
            ),
        ),
    ),)


def _rules_police_rat(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_PoliceRat_AtkUp")
    boss_bonus = _parameter(selected.definition, "buff_PoliceRat_Up")
    if None in {attack, boss_bonus}:
        return ()
    return (
        _static_rule(
            selected,
            factory,
            suffix="police-rat-attack",
            name="海特洛的安宁：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _static_rule(
            selected,
            factory,
            suffix="police-rat-boss",
            name="海特洛的安宁：Boss 目标伤害（缺少逐击 Boss 身份）",
            scope="unknown",
            modifiers=(_modifier("DamageUpGeneralBase", boss_bonus),),
        ),
    )


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
        interval_id=f"buff:fork-periodic:{suffix}:{rule.rule_id}",
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


def _active_segments(
    actions: Sequence[BattleInferredAction],
    battle_end_us: int,
) -> tuple[_ActiveSegment, ...]:
    foreground_actions = sorted(
        (
            row for row in actions
            if row.input_kind in {"A", "E", "Q", "QTE", "G"}
            and 0 <= row.start_us < battle_end_us
        ),
        key=lambda row: (row.start_us, row.action_id),
    )
    if not foreground_actions:
        return ()
    boundaries: list[tuple[int, int]] = [(0, foreground_actions[0].character_id)]
    for action in foreground_actions[1:]:
        if action.character_id != boundaries[-1][1]:
            boundaries.append((action.start_us, action.character_id))
    result = []
    for ordinal, (start_us, character_id) in enumerate(boundaries):
        end_us = (
            boundaries[ordinal + 1][0]
            if ordinal + 1 < len(boundaries)
            else battle_end_us
        )
        evidence = tuple(
            row.action_id for row in foreground_actions
            if start_us <= row.start_us < end_us
        )
        if start_us < end_us:
            result.append(_ActiveSegment(start_us, end_us, character_id, evidence))
    return tuple(result)


class BattleForkPeriodicRefinementService:
    """Build and replay manually confirmed periodic and derived fork semantics."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkDamageCompletionService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        builders = (
            (_LUNAR_PHASE_MARKER, _rules_lunar_phase),
            (_MOTOR_CANDY_MARKER, _rules_motor_candy),
            (_NAKUPEDA_MARKER, _rules_nakupeda),
            (_NEST_BIRD_MARKER, _rules_nest_bird),
            (_PAPER_PLANE_MARKER, _rules_paper_plane),
            (_POLICE_RAT_MARKER, _rules_police_rat),
        )
        for marker, builder in builders:
            if marker in effect_id:
                return builder(selected, rule_factory)
        return BattleForkDamageCompletionService.rules_for_selected_effect(
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
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = list(cls._infer_lunar_phase(
            rules,
            actions=actions,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(cls._infer_motor_candy(
            rules,
            actions=actions,
            battle_end_us=battle_end_us,
        ))
        results.extend(cls._infer_nest_bird(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkDamageCompletionService.infer_specialized(
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

    @staticmethod
    def _infer_lunar_phase(
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = []
        for rule in (
            row for row in rules
            if row.event_type == LUNAR_PHASE_Q_EVENT
            and row.duration_seconds is not None
        ):
            duration_us = round(rule.duration_seconds * 1_000_000)
            chains: list[_RefreshChain] = []
            for action in sorted(
                (
                    row for row in actions
                    if row.character_id == rule.source_character_id
                    and row.input_kind == "Q"
                ),
                key=lambda row: (row.start_us, row.action_id),
            ):
                now_active = _active_time(action.start_us, time_stop_intervals)
                expiry_active = now_active + duration_us
                if chains and now_active < chains[-1].end_active_us:
                    chains[-1].end_active_us = expiry_active
                    chains[-1].action_ids.append(action.action_id)
                    chains[-1].event_ids.extend(action.evidence_event_ids)
                else:
                    chains.append(_RefreshChain(
                        start_us=action.start_us,
                        end_active_us=expiry_active,
                        action_ids=[action.action_id],
                        event_ids=list(action.evidence_event_ids),
                    ))
            for ordinal, chain in enumerate(chains):
                expiry = _raw_expiry(
                    chain.end_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                interval = _interval(
                    rule,
                    suffix=f"lunar:{ordinal}",
                    start_us=chain.start_us,
                    end_us=min(battle_end_us, expiry),
                    stacks=1,
                    basis=(
                        "Q 开始立即获得光属性增伤与无视防御，本次 Q 享受；"
                        "效果不可叠加，重复 Q 刷新二十秒持续时间。"
                    ),
                    action_ids=chain.action_ids,
                    event_ids=chain.event_ids,
                )
                if interval is not None:
                    results.append(interval)
        return tuple(results)

    @staticmethod
    def _infer_motor_candy(
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = []
        segments = _active_segments(actions, battle_end_us)
        for rule in (
            row for row in rules
            if row.event_type == MOTOR_CANDY_PERIODIC_EVENT
            and row.cooldown_seconds is not None
        ):
            period_us = round(rule.cooldown_seconds * 1_000_000)
            if period_us <= 0:
                continue
            for segment_ordinal, segment in enumerate(segments):
                if segment.active_character_id != rule.source_character_id:
                    continue
                tick_us = segment.start_us + period_us
                stack = 0
                tick_ordinal = 0
                while tick_us < segment.end_us:
                    stack = min(rule.stack_limit_count, stack + 1)
                    next_tick_us = tick_us + period_us
                    interval = _interval(
                        rule,
                        suffix=f"motor:{segment_ordinal}:{tick_ordinal}",
                        start_us=tick_us,
                        end_us=min(segment.end_us, next_tick_us),
                        stacks=stack,
                        basis=(
                            "进入前台时从零层开始，完整经过一秒才获得首层；"
                            "按战报原始时间继续周期叠层，时停不暂停，最多五层；"
                            "离开前台立即清空，重新入场重新计时。"
                        ),
                        action_ids=segment.evidence_action_ids,
                    )
                    if interval is not None:
                        results.append(interval)
                    if stack >= rule.stack_limit_count:
                        if next_tick_us < segment.end_us:
                            results[-1] = _interval(
                                rule,
                                suffix=f"motor:{segment_ordinal}:capped",
                                start_us=tick_us,
                                end_us=segment.end_us,
                                stacks=stack,
                                basis=(
                                    "进入前台时从零层开始，完整经过一秒才获得首层；"
                                    "按战报原始时间继续周期叠层，时停不暂停，最多五层；"
                                    "离开前台立即清空，重新入场重新计时。"
                                ),
                                action_ids=segment.evidence_action_ids,
                            )
                        break
                    tick_us = next_tick_us
                    tick_ordinal += 1
        return tuple(row for row in results if row is not None)

    @staticmethod
    def _infer_nest_bird(
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        hits_by_event = {row.event_id: row for row in hits}
        results = []
        for rule in (
            row for row in rules
            if row.event_type == NEST_BIRD_Q_HIT_EVENT
            and row.duration_seconds is not None
        ):
            occurrences: dict[str, list[BattleAnalysisHit]] = {}
            action_ids_by_event: dict[str, str] = {}
            for action in actions:
                if (
                    action.character_id != rule.source_character_id
                    or action.input_kind != "Q"
                ):
                    continue
                for event_id in action.evidence_event_ids:
                    hit = hits_by_event.get(event_id)
                    if (
                        hit is None
                        or hit.direction != "outgoing"
                        or not hit.target_id
                    ):
                        continue
                    occurrences.setdefault(hit.target_id, []).append(hit)
                    action_ids_by_event[hit.event_id] = action.action_id
            duration_us = round(rule.duration_seconds * 1_000_000)
            for target_id, target_hits in sorted(occurrences.items()):
                chains: list[_RefreshChain] = []
                for hit in sorted(
                    target_hits,
                    key=lambda row: (row.relative_time_us, row.sequence),
                ):
                    start_us = min(battle_end_us, hit.relative_time_us + 1)
                    now_active = _active_time(start_us, time_stop_intervals)
                    expiry_active = now_active + duration_us
                    if chains and now_active < chains[-1].end_active_us:
                        chains[-1].end_active_us = expiry_active
                        chains[-1].action_ids.append(
                            action_ids_by_event[hit.event_id]
                        )
                        chains[-1].event_ids.append(hit.event_id)
                    else:
                        chains.append(_RefreshChain(
                            start_us=start_us,
                            end_active_us=expiry_active,
                            action_ids=[action_ids_by_event[hit.event_id]],
                            event_ids=[hit.event_id],
                        ))
                for ordinal, chain in enumerate(chains):
                    expiry = _raw_expiry(
                        chain.end_active_us,
                        battle_end_us=battle_end_us,
                        intervals=time_stop_intervals,
                    )
                    interval = _interval(
                        rule,
                        suffix=f"nest:{target_id}:{ordinal}",
                        start_us=chain.start_us,
                        end_us=min(battle_end_us, expiry),
                        stacks=1,
                        basis=(
                            "只消费 Q 动作绑定的真实逐击及其 target_id；标记从"
                            "触发击后一微秒生效，同目标刷新二十秒，多目标独立。"
                        ),
                        action_ids=chain.action_ids,
                        event_ids=chain.event_ids,
                    )
                    if interval is not None:
                        results.append(replace(
                            interval,
                            target_scope=f"fork-target:{target_id}",
                        ))
        return tuple(results)

    @staticmethod
    def nakupeda_random_semantics(selected: Any) -> NakupedaRandomSemantics | None:
        values = tuple(
            _parameter(selected.definition, name)
            for name in (
                "buff_Nakipeda_effect1",
                "buff_Nakipeda_effect2",
                "buff_Nakipeda_effect2CD",
                "buff_Nakipeda_effect3",
                "buff_Nakipeda_CD",
            )
        )
        if any(value is None for value in values):
            return None
        lowest, shield, shield_duration, team, cooldown = values
        return NakupedaRandomSemantics(
            cooldown_seconds=float(cooldown),
            outcome_probability=1.0 / 3.0,
            lowest_hp_heal_max_hp_ratio=float(lowest),
            owner_max_hp_shield_ratio=float(shield),
            shield_duration_seconds=float(shield_duration),
            team_heal_own_max_hp_ratio=float(team),
            applies_exactly_one_outcome=True,
            lowest_hp_tie_resolution="one_runtime_selected",
        )

    @staticmethod
    def nest_bird_mark_semantics() -> NestBirdMarkSemantics:
        return NestBirdMarkSemantics(
            outgoing_damage_additive=-0.18,
            duration_seconds=20.0,
            applies_after_triggering_hit=True,
            refreshes_same_target=True,
            tracks_targets_independently=True,
            requires_observed_q_hit_target=True,
        )

    @staticmethod
    def police_rat_semantics(selected: Any) -> PoliceRatDerivedHitSemantics | None:
        values = tuple(
            _parameter(selected.definition, name)
            for name in (
                "buff_Rat_CD",
                "buff_PoliceRat_SkillDamage",
                "buff_PoliceRat_Up",
            )
        )
        if any(value is None for value in values):
            return None
        cooldown, coefficient, boss_bonus = values
        return PoliceRatDerivedHitSemantics(
            summon_duration_seconds=30.0,
            cooldown_seconds=float(cooldown),
            attack_coefficient=float(coefficient),
            boss_damage_bonus=float(boss_bonus),
            derived_hit_can_crit=True,
            inherits_owner_damage_bonuses=True,
            uses_observed_axis_hits_only=True,
            confidence="低",
        )
