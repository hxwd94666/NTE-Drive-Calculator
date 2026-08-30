# 将第二批人工确认弧盘的前后台、受击与逐目标状态投影为固定轴规则。
"""Stateful fork-refinement adapters that generic Buff events cannot express."""

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
from src.services.battle_fork_hit_adjustment_service import (
    BOXING_CANDY_REQUIREMENT_PREFIX,
)
from src.services.battle_fork_default_policy import BLACK_BOOK_DERIVED_HIT_CAN_CRIT
from src.services.battle_fork_trigger_refinement_service import (
    BattleForkTriggerRefinementService,
    ForkCriticalEvent,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


FORK_STATE_REFINEMENT_MODEL_VERSION = "battle-fork-state-refinement-v3"
BIT_GAME_BACK_BASE_EVENT = "FORK_BIT_GAME_BACKGROUND_BASE"
BIT_GAME_BACK_STACK_EVENT = "FORK_BIT_GAME_BACKGROUND_DAMAGE_STACK"
BIT_GAME_FRONT_BASE_EVENT = "FORK_BIT_GAME_FOREGROUND_BASE"
BIT_GAME_FRONT_STACK_EVENT = "FORK_BIT_GAME_FOREGROUND_NORMAL_PSYCHIC_STACK"
BITTER_CAKE_BEFORE_HIT_EVENT = "FORK_BITTER_CAKE_BEFORE_INCOMING_HIT"
BLACK_BOOK_LINKED_TARGET_EVENT = "FORK_BLACK_BOOK_LINKED_TARGET"
BLAST_CANDY_Q_BEGIN_EVENT = "FORK_BLAST_CANDY_Q_BEGIN"
BOXING_CANDY_STATIC_EVENT = "STATIC_EQUIPPED_SOURCE"

_BIT_GAME_MARKER = "upgradestar_pack_fork_bitgame"
_BITTER_CAKE_MARKER = "upgradestar_pack_fork_bittercake"
_BLACK_BOOK_MARKER = "upgradestar_pack_fork_blackbook"
_BLAST_CANDY_MARKER = "upgradestar_pack_fork_blastcandy"
_BOXING_CANDY_MARKER = "upgradestar_pack_fork_boxingcandy"
_AUDITED_MARKERS = frozenset({
    _BIT_GAME_MARKER,
    _BITTER_CAKE_MARKER,
    _BLACK_BOOK_MARKER,
    _BLAST_CANDY_MARKER,
    _BOXING_CANDY_MARKER,
})


@dataclass(frozen=True, slots=True)
class BlackBookDerivedHitSemantics:
    """Confirmed Black Book boundaries; crit capability remains unresolved."""

    unbalance_add: float
    summon_duration_seconds: float
    designation_period_seconds: float
    linked_dark_damage_bonus: float
    attack_coefficient: float
    initial_target_delay_seconds: float
    chain_count: int
    qte_actions_per_chain: int
    derived_hit_can_crit: bool | None
    derived_hit_uses_linked_bonus: bool


@dataclass(frozen=True, slots=True)
class BlackBookChainProgress:
    at_us: int
    unlocked_chains: int
    remaining_chains: int
    summon_ready: bool
    evidence_action_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ActiveSegment:
    start_us: int
    end_us: int
    active_character_id: int


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
    application_requirement: str = "",
    target_require_tags: Sequence[str] = (),
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        application_requirement_asset_path=application_requirement,
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
        stacking_type="AggregateBySource",
        stack_limit_count=stack_limit_count,
        cooldown_seconds=cooldown_seconds,
    )


