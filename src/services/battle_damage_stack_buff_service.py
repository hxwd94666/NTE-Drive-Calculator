# 依据逐击与扣时停时钟投影“命中后叠层”的持续 Buff。
"""Dynamic damage-triggered Buff stacks reconstructed from battle hits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


DAMAGE_STACK_EVENT_PREFIX = "DAMAGE_STACK_AFTER_HIT"
_DEMON_BLADE_MARKER = "fork_demonblade"


def _parameter(
    definition: Mapping[str, Any] | None,
    name_id: str,
) -> float | None:
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


def demon_blade_damage_stack_rules(
    selected: Any,
    rule_factory: type[Any],
) -> tuple[Any, ...]:
    """Build the confirmed runtime adapter omitted by the exported controller."""

    effect_id = str(selected.effect_definition_id)
    if _DEMON_BLADE_MARKER not in effect_id.casefold():
        return ()
    crit_damage = _parameter(
        selected.definition,
        "buff_DemonBlade_CritDamageUp",
    )
    duration = _parameter(selected.definition, "buff_DemonBlade_CD")
    if crit_damage is None or duration is None:
        return ()
    target_path = (
        "/Game/Blueprints/Abilities/Buff/Fork/Fork_DemonBlade/"
        "Buff_Fork_DemonBlade_CritDmgUp"
    )
    return (rule_factory(
        rule_id=f"{effect_id}:confirmed-damage-stack",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_damage_stack",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=target_path,
        target_name="妖刀：咒伤暴击伤害",
        target_scope="self",
        event_type=f"{DAMAGE_STACK_EVENT_PREFIX}|incantation|0.3",
        effect_type="ADD_AFTER_DAMAGE",
        duration_policy="HasDuration",
        duration_seconds=float(duration),
        stack_count=1,
        modifiers=(BattleBuffModifierEvidence(
            property_id="CritDamageBase",
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="confirmed_text",
            magnitude_value=float(crit_damage),
            calculation_asset_path=(
                "/Game/Blueprints/Abilities/Calculation/Fork/Fork_DemonBlade/"
                "Cau_Fork_DemonBlade_CIrtDmg"
            ),
            value_confidence="高",
        ),),
        stacking_type="AggregateByTarget",
        stack_limit_count=7,
    ),)


def _eligible_hit(rule: Any, hit: BattleAnalysisHit) -> bool:
    if (
        hit.character_id != rule.source_character_id
        or hit.direction != "outgoing"
        or hit.is_follow_up
        or hit.classification in {"topple", "mechanic"}
    ):
        return False
    attribute = hit.damage_attribute.casefold()
    if attribute not in {"", "unknown"}:
        return attribute == "incantation"
    effect = hit.gameplay_effect_id.casefold()
    return (
        effect.startswith("ge_player_zankou_")
        or effect.startswith("buff_reaction_5_new_")
    )


def infer_damage_stack_intervals(
    rules: Sequence[Any],
    *,
    hits: Sequence[BattleAnalysisHit],
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[BattleInferredBuffInterval, ...]:
    """Replay post-hit stacks on the active clock; time stop freezes all timers."""

    results: list[BattleInferredBuffInterval] = []
    ordinal = 0
    for rule in rules:
        if not rule.event_type.startswith(DAMAGE_STACK_EVENT_PREFIX):
            continue
        try:
            cooldown_seconds = float(rule.event_type.rsplit("|", 1)[1])
        except (IndexError, ValueError):
            continue
        duration_us = round(float(rule.duration_seconds or 0.0) * 1_000_000)
        cooldown_us = round(cooldown_seconds * 1_000_000)
        if duration_us <= 0:
            continue
        stack = 0
        last_trigger_active_us: int | None = None
        expires_active_us: int | None = None
        open_start_us: int | None = None
        open_event_ids: tuple[str, ...] = ()

        def active_time(raw_time_us: int) -> int:
            return project_timeline_time_us(
                raw_time_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )

        def append_interval(end_us: int) -> None:
            nonlocal ordinal
            if open_start_us is None or stack <= 0 or end_us <= open_start_us:
                return
            results.append(BattleInferredBuffInterval(
                interval_id=f"buff:damage-stack:{ordinal}:{rule.rule_id}",
                buff_asset_path=rule.target_asset_path,
                buff_name=rule.target_name,
                source_effect_definition_id=rule.source_effect_definition_id,
                source_kind=rule.source_kind,
                source_character_id=rule.source_character_id,
                source_character_name=rule.source_character_name,
                target_scope=rule.target_scope,
                start_us=open_start_us,
                end_us=end_us,
                stacks=stack,
                duration_policy=rule.duration_policy,
                state_confidence="中",
                value_confidence="高",
                inference_basis=(
                    "按逐击正向重放：咒属性直伤、DOT 与浊燃在伤害结算后叠层；"
                    "0.3 秒冷却和 15 秒持续时间均使用扣时停时钟。"
                ),
                trigger_event_type=rule.event_type,
                evidence_action_ids=(),
                evidence_event_ids=open_event_ids,
                modifiers=rule.modifiers,
                stacking_type=rule.stacking_type,
                stack_limit_count=rule.stack_limit_count,
            ))
            ordinal += 1

        eligible = sorted(
            (row for row in hits if _eligible_hit(rule, row)),
            key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
        )
        for hit in eligible:
            raw_us = hit.relative_time_us
            now_active_us = active_time(raw_us)
            if expires_active_us is not None and now_active_us >= expires_active_us:
                expiry_raw_us = unproject_timeline_time_us(
                    expires_active_us,
                    battle_start_us=0,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                    mode=ACTIVE_TIME_MODE,
                    prefer_interval_end=True,
                )
                append_interval(expiry_raw_us)
                stack = 0
                last_trigger_active_us = None
                expires_active_us = None
                open_start_us = None
                open_event_ids = ()
            if (
                last_trigger_active_us is not None
                and now_active_us - last_trigger_active_us < cooldown_us
            ):
                continue
            append_interval(raw_us + 1)
            stack = min(rule.stack_limit_count, stack + rule.stack_count)
            last_trigger_active_us = now_active_us
            expires_active_us = now_active_us + duration_us
            open_start_us = raw_us + 1
            open_event_ids = (hit.event_id,)
        if open_start_us is not None and expires_active_us is not None:
            expiry_raw_us = unproject_timeline_time_us(
                expires_active_us,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )
            append_interval(min(battle_end_us, expiry_raw_us))
    return tuple(results)
