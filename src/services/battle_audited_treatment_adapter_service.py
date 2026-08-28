# 将已审计且能由当前战报证据唯一定位的角色治疗行为投影为事件。
"""Character treatment producers beyond Oneiroi's direct skill events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import ceil
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


AUDITED_TREATMENT_ADAPTER_MODEL_VERSION = "battle-audited-treatment-v1"

_LACRIMOSA_IDS = frozenset({1004})
_LACRIMOSA_PERIOD_US = 3_000_000
_LACRIMOSA_RECOVER_RATIO = 0.015
_EDGAR_ID = 1021
_EDGAR_Q_BASE_TICKS = 10
_EDGAR_Q_PERIOD_US = 1_000_000
_SHINKU_ID = 1076
_SHINKU_EFFECT5_RECOVER_RATIO = 3.0
_KUHARA_ID = 1055
_KUHARA_Q_SETTLEMENT_TOLERANCE_US = 500_000
_ZANKOU_ID = 1036
_ZANKOU_TREATMENT_PERIOD_US = 1_000_000


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


def _characters(
    build: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    return tuple((build or {}).get("characters") or ())


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


def _base_attack(character: Mapping[str, Any]) -> float | None:
    values = tuple(
        float(row.get("value") or 0.0)
        for row in character.get("stats") or ()
        if str(row.get("property_id") or "") == "AtkBase"
        and str(row.get("source_group") or "") in {"character", "fork"}
    )
    return sum(values) if values else None


def _max_health(character: Mapping[str, Any]) -> float | None:
    totals: dict[str, float] = {}
    for row in character.get("stats") or ():
        property_id = str(row.get("property_id") or "")
        totals[property_id] = totals.get(property_id, 0.0) + float(
            row.get("value") or 0.0
        )
    base = totals.get("HPMaxBase", 0.0)
    if base <= 0:
        return None
    return base * (1.0 + totals.get("HPMaxUp", 0.0)) + totals.get(
        "HPMaxAdd",
        0.0,
    )


def _nightmare_hit(hit: BattleAnalysisHit, source_ids: frozenset[int]) -> bool:
    if hit.character_id not in source_ids or hit.damage <= 0:
        return False
    text = "|".join((
        hit.damage_name,
        hit.skill_name,
        hit.gameplay_effect_id,
    )).casefold()
    return any(marker in text for marker in (
        "噩梦",
        "nightmare",
        "lacrimosa_meleetotal",
    ))


def _hit_index(
    hits: Sequence[BattleAnalysisHit],
) -> dict[str, BattleAnalysisHit]:
    return {row.event_id: row for row in hits}


def is_kuhara_q_settlement_hit(
    hit: BattleAnalysisHit,
    actions: Sequence[BattleInferredAction],
) -> bool:
    """Return whether a BudBoom hit is formally tied to one observed Q action."""

    if (
        hit.character_id != _KUHARA_ID
        or "kuhara_budboom_damage" not in hit.gameplay_effect_id.casefold()
    ):
        return False
    return any(
        action.character_id == _KUHARA_ID
        and action.input_kind == "Q"
        and action.start_us <= hit.relative_time_us
        <= action.end_us + _KUHARA_Q_SETTLEMENT_TOLERANCE_US
        for action in actions
    )


class BattleAuditedTreatmentAdapterService:
    """Emit only treatment events supported by frozen build and axis facts."""

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        state_buff_intervals: Sequence[BattleInferredBuffInterval] = (),
        zankou_effect_three_recover_ratio: float | None = None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        results = [
            *cls._lacrimosa_effect_five(
                build=build,
                hits=hits,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ),
            *cls._edgar_hold_e(
                actions=actions,
                hits=hits,
            ),
            *cls._edgar_q_field(
                build=build,
                actions=actions,
                hits=hits,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
            ),
            *cls._shinku_effect_five(
                build=build,
                hits=hits,
            ),
            *cls._kuhara_effect_two(
                build=build,
                actions=actions,
                hits=hits,
            ),
            *cls._zankou_effect_three(
                build=build,
                state_buff_intervals=state_buff_intervals,
                battle_end_us=battle_end_us,
                time_stop_intervals=time_stop_intervals,
                recover_ratio=zankou_effect_three_recover_ratio,
            ),
        ]
        return tuple(sorted(
            results,
            key=lambda row: (row.relative_time_us, row.event_id),
        ))

    @staticmethod
    def _lacrimosa_effect_five(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        characters = tuple(
            row for row in _characters(build)
            if int(row.get("character_id") or 0) in _LACRIMOSA_IDS
            and _effect_enabled(row, "Effect5")
        )
        if not characters:
            return ()
        source_ids = frozenset(
            int(row.get("character_id") or 0) for row in characters
        )
        source_names = {
            int(row.get("character_id") or 0): str(
                row.get("observed_name") or "安魂曲"
            )
            for row in characters
        }
        nightmare_hits = tuple(
            row for row in hits if _nightmare_hit(row, source_ids)
        )
        maximum_active_us = _active_time(
            battle_end_us,
            time_stop_intervals,
        )
        results: list[BattleTreatmentEvent] = []
        for tick_active_us in range(
            _LACRIMOSA_PERIOD_US,
            maximum_active_us + 1,
            _LACRIMOSA_PERIOD_US,
        ):
            window_start_us = tick_active_us - _LACRIMOSA_PERIOD_US
            window_hits = tuple(
                row for row in nightmare_hits
                if window_start_us
                < _active_time(row.relative_time_us, time_stop_intervals)
                <= tick_active_us
            )
            damage_by_source: dict[int, float] = {}
            for hit in window_hits:
                source_id = int(hit.character_id or 0)
                damage_by_source[source_id] = (
                    damage_by_source.get(source_id, 0.0) + hit.damage
                )
            event_time_us = _raw_time(
                tick_active_us,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
            )
            if event_time_us >= battle_end_us:
                continue
            for source_id, damage in damage_by_source.items():
                if damage <= 0:
                    continue
                raw_heal = float(ceil(damage * _LACRIMOSA_RECOVER_RATIO))
                evidence_ids = tuple(
                    row.event_id for row in window_hits
                    if row.character_id == source_id
                )
                results.append(BattleTreatmentEvent(
                    event_id=(
                        f"treatment:lacrimosa:effect5:{source_id}:"
                        f"{tick_active_us}"
                    ),
                    relative_time_us=event_time_us,
                    source_character_id=source_id,
                    source_character_name=source_names[source_id],
                    treatment_kind="lacrimosa_effect5_period",
                    target_scope="self",
                    evidence_kind="formal_period_and_damage_window",
                    confidence="中",
                    evidence_event_ids=evidence_ids,
                    inference_basis=(
                        "安魂曲五觉每 3 个有效战斗秒汇总上一窗口的噩梦本体伤害，"
                        "并按总和的 1.5% 向上取整自疗；无正数噩梦伤害的窗口不"
                        "推断来源侧治疗广播。"
                    ),
                    target_character_ids=(source_id,),
                    raw_healing_amount=raw_heal,
                    is_periodic=True,
                    application_tick=tick_active_us // _LACRIMOSA_PERIOD_US,
                    amount_basis=(
                        f"ceil({damage:g} × {_LACRIMOSA_RECOVER_RATIO:g})"
                    ),
                ))
        return tuple(results)

    @staticmethod
    def _edgar_hold_e(
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        held_event_ids = {
            event_id
            for action in actions
            if action.character_id == _EDGAR_ID
            and action.input_kind == "E"
            and action.input_gesture == "hold"
            for event_id in action.evidence_event_ids
        }
        results: list[BattleTreatmentEvent] = []
        for hit in hits:
            if hit.event_id not in held_event_ids:
                continue
            results.append(BattleTreatmentEvent(
                event_id=f"treatment:edgar:e:{hit.event_id}",
                relative_time_us=hit.relative_time_us,
                source_character_id=_EDGAR_ID,
                source_character_name=hit.character_name or "埃德嘉",
                treatment_kind="edgar_hold_e_segment",
                target_scope="lowest_hp_team_member",
                evidence_kind="formal_damage_segment_binding",
                confidence="中",
                evidence_event_ids=(hit.event_id,),
                inference_basis=(
                    "埃德嘉长按 E 每个实际伤害段同时应用一次治疗；只按战报"
                    "真实存在的长按段生成，不补足理论七段。"
                ),
                amount_basis=(
                    "技能等级固定值 + 结算时受治疗目标最大生命 × 技能等级比例；"
                    "当前轴缺少受治疗目标与角色生命，数值保持未知。"
                ),
            ))
        return tuple(results)

    @staticmethod
    def _edgar_q_field(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleTreatmentEvent, ...]:
        hits_by_id = _hit_index(hits)
        results: list[BattleTreatmentEvent] = []
        ordered_actions = tuple(sorted(
            actions,
            key=lambda row: (row.start_us, row.action_id),
        ))
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _EDGAR_ID
        ), None)
        teammate_qte_enabled = bool(
            character is not None and _effect_enabled(character, "Effect1")
        )
        truth_keys = 0
        for action in ordered_actions:
            grants_own_key = (
                action.character_id == _EDGAR_ID
                and action.input_kind in {"E", "QTE"}
            )
            grants_teammate_key = (
                teammate_qte_enabled
                and action.character_id != _EDGAR_ID
                and action.input_kind == "QTE"
            )
            if grants_own_key or grants_teammate_key:
                truth_keys = min(3, truth_keys + 1)
                continue
            if action.character_id != _EDGAR_ID or action.input_kind != "Q":
                continue
            tick_count = _EDGAR_Q_BASE_TICKS + truth_keys
            truth_keys = 0
            evidence_hits = tuple(
                hits_by_id[event_id]
                for event_id in action.evidence_event_ids
                if event_id in hits_by_id
            )
            field_start_us = min(
                (row.relative_time_us for row in evidence_hits),
                default=action.start_us,
            )
            switch_us = min(
                (
                    row.start_us for row in ordered_actions
                    if row.start_us > field_start_us
                    and row.character_id != _EDGAR_ID
                ),
                default=battle_end_us,
            )
            start_active_us = _active_time(
                field_start_us,
                time_stop_intervals,
            )
            for ordinal in range(1, tick_count + 1):
                event_time_us = _raw_time(
                    start_active_us + ordinal * _EDGAR_Q_PERIOD_US,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= min(battle_end_us, switch_us):
                    break
                results.append(BattleTreatmentEvent(
                    event_id=f"treatment:edgar:q:{action.action_id}:{ordinal}",
                    relative_time_us=event_time_us,
                    source_character_id=_EDGAR_ID,
                    source_character_name=action.character_name or "埃德嘉",
                    source_action_id=action.action_id,
                    treatment_kind="edgar_q_field_period",
                    target_scope="active_character",
                    evidence_kind="formal_field_period",
                    confidence="中",
                    evidence_event_ids=action.evidence_event_ids,
                    inference_basis=(
                        "埃德嘉 Q 领域生成 1 个有效战斗秒后开始逐秒治疗。"
                        "真理之匙按本场已推断的埃德嘉 E/QTE 与一觉队友 QTE"
                        "动作累计至三把，Q 时消耗并把基础 10 跳逐把延长；"
                        "检测到其他角色动作时停止后续领域治疗。"
                    ),
                    is_periodic=True,
                    application_tick=ordinal,
                    amount_basis=(
                        "技能等级固定值 + 当前受治疗角色最大生命 × 技能等级比例；"
                        "当前轴缺少受治疗目标与角色生命，数值保持未知。"
                    ),
                ))
        return tuple(results)

    @staticmethod
    def _shinku_effect_five(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _SHINKU_ID
        ), None)
        if character is None or not _effect_enabled(character, "Effect5"):
            return ()
        rage_hits = tuple(
            row for row in hits
            if row.character_id == _SHINKU_ID
            and "shinku_skill2_rage_damage"
            in row.gameplay_effect_id.casefold()
        )
        grouped: dict[tuple[int, str, str], list[BattleAnalysisHit]] = {}
        for hit in rage_hits:
            key = (
                hit.relative_time_us,
                hit.ability_id.casefold(),
                hit.gameplay_effect_id.casefold(),
            )
            grouped.setdefault(key, []).append(hit)
        base_attack = _base_attack(character)
        raw_heal = (
            None
            if base_attack is None
            else base_attack * _SHINKU_EFFECT5_RECOVER_RATIO
        )
        source_name = str(character.get("observed_name") or "真红")
        return tuple(
            BattleTreatmentEvent(
                event_id=(
                    "treatment:shinku:effect5:"
                    f"{group[0].relative_time_us}"
                ),
                relative_time_us=group[0].relative_time_us,
                source_character_id=_SHINKU_ID,
                source_character_name=source_name,
                treatment_kind="shinku_effect5_rage_e",
                target_scope="self",
                evidence_kind="formal_rage_e_damage_binding",
                confidence="中",
                evidence_event_ids=tuple(row.event_id for row in group),
                inference_basis=(
                    "真红五觉仅在升腾之赤替换 E 的第二段正式伤害项实际出现时"
                    "生成一次自疗；同刻多目标 hit 不重复生成来源侧治疗。"
                ),
                target_character_ids=(_SHINKU_ID,),
                raw_healing_amount=raw_heal,
                amount_basis=(
                    "GetAtkBase × 300%"
                    if base_attack is None
                    else f"{base_attack:g} × 300%"
                ),
            )
            for group in grouped.values()
        )

    @staticmethod
    def _kuhara_effect_two(
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _KUHARA_ID
        ), None)
        if character is None or not _effect_enabled(character, "Effect2"):
            return ()
        settlements = tuple(
            row for row in hits
            if is_kuhara_q_settlement_hit(row, actions)
        )
        grouped: dict[tuple[int, str], list[BattleAnalysisHit]] = {}
        for hit in settlements:
            grouped.setdefault(
                (hit.relative_time_us, hit.target_id),
                [],
            ).append(hit)
        source_name = str(character.get("observed_name") or "九原")
        return tuple(
            BattleTreatmentEvent(
                event_id=(
                    "treatment:kuhara:effect2:"
                    f"{group[0].relative_time_us}:{group[0].target_id}"
                ),
                relative_time_us=group[0].relative_time_us,
                source_character_id=_KUHARA_ID,
                source_character_name=source_name,
                treatment_kind="kuhara_effect2_q_settlement",
                target_scope="team",
                evidence_kind="formal_q_settlement_hit",
                confidence="中",
                evidence_event_ids=tuple(row.event_id for row in group),
                inference_basis=(
                    "九原二觉只在 Q 主动清算的真实玫约结算时治疗全队；"
                    "长按 A 清算和自然到期不生成治疗。多目标按各自已观测"
                    "清算事件保留独立来源。"
                ),
                amount_basis=(
                    "该玫约目标累计总伤害 × 5%；当前轴未保存治疗执行器"
                    "读取的累计值，数值保持未知。"
                ),
            )
            for group in grouped.values()
        )

    @staticmethod
    def _zankou_effect_three(
        *,
        build: Mapping[str, Any] | None,
        state_buff_intervals: Sequence[BattleInferredBuffInterval],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
        recover_ratio: float | None,
    ) -> tuple[BattleTreatmentEvent, ...]:
        character = next((
            row for row in _characters(build)
            if int(row.get("character_id") or 0) == _ZANKOU_ID
        ), None)
        if (
            character is None
            or not _effect_enabled(character, "Effect3")
            or recover_ratio is None
            or recover_ratio <= 0
        ):
            return ()
        huo_intervals = tuple(
            row for row in state_buff_intervals
            if row.source_character_id == _ZANKOU_ID
            and row.source_effect_definition_id.endswith(":huo")
        )
        maximum_health = _max_health(character)
        raw_heal = (
            maximum_health * recover_ratio
            if maximum_health is not None
            else None
        )
        source_name = str(character.get("observed_name") or "残虹")
        results: list[BattleTreatmentEvent] = []
        for interval in huo_intervals:
            start_active_us = _active_time(
                interval.start_us,
                time_stop_intervals,
            )
            end_active_us = _active_time(
                interval.end_us,
                time_stop_intervals,
            )
            ordinal = 1
            while True:
                tick_active_us = (
                    start_active_us + ordinal * _ZANKOU_TREATMENT_PERIOD_US
                )
                if tick_active_us >= end_active_us:
                    break
                event_time_us = _raw_time(
                    tick_active_us,
                    battle_end_us=battle_end_us,
                    intervals=time_stop_intervals,
                )
                if event_time_us >= battle_end_us:
                    break
                results.append(BattleTreatmentEvent(
                    event_id=(
                        f"treatment:zankou:effect3:{interval.interval_id}:"
                        f"{ordinal}"
                    ),
                    relative_time_us=event_time_us,
                    source_character_id=_ZANKOU_ID,
                    source_character_name=source_name,
                    treatment_kind="zankou_effect3_huo_period",
                    target_scope="self",
                    evidence_kind="formal_huo_interval_and_period",
                    confidence="中",
                    evidence_event_ids=(interval.interval_id,),
                    inference_basis=(
                        "残虹三觉治疗由惑状态上的 1 秒正式周期执行；只在已重建"
                        "惑持有区间内按扣除时停的活动时钟生成。当前轴没有玩家"
                        "阵亡事件，一觉常驻惑因此投影至战斗结束。"
                    ),
                    target_character_ids=(_ZANKOU_ID,),
                    raw_healing_amount=raw_heal,
                    is_periodic=True,
                    application_tick=ordinal,
                    amount_basis=(
                        f"{maximum_health:g} × {recover_ratio:g}"
                        if maximum_health is not None
                        else f"残虹结算时最大生命 × {recover_ratio:g}"
                    ),
                ))
                ordinal += 1
        return tuple(results)