def _rules_bit_game(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    back_base = _parameter(selected.definition, "buff_BitGame2_AtkUp1")
    back_stack = _parameter(selected.definition, "buff_BitGame2_AtkUp2")
    back_cooldown = _parameter(selected.definition, "buff_BitGame2_CD")
    front_base = _parameter(
        selected.definition,
        "buff_BitGame2_DamageUpPsycheBase1",
    )
    front_stack = _parameter(
        selected.definition,
        "buff_BitGame2_DamageUpPsycheBase2",
    )
    if None in {back_base, back_stack, back_cooldown, front_base, front_stack}:
        return ()
    return (
        _rule(
            selected,
            rule_factory,
            suffix="bit-game-back-base",
            name="引爆全场：后台当前角色攻击",
            scope="team_others",
            event_type=BIT_GAME_BACK_BASE_EVENT,
            modifiers=(_modifier("AtkUp", back_base),),
        ),
        _rule(
            selected,
            rule_factory,
            suffix="bit-game-back-stack",
            name="引爆全场：后台伤害攻击层数",
            scope="team_others",
            event_type=BIT_GAME_BACK_STACK_EVENT,
            modifiers=(_modifier("AtkUp", back_stack),),
            stack_limit_count=4,
            cooldown_seconds=back_cooldown,
        ),
        _rule(
            selected,
            rule_factory,
            suffix="bit-game-front-base",
            name="引爆全场：前台心灵伤害",
            scope="self",
            event_type=BIT_GAME_FRONT_BASE_EVENT,
            modifiers=(_modifier("DamageUpPsycheBase", front_base),),
        ),
        _rule(
            selected,
            rule_factory,
            suffix="bit-game-front-stack",
            name="引爆全场：前台普攻心灵层数",
            scope="self",
            event_type=BIT_GAME_FRONT_STACK_EVENT,
            modifiers=(_modifier("DamageUpPsycheBase", front_stack),),
            stack_limit_count=10,
            cooldown_seconds=0.3,
        ),
    )


def _rules_bitter_cake(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    value = _parameter(selected.definition, "buff_BitterCake_DefUp")
    duration = _parameter(selected.definition, "buff_BitterCake_CD")
    cooldown = _parameter(selected.definition, "buff_BitterCake_CD2")
    if None in {value, duration, cooldown}:
        return ()
    return (_rule(
        selected,
        rule_factory,
        suffix="bitter-cake-before-hit",
        name="良药苦口：受击前防御",
        scope="self",
        event_type=BITTER_CAKE_BEFORE_HIT_EVENT,
        duration_seconds=duration,
        cooldown_seconds=cooldown,
        modifiers=(_modifier("DefUp", value),),
    ),)


def _rules_black_book(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    unbalance = _parameter(selected.definition, "buff_BlackBook2_Unbal")
    dark_up = _parameter(
        selected.definition,
        "buff_BlackBook2_DamageUpChaosBase",
    )
    if None in {unbalance, dark_up}:
        return ()
    return (
        _rule(
            selected,
            rule_factory,
            suffix="black-book-unbalance",
            name="漆黑青春妄想：失衡强度",
            scope="self",
            event_type="STATIC_EQUIPPED_SOURCE",
            modifiers=(_modifier("UnbalIntensityAdd", unbalance),),
        ),
        _rule(
            selected,
            rule_factory,
            suffix="black-book-linked-dark",
            name="漆黑青春妄想：指定目标暗属性增伤",
            scope="unknown",
            event_type=BLACK_BOOK_LINKED_TARGET_EVENT,
            modifiers=(_modifier(
                "DamageUpChaosBase",
                dark_up,
                target_require_tags=("Ability.ForkSkill.BlackBook.Linked",),
            ),),
        ),
    )


def _rules_blast_candy(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    value = _parameter(selected.definition, "buff_BlastCandy_AtkUp")
    duration = _parameter(selected.definition, "buff_BlastCandy_CD")
    if None in {value, duration}:
        return ()
    return (_rule(
        selected,
        rule_factory,
        suffix="blast-candy-q",
        name="无畏之绵：Q 开始攻击",
        scope="self",
        event_type=BLAST_CANDY_Q_BEGIN_EVENT,
        duration_seconds=duration,
        modifiers=(_modifier("AtkUp", value),),
    ),)


def _rules_boxing_candy(selected: Any, rule_factory: type[Any]) -> tuple[Any, ...]:
    base = _parameter(selected.definition, "buff_BoxingCandy_Up")
    enhanced = _parameter(selected.definition, "buff_BoxingCandy_Up2")
    if None in {base, enhanced}:
        return ()
    return (_rule(
        selected,
        rule_factory,
        suffix="boxing-candy-damage",
        name="不屈之绵：逐击目标血线增伤",
        scope="self",
        event_type=BOXING_CANDY_STATIC_EVENT,
        modifiers=(_modifier(
            "DamageUpGeneralBase",
            base,
            application_requirement=(
                f"{BOXING_CANDY_REQUIREMENT_PREFIX}{enhanced:.12g}"
            ),
        ),),
    ),)


def _active_time(
    raw_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        raw_us, battle_start_us=0, intervals=intervals, mode=ACTIVE_TIME_MODE
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
    target_scope: str | None = None,
) -> BattleInferredBuffInterval | None:
    if end_us <= start_us or stacks <= 0:
        return None
    return BattleInferredBuffInterval(
        interval_id=f"buff:fork-state:{suffix}:{rule.rule_id}",
        buff_asset_path=rule.target_asset_path,
        buff_name=rule.target_name,
        source_effect_definition_id=rule.source_effect_definition_id,
        source_kind=rule.source_kind,
        source_character_id=rule.source_character_id,
        source_character_name=rule.source_character_name,
        target_scope=target_scope or rule.target_scope,
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
    switches = sorted(
        (
            row for row in actions
            if row.input_kind in {"A", "E", "Q", "QTE", "G"}
            and 0 <= row.start_us < battle_end_us
        ),
        key=lambda row: (row.start_us, row.action_id),
    )
    if not switches:
        return ()
    result: list[_ActiveSegment] = []
    active_id = switches[0].character_id
    start_us = 0
    for action in switches[1:]:
        if action.character_id == active_id:
            continue
        if action.start_us > start_us:
            result.append(_ActiveSegment(start_us, action.start_us, active_id))
        active_id = action.character_id
        start_us = action.start_us
    if start_us < battle_end_us:
        result.append(_ActiveSegment(start_us, battle_end_us, active_id))
    return tuple(result)


def _is_normal_psychic_hit(hit: BattleAnalysisHit) -> bool:
    identity = "|".join((
        hit.attack_type,
        hit.ability_id,
        hit.skill_name,
        hit.gameplay_effect_id,
    )).casefold()
    return (
        hit.damage_attribute.casefold() in {"psyche", "psychically"}
        and (
            "普攻" in hit.attack_type
            or "melee" in identity
            or hit.attack_type.casefold() == "normal"
        )
    )


def _stack_intervals(
    rule: Any,
    hits: Sequence[BattleAnalysisHit],
    *,
    segment: _ActiveSegment,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    basis: str,
    target_scope: str | None = None,
) -> tuple[BattleInferredBuffInterval, ...]:
    cooldown_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
    accepted: list[BattleAnalysisHit] = []
    last_active_us: int | None = None
    for hit in sorted(hits, key=lambda row: (row.relative_time_us, row.sequence)):
        now_active = _active_time(hit.relative_time_us, time_stop_intervals)
        if last_active_us is not None and now_active - last_active_us < cooldown_us:
            continue
        accepted.append(hit)
        last_active_us = now_active
    results = []
    stack = 0
    start_us: int | None = None
    evidence_ids: list[str] = []
    for ordinal, hit in enumerate(accepted):
        next_start = min(segment.end_us, hit.relative_time_us + 1)
        previous = _interval(
            rule,
            suffix=f"stack:{segment.start_us}:{ordinal}",
            start_us=start_us if start_us is not None else next_start,
            end_us=next_start,
            stacks=stack,
            basis=basis,
            event_ids=evidence_ids,
            target_scope=target_scope,
        )
        if previous is not None:
            results.append(previous)
        stack = min(rule.stack_limit_count, stack + 1)
        start_us = next_start
        evidence_ids.append(hit.event_id)
    final = _interval(
        rule,
        suffix=f"stack:{segment.start_us}:final",
        start_us=start_us if start_us is not None else segment.end_us,
        end_us=segment.end_us,
        stacks=stack,
        basis=basis,
        event_ids=evidence_ids,
        target_scope=target_scope,
    )
    if final is not None:
        results.append(final)
    return tuple(results)


class BattleForkStateRefinementService:
    """Build and replay the manually confirmed stateful fork batch."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkTriggerRefinementService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        if _BIT_GAME_MARKER in effect_id:
            return _rules_bit_game(selected, rule_factory)
        if _BITTER_CAKE_MARKER in effect_id:
            return _rules_bitter_cake(selected, rule_factory)
        if _BLACK_BOOK_MARKER in effect_id:
            return _rules_black_book(selected, rule_factory)
        if _BLAST_CANDY_MARKER in effect_id:
            return _rules_blast_candy(selected, rule_factory)
        if _BOXING_CANDY_MARKER in effect_id:
            return _rules_boxing_candy(selected, rule_factory)
        return BattleForkTriggerRefinementService.rules_for_selected_effect(
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
        critical_events: Sequence[ForkCriticalEvent] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = list(cls._infer_bit_game(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(cls._infer_bitter_cake(
            rules,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(cls._infer_blast_candy(
            rules,
            actions=actions,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        ))
        results.extend(BattleForkTriggerRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            critical_events=critical_events,
        ))
        return tuple(sorted(results, key=lambda row: (
            row.start_us,
            row.end_us,
            row.source_character_id,
            row.buff_asset_path,
        )))

    @classmethod
    def _infer_bit_game(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result: list[BattleInferredBuffInterval] = []
        for base_rule in (
            row for row in rules
            if row.event_type in {BIT_GAME_BACK_BASE_EVENT, BIT_GAME_FRONT_BASE_EVENT}
        ):
            is_front = base_rule.event_type == BIT_GAME_FRONT_BASE_EVENT
            stack_event = (
                BIT_GAME_FRONT_STACK_EVENT if is_front else BIT_GAME_BACK_STACK_EVENT
            )
            stack_rule = next((
                row for row in rules
                if row.source_effect_definition_id
                == base_rule.source_effect_definition_id
                and row.event_type == stack_event
            ), None)
            if stack_rule is None:
                continue
            for segment in _active_segments(actions, battle_end_us):
                owner_active = (
                    segment.active_character_id == base_rule.source_character_id
                )
                if owner_active != is_front:
                    continue
                target_scope = (
                    None
                    if is_front
                    else f"character:{segment.active_character_id}"
                )
                base = _interval(
                    base_rule,
                    suffix=f"bit-game-base:{segment.start_us}",
                    start_us=segment.start_us,
                    end_us=segment.end_us,
                    stacks=1,
                    basis=(
                        "由直接 A/E/Q/QTE 动作切换来源重建前后台；装备者进出前台"
                        "时对应状态与层数立即重置。"
                    ),
                    target_scope=target_scope,
                )
                if base is not None:
                    result.append(base)
                candidates = tuple(
                    hit for hit in hits
                    if hit.character_id == base_rule.source_character_id
                    and hit.direction == "outgoing"
                    and segment.start_us <= hit.relative_time_us < segment.end_us
                    and (not is_front or _is_normal_psychic_hit(hit))
                )
                result.extend(_stack_intervals(
                    stack_rule,
                    candidates,
                    segment=segment,
                    time_stop_intervals=time_stop_intervals,
                    basis=(
                        "每个被冷却接受的伤害时点只叠一层；同一时点多目标不"
                        "重复计层，新层从该击结算后一微秒开始，切换状态清空。"
                    ),
                    target_scope=target_scope,
                ))
        return tuple(result)

    @classmethod
    def _infer_bitter_cake(
        cls,
        rules: Sequence[Any],
        *,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result = []
        for rule in (
            row for row in rules
            if row.event_type == BITTER_CAKE_BEFORE_HIT_EVENT
            and row.duration_seconds is not None
        ):
            cooldown_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
            duration_us = round(rule.duration_seconds * 1_000_000)
            last_active_us: int | None = None
            for ordinal, hit in enumerate(sorted(
                (
                    row for row in hits
                    if row.direction == "incoming"
                    and row.character_id == rule.source_character_id
                ),
                key=lambda row: (row.relative_time_us, row.sequence),
            )):
                now_active = _active_time(hit.relative_time_us, time_stop_intervals)
                if last_active_us is not None and now_active - last_active_us < cooldown_us:
                    continue
                last_active_us = now_active
                expiry = _raw_expiry(
                    now_active + duration_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                interval = _interval(
                    rule,
                    suffix=f"bitter-cake:{ordinal}",
                    start_us=hit.relative_time_us,
                    end_us=min(battle_end_us, expiry),
                    stacks=1,
                    basis=(
                        "静态触发为受击计算前；区间从本次受击时点开始，故触发"
                        "它的本次攻击已读取防御提升，来源侧 20 秒冷却。"
                    ),
                    event_ids=(hit.event_id,),
                )
                if interval is not None:
                    result.append(interval)
        return tuple(result)

    @classmethod
    def _infer_blast_candy(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        result = []
        for rule in (
            row for row in rules
            if row.event_type == BLAST_CANDY_Q_BEGIN_EVENT
            and row.duration_seconds is not None
        ):
            duration_us = round(rule.duration_seconds * 1_000_000)
            role_actions = sorted(
                (
                    row for row in actions
                    if row.character_id == rule.source_character_id
                    and row.input_kind == "Q"
                ),
                key=lambda row: (row.start_us, row.action_id),
            )
            chains: list[_RefreshChain] = []
            for action in role_actions:
                now_active = _active_time(action.start_us, time_stop_intervals)
                proposed_end = now_active + duration_us
                if chains and now_active < chains[-1].end_active_us:
                    chains[-1].end_active_us = proposed_end
                    chains[-1].action_ids.append(action.action_id)
                    chains[-1].event_ids.extend(action.evidence_event_ids)
                else:
                    chains.append(_RefreshChain(
                        start_us=action.start_us,
                        end_active_us=proposed_end,
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
                    suffix=f"blast-candy:{ordinal}",
                    start_us=chain.start_us,
                    end_us=min(battle_end_us, expiry),
                    stacks=1,
                    basis=(
                        "Q 开始即获得攻击提升，触发它的本次 Q 也享受；"
                        "重复触发只刷新而不叠加。"
                    ),
                    action_ids=chain.action_ids,
                    event_ids=chain.event_ids,
                )
                if interval is not None:
                    result.append(interval)
        return tuple(result)

    @staticmethod
    def black_book_semantics(selected: Any) -> BlackBookDerivedHitSemantics | None:
        names = (
            "buff_BlackBook2_Unbal",
            "buff_BlackBook2_CD",
            "buff_BlackBook2_CD2",
            "buff_BlackBook2_DamageUpChaosBase",
            "buff_BlackBook2_SkillDamage",
        )
        values = tuple(_parameter(selected.definition, name) for name in names)
        if any(value is None for value in values):
            return None
        unbalance, duration, period, dark_up, coefficient = values
        assert all(value is not None for value in values)
        return BlackBookDerivedHitSemantics(
            unbalance_add=float(unbalance),
            summon_duration_seconds=float(duration),
            designation_period_seconds=float(period),
            linked_dark_damage_bonus=float(dark_up),
            attack_coefficient=float(coefficient),
            initial_target_delay_seconds=0.0,
            chain_count=2,
            qte_actions_per_chain=1,
            derived_hit_can_crit=BLACK_BOOK_DERIVED_HIT_CAN_CRIT,
            derived_hit_uses_linked_bonus=True,
        )

    @staticmethod
    def black_book_chain_progress(
        actions: Sequence[BattleInferredAction],
        *,
        tracking_start_us: int,
        tracking_end_us: int,
    ) -> tuple[BlackBookChainProgress, ...]:
        progress = [BlackBookChainProgress(
            at_us=tracking_start_us,
            unlocked_chains=0,
            remaining_chains=2,
            summon_ready=False,
            evidence_action_ids=(),
        )]
        accepted = sorted(
            (
                row for row in actions
                if row.input_kind == "QTE"
                and tracking_start_us <= row.start_us < tracking_end_us
            ),
            key=lambda row: (row.start_us, row.action_id),
        )[:2]
        evidence: list[str] = []
        for unlocked, action in enumerate(accepted, start=1):
            evidence.append(action.action_id)
            progress.append(BlackBookChainProgress(
                at_us=action.start_us,
                unlocked_chains=unlocked,
                remaining_chains=2 - unlocked,
                summon_ready=unlocked == 2,
                evidence_action_ids=tuple(evidence),
            ))
        return tuple(progress)
