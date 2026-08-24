# 重放伤害向弧盘的逐击叠层、技能消费与固定轴状态。
"""State machines for damage-first fork completion rules."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_damage_composition_service import (
    classify_battle_hit_channel,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


ROSE_STACK_EVENT = "FORK_ROSE_DAMAGE_STACK"
TIGER_NORMAL_STACK_EVENT = "FORK_TIGER_NORMAL_STACK"
TIGER_COMMANDER_EVENT = "FORK_TIGER_COMMANDER_INFERRED"
TIME_Q_CONSUME_EVENT = "FORK_TIME_Q_CONSUME"
MOON_PSYCHIC_STACK_EVENT = "FORK_MOON_PSYCHIC_STACK"
SPIDER_Q_CONSUME_EVENT = "FORK_SPIDER_Q_CONSUME"

_CONTINUOUS_CHANNELS = frozenset({
    "dot",
    "special_nightmare",
    "special_zankou_erosion",
    "special_zankou_venom",
    "reaction_scorch",
})


@dataclass(frozen=True, slots=True)
class _TimedOccurrence:
    time_us: int
    action_ids: tuple[str, ...] = ()
    event_ids: tuple[str, ...] = ()


@dataclass(slots=True)
class _InferredCast:
    """One cast after collapsing overlapping damage-window fragments."""

    character_id: int
    input_kind: str
    action_name: str
    start_us: int
    end_us: int
    gameplay_effect_ids: set[str]
    action_ids: list[str]
    event_ids: list[str]


def _deduplicate_overlapping_casts(
    actions: Sequence[BattleInferredAction],
) -> tuple[_InferredCast, ...]:
    """Collapse only overlapping fragments backed by the same GE source.

    The shared action axis is damage-window based. Team hits can interleave and
    split one cast into several overlapping actions, but a trigger such as
    「荒时」must count the release once. Non-overlapping repetitions remain
    independent casts.
    """

    casts: list[_InferredCast] = []
    latest_by_key: dict[tuple[int, str, str], _InferredCast] = {}
    for action in sorted(actions, key=lambda row: (row.start_us, row.action_id)):
        effects = {
            value.casefold()
            for value in action.gameplay_effect_ids
            if value.strip()
        }
        key = (
            action.character_id,
            action.input_kind,
            action.action_name.casefold(),
        )
        previous = latest_by_key.get(key)
        if (
            previous is not None
            and action.start_us < previous.end_us
            and action.end_us > previous.start_us
            and effects
            and previous.gameplay_effect_ids.intersection(effects)
        ):
            previous.start_us = min(previous.start_us, action.start_us)
            previous.end_us = max(previous.end_us, action.end_us)
            previous.gameplay_effect_ids.update(effects)
            previous.action_ids.append(action.action_id)
            previous.event_ids.extend(action.evidence_event_ids)
            continue
        cast = _InferredCast(
            character_id=action.character_id,
            input_kind=action.input_kind,
            action_name=action.action_name,
            start_us=action.start_us,
            end_us=action.end_us,
            gameplay_effect_ids=effects,
            action_ids=[action.action_id],
            event_ids=list(action.evidence_event_ids),
        )
        casts.append(cast)
        latest_by_key[key] = cast
    return tuple(sorted(casts, key=lambda row: (row.start_us, row.action_ids[0])))


def _active_time(
    value_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return project_timeline_time_us(
        value_us,
        battle_start_us=0,
        intervals=intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _raw_time(
    value_us: int,
    *,
    battle_end_us: int,
    intervals: Sequence[tuple[int | None, int | None]],
) -> int:
    return unproject_timeline_time_us(
        value_us,
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
    modifiers: tuple[BattleBuffModifierEvidence, ...] | None = None,
    state_confidence: str = "中",
) -> BattleInferredBuffInterval | None:
    if start_us >= end_us or stacks <= 0:
        return None
    return BattleInferredBuffInterval(
        interval_id=f"buff:fork-damage:{suffix}:{rule.rule_id}",
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
        state_confidence=state_confidence,
        value_confidence="高",
        inference_basis=basis,
        trigger_event_type=rule.event_type,
        evidence_action_ids=tuple(action_ids),
        evidence_event_ids=tuple(event_ids),
        modifiers=rule.modifiers if modifiers is None else modifiers,
        stacking_type=rule.stacking_type,
        stack_limit_count=rule.stack_limit_count,
    )


def _independent_occurrence_intervals(
    rule: Any,
    occurrences: Sequence[_TimedOccurrence],
    *,
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    basis: str,
) -> tuple[BattleInferredBuffInterval, ...]:
    if rule.duration_seconds is None:
        return ()
    duration_us = round(rule.duration_seconds * 1_000_000)
    results = []
    accepted_active: list[int] = []
    cooldown_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
    for ordinal, occurrence in enumerate(sorted(
        occurrences,
        key=lambda row: row.time_us,
    )):
        now_active = _active_time(occurrence.time_us, time_stop_intervals)
        if accepted_active and now_active - accepted_active[-1] < cooldown_us:
            continue
        accepted_active.append(now_active)
        expiry = _raw_time(
            now_active + duration_us,
            battle_end_us=battle_end_us,
            intervals=time_stop_intervals,
        )
        interval = _interval(
            rule,
            suffix=f"occurrence:{ordinal}",
            start_us=min(battle_end_us, occurrence.time_us + 1),
            end_us=min(battle_end_us, expiry),
            stacks=1,
            basis=basis,
            action_ids=occurrence.action_ids,
            event_ids=occurrence.event_ids,
        )
        if interval is not None:
            results.append(interval)
    return tuple(results)


class BattleForkDamageStateService:
    """Replay only stateful rules selected by the damage catalog."""

    @classmethod
    def infer_specialized(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        results = []
        results.extend(cls._infer_tiger(
            rules, actions, hits, battle_end_us, time_stop_intervals
        ))
        results.extend(cls._infer_rose(
            rules, actions, hits, battle_end_us, time_stop_intervals
        ))
        results.extend(cls._infer_moon(
            rules, hits, battle_end_us, time_stop_intervals
        ))
        results.extend(cls._infer_time(
            rules,
            actions,
            battle_end_us,
            time_stop_intervals,
        ))
        results.extend(cls._infer_spider(
            rules, actions, hits, battle_end_us, time_stop_intervals
        ))
        return tuple(sorted(results, key=lambda row: (
            row.start_us,
            row.end_us,
            row.source_character_id,
            row.buff_asset_path,
        )))

    @staticmethod
    def _infer_tiger(rules, actions, hits, battle_end_us, time_stops):
        results = []
        for rule in (row for row in rules if row.event_type == TIGER_NORMAL_STACK_EVENT):
            occurrences = tuple(
                _TimedOccurrence(row.start_us, (row.action_id,), row.evidence_event_ids)
                for row in actions
                if row.character_id == rule.source_character_id
                and row.input_kind in {"E", "Q"}
            )
            results.extend(_independent_occurrence_intervals(
                rule,
                occurrences,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stops,
                basis=(
                    "每次 E 或 Q 开始增加一层普攻与极限反击增伤；"
                    "每层独立持续十五个有效战斗秒，命中时最多采用两层。"
                ),
            ))
        for rule in (row for row in rules if row.event_type == TIGER_COMMANDER_EVENT):
            results.extend(BattleForkDamageStateService._infer_tiger_commander(
                rule,
                actions,
                hits=hits,
                battle_end_us=battle_end_us,
                time_stops=time_stops,
            ))
        return tuple(results)

    @staticmethod
    def _infer_tiger_commander(
        rule: Any,
        actions: Sequence[BattleInferredAction],
        *,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stops: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        """Infer an unobservable R only after dual tokens and a damage step."""

        if rule.duration_seconds is None or not rule.modifiers:
            return ()
        owner_actions = sorted(
            (
                action for action in actions
                if action.character_id == rule.source_character_id
                and action.input_kind in {"E", "Q"}
            ),
            key=lambda row: (
                row.end_us if row.input_kind == "E" else row.start_us,
                row.action_id,
            ),
        )
        token_window_us = round(float(rule.cooldown_seconds or 0.0) * 1_000_000)
        if token_window_us <= 0:
            return ()
        pending: dict[str, tuple[int, BattleInferredAction]] = {}
        unlocks: list[tuple[int, tuple[BattleInferredAction, ...]]] = []
        for action in owner_actions:
            token_us = action.end_us if action.input_kind == "E" else action.start_us
            now_active = _active_time(token_us, time_stops)
            other_kind = "Q" if action.input_kind == "E" else "E"
            other = pending.get(other_kind)
            if other is not None and now_active - other[0] <= token_window_us:
                unlocks.append((token_us, (other[1], action)))
                pending.clear()
                continue
            pending[action.input_kind] = (now_active, action)
            pending = {
                kind: row
                for kind, row in pending.items()
                if now_active - row[0] <= token_window_us
            }

        owner_hits = tuple(sorted(
            (
                hit for hit in hits
                if hit.character_id == rule.source_character_id
                and hit.direction == "outgoing"
                and hit.damage > 0
            ),
            key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
        ))
        results = []
        accepted_until_active = -1
        for unlock_us, evidence_actions in unlocks:
            unlock_active = _active_time(unlock_us, time_stops)
            if unlock_active < accepted_until_active:
                continue
            step = BattleForkDamageStateService._find_tiger_damage_step(
                owner_hits,
                unlock_us=unlock_us,
                unlock_active=unlock_active,
                token_window_us=token_window_us,
                time_stops=time_stops,
            )
            if step is None:
                continue
            start_hit, ratio, event_ids = step
            start_active = _active_time(start_hit.relative_time_us, time_stops)
            expiry_active = start_active + round(rule.duration_seconds * 1_000_000)
            expiry_us = _raw_time(
                expiry_active,
                battle_end_us=battle_end_us,
                intervals=time_stops,
            )
            base = rule.modifiers[0]
            modifier = BattleBuffModifierEvidence(
                property_id=base.property_id,
                modifier_operation=base.modifier_operation,
                magnitude_kind=base.magnitude_kind,
                magnitude_value=base.magnitude_value,
                calculation_asset_path=base.calculation_asset_path,
                value_confidence=base.value_confidence,
                modifier_group_ordinal=base.modifier_group_ordinal,
                application_requirement_asset_path=(
                    f"battle-hit-target|id={start_hit.target_id}"
                ),
                source_require_tags=base.source_require_tags,
                source_ignore_tags=base.source_ignore_tags,
                target_require_tags=base.target_require_tags,
                target_ignore_tags=base.target_ignore_tags,
            )
            interval = _interval(
                rule,
                suffix=f"commander:{start_hit.event_id}",
                start_us=start_hit.relative_time_us,
                end_us=min(battle_end_us, expiry_us),
                stacks=1,
                basis=(
                    "E 实际结束取得左虎符、Q begin 取得右虎符，并在十五个有效战斗秒内凑齐；"
                    "未取得主动 R "
                    f"事件，因此只在同一目标的可比伤害项从本击开始出现一致倍率阶跃"
                    f"（中位比值 {ratio:.3f}）时，低置信推断司令虎符起点。"
                ),
                action_ids=tuple(action.action_id for action in evidence_actions),
                event_ids=event_ids,
                modifiers=(modifier,),
                state_confidence="低",
            )
            if interval is not None:
                results.append(interval)
                accepted_until_active = expiry_active
        return tuple(results)

    @staticmethod
    def _find_tiger_damage_step(
        hits: Sequence[BattleAnalysisHit],
        *,
        unlock_us: int,
        unlock_active: int,
        token_window_us: int,
        time_stops: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleAnalysisHit, float, tuple[str, ...]] | None:
        def is_normal_or_counter(hit: BattleAnalysisHit) -> bool:
            identity = "|".join((
                hit.attack_type,
                hit.gameplay_effect_id,
                hit.ability_id,
                hit.skill_name,
                hit.damage_name,
            )).casefold()
            return (
                (
                    "_melee" in hit.ability_id.casefold()
                    and "ultraskill" not in hit.ability_id.casefold()
                )
                or "extrem" in identity
                or "极限反击" in hit.attack_type
                or "闪避反击" in hit.attack_type
            )

        def signature(hit: BattleAnalysisHit) -> tuple[str, ...]:
            damage_identity = (
                hit.gameplay_effect_id
                or f"{hit.ability_id}|{hit.damage_name}|{hit.attack_type}"
            )
            return (
                hit.target_id,
                damage_identity.casefold(),
                hit.damage_attribute.casefold(),
                hit.classification.casefold(),
            )

        active_times = {
            hit.event_id: _active_time(hit.relative_time_us, time_stops)
            for hit in hits
        }
        by_target: dict[str, list[BattleAnalysisHit]] = {}
        by_signature: dict[tuple[str, ...], list[BattleAnalysisHit]] = {}
        for hit in hits:
            by_target.setdefault(hit.target_id, []).append(hit)
            by_signature.setdefault(signature(hit), []).append(hit)
        signature_times = {
            key: [row.relative_time_us for row in rows]
            for key, rows in by_signature.items()
        }
        target_times = {
            key: [row.relative_time_us for row in rows]
            for key, rows in by_target.items()
        }
        candidates = tuple(
            hit for hit in hits
            if hit.relative_time_us >= unlock_us
            and not is_normal_or_counter(hit)
            and active_times[hit.event_id] - unlock_active <= token_window_us
        )
        for candidate in candidates:
            candidate_active = active_times[candidate.event_id]
            post_end_active = candidate_active + 2_000_000
            comparable = []
            target_rows = by_target.get(candidate.target_id, ())
            post_start = bisect_left(
                target_times.get(candidate.target_id, ()),
                candidate.relative_time_us,
            )
            for post in target_rows[post_start:post_start + 24]:
                post_active = active_times[post.event_id]
                if post_active > post_end_active:
                    break
                if not (
                    candidate_active <= post_active <= post_end_active
                ):
                    continue
                if is_normal_or_counter(post):
                    continue
                key = signature(post)
                history = by_signature[key]
                history_end = bisect_left(
                    signature_times[key],
                    candidate.relative_time_us,
                )
                prior = [
                    row.damage
                    for row in history[max(0, history_end - 5):history_end]
                    if candidate_active - active_times[row.event_id] <= 10_000_000
                    and not is_normal_or_counter(row)
                ]
                if not prior:
                    continue
                baseline = float(median(prior))
                if baseline <= 0:
                    continue
                comparable.append((post, post.damage / baseline))
            candidate_ratio = next(
                (ratio for hit, ratio in comparable if hit.event_id == candidate.event_id),
                None,
            )
            rising = tuple(
                (hit, ratio) for hit, ratio in comparable
                if 1.025 <= ratio <= 1.35
            )
            if candidate_ratio is None or not (1.025 <= candidate_ratio <= 1.35):
                continue
            if len(rising) < 2 or len(rising) * 2 < len(comparable):
                continue
            signatures = {signature(hit) for hit, _ratio in rising}
            if len(signatures) < 2 and len(rising) < 3:
                continue
            ratios = tuple(ratio for _hit, ratio in rising)
            if max(ratios) - min(ratios) > 0.04:
                continue
            evidence = tuple(dict.fromkeys(
                hit.event_id for hit, _ratio in rising
            ))
            return candidate, float(median(ratios)), evidence
        return None

    @staticmethod
    def _infer_rose(rules, actions, hits, battle_end_us, time_stops):
        results = []
        for rule in (row for row in rules if row.event_type == ROSE_STACK_EVENT):
            if rule.duration_seconds is None:
                continue
            role_id = rule.source_character_id
            events = [
                (row.start_us, 10, (row.action_id,), row.evidence_event_ids)
                for row in actions
                if row.character_id == role_id and row.input_kind == "E"
            ]
            last_dot_active = None
            for hit in sorted(hits, key=lambda row: (row.relative_time_us, row.sequence)):
                if hit.character_id != role_id or hit.direction != "outgoing":
                    continue
                if classify_battle_hit_channel(hit)[0] not in _CONTINUOUS_CHANNELS:
                    continue
                now_active = _active_time(hit.relative_time_us, time_stops)
                if last_dot_active is not None and now_active - last_dot_active < 300_000:
                    continue
                last_dot_active = now_active
                events.append((hit.relative_time_us + 1, 1, (), (hit.event_id,)))
            stack = 0
            expiry_active = None
            segment_start = None
            action_ids = ()
            event_ids = ()
            for ordinal, (time_us, amount, action_refs, event_refs) in enumerate(
                sorted(events, key=lambda row: row[0])
            ):
                now_active = _active_time(time_us, time_stops)
                if expiry_active is not None and now_active >= expiry_active:
                    stack = 0
                    segment_start = None
                elif segment_start is not None:
                    previous = _interval(
                        rule,
                        suffix=f"rose:{ordinal}:previous",
                        start_us=segment_start,
                        end_us=time_us,
                        stacks=stack,
                        basis="持续伤害每 0.3 秒最多叠一层；E 开始立即补满十层。",
                        action_ids=action_ids,
                        event_ids=event_ids,
                    )
                    if previous is not None:
                        results.append(previous)
                stack = min(
                    rule.stack_limit_count,
                    max(stack, amount) if amount == 10 else stack + amount,
                )
                segment_start = time_us
                expiry_active = now_active + round(rule.duration_seconds * 1_000_000)
                action_ids = action_refs
                event_ids = event_refs
            if segment_start is not None and expiry_active is not None:
                expiry = _raw_time(
                    expiry_active,
                    battle_end_us=battle_end_us,
                    intervals=time_stops,
                )
                final = _interval(
                    rule,
                    suffix="rose:final",
                    start_us=segment_start,
                    end_us=min(battle_end_us, expiry),
                    stacks=stack,
                    basis="持续伤害每 0.3 秒最多叠一层；E 开始立即补满十层。",
                    action_ids=action_ids,
                    event_ids=event_ids,
                )
                if final is not None:
                    results.append(final)
        return tuple(results)

    @staticmethod
    def _infer_moon(rules, hits, battle_end_us, time_stops):
        results = []
        for rule in (row for row in rules if row.event_type == MOON_PSYCHIC_STACK_EVENT):
            occurrences = tuple(
                _TimedOccurrence(row.relative_time_us, event_ids=(row.event_id,))
                for row in hits
                if row.character_id == rule.source_character_id
                and row.direction == "outgoing"
                and row.damage_attribute.casefold() == "psyche"
            )
            results.extend(_independent_occurrence_intervals(
                rule,
                occurrences,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stops,
                basis=(
                    "每次正式魂属性伤害结算后增加一层，0.1 秒最多一层；"
                    "每层独立持续五个有效战斗秒，最多采用十层。"
                ),
            ))
        return tuple(results)

    @staticmethod
    def _infer_time(rules, actions, battle_end_us, time_stops):
        results = []
        distinct_casts = _deduplicate_overlapping_casts(tuple(
            row for row in actions if row.input_kind in {"E", "Q", "QTE"}
        ))
        for rule in (row for row in rules if row.event_type == TIME_Q_CONSUME_EVENT):
            role_id = rule.source_character_id
            in_maze = False
            stacks = 0
            evidence_actions = []
            evidence_events = []
            for action in distinct_casts:
                if action.character_id == role_id and action.input_kind == "E":
                    in_maze = True
                    stacks = 0
                    evidence_actions = list(action.action_ids)
                    evidence_events = list(action.event_ids)
                elif in_maze and action.character_id != role_id and action.input_kind in {"E", "QTE"}:
                    stacks = min(3, stacks + 1)
                    evidence_actions.extend(action.action_ids)
                    evidence_events.extend(action.event_ids)
                elif in_maze and action.character_id == role_id and action.input_kind == "Q":
                    crit = tuple(row for row in rule.modifiers if row.property_id == "CritDamageBase")
                    if len(crit) != 2:
                        continue
                    scaled = BattleBuffModifierEvidence(
                        property_id="CritDamageBase",
                        modifier_operation=crit[1].modifier_operation,
                        magnitude_kind=crit[1].magnitude_kind,
                        magnitude_value=float(crit[1].magnitude_value or 0.0) * stacks,
                        calculation_asset_path=crit[1].calculation_asset_path,
                        value_confidence=crit[1].value_confidence,
                        source_require_tags=crit[1].source_require_tags,
                    )
                    defence = tuple(
                        row for row in rule.modifiers
                        if row.property_id == "DefIgnore" and stacks == 3
                    )
                    start_active = _active_time(action.start_us, time_stops)
                    expiry = _raw_time(
                        start_active
                        + round(float(rule.duration_seconds or 0) * 1_000_000),
                        battle_end_us=battle_end_us,
                        intervals=time_stops,
                    )
                    interval = _interval(
                        rule,
                        suffix=f"time:{action.action_ids[0]}",
                        start_us=action.start_us,
                        end_us=min(battle_end_us, expiry),
                        stacks=1,
                        basis=(
                            f"E 建立荒时迷宫并清零；队友 E/QTE 累积荒时；"
                            f"本次 Q 消耗 {stacks} 层荒时，三层时才附加无视防御；"
                            "强化从 Q 起点开始并按有效战斗时钟持续。"
                        ),
                        action_ids=(*evidence_actions, *action.action_ids),
                        event_ids=(*evidence_events, *action.event_ids),
                        modifiers=(crit[0], scaled, *defence),
                    )
                    if interval is not None:
                        results.append(interval)
                    in_maze = False
                    stacks = 0
        return tuple(results)

    @staticmethod
    def _infer_spider(rules, actions, hits, battle_end_us, time_stops):
        results = []
        for rule in (row for row in rules if row.event_type == SPIDER_Q_CONSUME_EVENT):
            role_id = rule.source_character_id
            events = [
                (row.relative_time_us + 1, "A", row.event_id, "")
                for row in hits
                if row.character_id == role_id
                and row.direction == "outgoing"
                and (
                    row.attack_type in {"普攻", "普通攻击"}
                    or "_melee" in row.ability_id.casefold()
                )
            ]
            events.extend(
                (row.start_us, "Q", "", row.action_id)
                for row in actions
                if row.character_id == role_id and row.input_kind == "Q"
            )
            stacks = 0
            last_stack_active = None
            evidence_hits = []
            for time_us, kind, event_id, action_id in sorted(events):
                now_active = _active_time(time_us, time_stops)
                if kind == "A":
                    if last_stack_active is None or now_active - last_stack_active >= 500_000:
                        stacks = min(8, stacks + 1)
                        last_stack_active = now_active
                        evidence_hits.append(event_id)
                    continue
                if stacks <= 0:
                    continue
                base, extra = rule.modifiers
                total = float(base.magnitude_value or 0.0) * stacks
                if stacks == 8:
                    total += float(extra.magnitude_value or 0.0)
                modifier = BattleBuffModifierEvidence(
                    property_id="AtkUp",
                    modifier_operation=base.modifier_operation,
                    magnitude_kind=base.magnitude_kind,
                    magnitude_value=total,
                    calculation_asset_path=base.calculation_asset_path,
                    value_confidence=base.value_confidence,
                )
                expiry = _raw_time(
                    now_active + round(float(rule.duration_seconds or 0) * 1_000_000),
                    battle_end_us=battle_end_us,
                    intervals=time_stops,
                )
                interval = _interval(
                    rule,
                    suffix=f"spider:{action_id}",
                    start_us=time_us,
                    end_us=min(battle_end_us, expiry),
                    stacks=1,
                    basis=(
                        f"普通攻击每 0.5 秒最多获得一层蜘识；Q 消耗 {stacks} 层，"
                        "八层时追加额外全队攻击力。"
                    ),
                    action_ids=(action_id,),
                    event_ids=evidence_hits,
                    modifiers=(modifier,),
                )
                if interval is not None:
                    results.append(interval)
                stacks = 0
                last_stack_active = None
                evidence_hits = []
        return tuple(results)
