# 从正式目标血量样本派生最大生命下降事件，并独立推断机制归属。
"""Derive target max-HP settlements without rewriting formal hit evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_report import (
    BattleMaxHpReductionEvent,
    BattleTargetCondition,
    BattleTimelineDamageGroup,
)
from src.services.battle_fadia_hp_stack_service import resolve_fadia_inherent_hp
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)


TARGET_VITAL_MODEL_VERSION = "battle-target-vital-v4"

_LACRIMOSA_ID = 1004
_FADIA_ID = 1039
_LACRIMOSA_NIGHTMARE_EFFECTS = frozenset(
    {
        "ge_player_lacrimosa_blood_damage",
        "ge_player_lacrimosa_blood_damage_lv6",
    }
)
_FADIA_DARK_STAR_EFFECT = "buff_reaction_4_new"
_LACRIMOSA_MATCH_WINDOW_US = 4_000_000
_FADIA_MATCH_WINDOW_US = 5_000_000
_LACRIMOSA_REDUCTION_DAMAGE_RATIO = 2.0
_FADIA_REDUCTION_HP_RATIO = 2.0


def _text(value: Any, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_battle_target_identity(
    row: Mapping[str, Any],
) -> tuple[str, str]:
    """Return one canonical target key and display name for every projection."""

    target_name = _text(row.get("target_name"), "未知目标")
    target_id = _text(
        row.get("target_id"),
        _text(row.get("target_monster_id"), "unknown"),
    )
    return target_id, target_name


def battle_target_identity_mode(rows: Sequence[Mapping[str, Any]]) -> str:
    """Describe whether target samples are instance-scoped or need a fallback."""

    relevant = tuple(
        row
        for row in rows
        if _text(row.get("direction"), "unknown") == "outgoing"
        and _number(row.get("target_max_hp")) is not None
    )
    if not relevant:
        return "no_target_vital_samples"
    explicit = tuple(bool(_text(row.get("target_id"))) for row in relevant)
    if all(explicit):
        return "instance_scoped"
    if any(explicit):
        return "mixed_guarded"
    return "single_target_assumed"


def bind_confirmed_single_target(
    rows: Sequence[Mapping[str, Any]],
    condition: BattleTargetCondition | None,
) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    """Bind missing outgoing identities only under one explicit user target."""

    if condition is None:
        return tuple(rows), False
    selected = tuple(dict.fromkeys(condition.selected_target_ids))
    if (
        len(selected) != 1
        or not condition.primary_target_id
        or selected[0] != condition.primary_target_id
    ):
        return tuple(rows), False
    outgoing = tuple(
        row for row in rows
        if _text(row.get("direction"), "unknown") == "outgoing"
    )
    if not outgoing or any(_text(row.get("target_id")) for row in outgoing):
        return tuple(rows), False
    bound = []
    for row in rows:
        if _text(row.get("direction"), "unknown") != "outgoing":
            bound.append(row)
            continue
        copy = dict(row)
        copy["target_id"] = condition.primary_target_id
        copy["target_name"] = condition.target_name
        bound.append(copy)
    return tuple(bound), True


def _character_id(row: Mapping[str, Any]) -> int | None:
    value = row.get("character_id")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sequence(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("sequence_order") or row.get("sequence_text") or 0)
    except (TypeError, ValueError):
        return 0


def _event_id(row: Mapping[str, Any]) -> str:
    return f"{_sequence(row)}:primary"


def _target_scope(
    row: Mapping[str, Any],
    target_id: str,
) -> tuple[str, str]:
    return _text(row.get("abyss_half")).casefold(), target_id


def _lacrimosa_awaken_five_enabled(build: Mapping[str, Any] | None) -> bool:
    for character in (build or {}).get("characters") or ():
        try:
            character_id = int(character.get("character_id"))
        except (TypeError, ValueError):
            continue
        if character_id != _LACRIMOSA_ID:
            continue
        profile = character.get("profile")
        profile = profile if isinstance(profile, Mapping) else {}
        if bool(profile.get("awakening_selection_initialized")):
            selected = {
                str(value)
                for value in profile.get("selected_awaken_effect_ids") or ()
            }
            return "Effect5" in selected
        try:
            awakening_level = int(
                profile.get("awakening_level")
                or character.get("awakening_level")
                or 0
            )
        except (TypeError, ValueError):
            awakening_level = 0
        return awakening_level >= 5
    return False


@dataclass(slots=True)
class _TargetState:
    confirmed_max_hp: float | None = None
    last_observed_hp: float | None = None
    settlement_frontier_hp: float | None = None


def _remember_hp_sample(
    state: _TargetState,
    *,
    hp_before: float | None,
    hp_after: float | None,
) -> None:
    observed_hp = hp_after if hp_after is not None else hp_before
    if observed_hp is None:
        return
    state.settlement_frontier_hp = (
        observed_hp
        if state.settlement_frontier_hp is None
        else min(state.settlement_frontier_hp, observed_hp)
    )


def _settlement_frontier_hp(
    state: _TargetState,
    *,
    fallback_hp: float | None,
) -> float:
    if state.settlement_frontier_hp is not None:
        return state.settlement_frontier_hp
    return max(0.0, float(fallback_hp or 0.0))


class BattleTargetVitalAnalysisService:
    """Recognize observed max-HP drops and attribute only known player mechanisms."""

    @staticmethod
    def derive(
        *,
        rows: Sequence[Mapping[str, Any]],
        build: Mapping[str, Any] | None,
    ) -> tuple[BattleMaxHpReductionEvent, ...]:
        lacrimosa_enabled = _lacrimosa_awaken_five_enabled(build)
        fadia_enabled = BattleCharacterPassiveService.is_unlocked(
            build,
            _FADIA_ID,
            2,
        )
        identity_mode = battle_target_identity_mode(rows)
        states: dict[tuple[str, str], _TargetState] = defaultdict(_TargetState)
        pending_lacrimosa: dict[
            tuple[str, str], list[Mapping[str, Any]]
        ] = defaultdict(list)
        pending_fadia: dict[
            tuple[str, str], list[Mapping[str, Any]]
        ] = defaultdict(list)
        events: list[BattleMaxHpReductionEvent] = []

        ordered = sorted(
            rows,
            key=lambda row: (int(row.get("relative_time_us") or 0), _sequence(row)),
        )
        for row in ordered:
            if _text(row.get("direction"), "unknown") != "outgoing":
                continue
            time_us = int(row.get("relative_time_us") or 0)
            if identity_mode == "mixed_guarded" and not _text(
                row.get("target_id")
            ):
                continue
            target_id, target_name = resolve_battle_target_identity(row)
            target_scope = _target_scope(row, target_id)
            effect = _text(row.get("gameplay_effect_name")).casefold()
            character_id = _character_id(row)
            if (
                character_id == _LACRIMOSA_ID
                and effect in _LACRIMOSA_NIGHTMARE_EFFECTS
            ):
                pending_lacrimosa[target_scope].append(row)
            if (
                fadia_enabled
                and character_id == _FADIA_ID
                and effect == _FADIA_DARK_STAR_EFFECT
                and bool(_text(row.get("target_id")))
            ):
                pending_fadia[target_scope].append(row)

            state = states[target_scope]
            observed_max = _number(row.get("target_max_hp"))
            hp_before = _number(row.get("target_hp_before"))
            hp_after = _number(row.get("target_hp_after"))
            if observed_max is None or observed_max <= 0:
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                continue
            if state.confirmed_max_hp is None:
                state.confirmed_max_hp = observed_max
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                _remember_hp_sample(
                    state,
                    hp_before=hp_before,
                    hp_after=hp_after,
                )
                continue
            if observed_max > state.confirmed_max_hp:
                continue
            if observed_max == state.confirmed_max_hp:
                state.last_observed_hp = hp_after if hp_after is not None else hp_before
                _remember_hp_sample(
                    state,
                    hp_before=hp_before,
                    hp_after=hp_after,
                )
                continue

            old_max_hp = state.confirmed_max_hp
            max_hp_reduction = old_max_hp - observed_max
            fadia_candidates = tuple(
                candidate
                for candidate in pending_fadia[target_scope]
                if 0 <= time_us - int(candidate.get("relative_time_us") or 0)
                <= _FADIA_MATCH_WINDOW_US
            )
            lacrimosa_candidates = tuple(
                candidate
                for candidate in pending_lacrimosa[target_scope]
                if 0 <= time_us - int(candidate.get("relative_time_us") or 0)
                <= _LACRIMOSA_MATCH_WINDOW_US
            )
            if fadia_candidates:
                source_rows = fadia_candidates
                source_character_id = _FADIA_ID
                source_character_name = _text(
                    fadia_candidates[-1].get("character_name"),
                    "法帝娅",
                )
                mechanic_kind = "fadia_dark_star_max_hp_transfer"
                mechanic_name = "法帝娅被动·黯星生命上限汲取"
                source_skill_name = "罪感熔炉"
                attribution_confidence = "中"
                basis = (
                    "最大生命样本下降前 5 秒内存在法帝娅 Buff_Reaction_4_new；"
                    "以观测差值结算，不使用静态技能参数反推数值。"
                )
            elif lacrimosa_enabled and lacrimosa_candidates:
                source_rows = lacrimosa_candidates
                source_character_id = _LACRIMOSA_ID
                source_character_name = _text(
                    lacrimosa_candidates[-1].get("character_name"),
                    "安魂曲",
                )
                mechanic_kind = "lacrimosa_nightmare_awaken_5"
                mechanic_name = "安魂曲五觉·噩梦生命上限削减"
                source_skill_name = "噩梦"
                attribution_confidence = "中"
                basis = (
                    "冻结配装已激活 Effect5，且最大生命样本下降前 4 秒内存在噩梦伤害；"
                    "以观测差值结算，不按描述倍率重复计伤。"
                )
            else:
                source_rows = ()
                source_character_id = None
                source_character_name = "未归因"
                mechanic_kind = "unattributed_max_hp_reduction"
                mechanic_name = "未归因最大生命下降"
                source_skill_name = ""
                attribution_confidence = "低"
                basis = "确认最大生命样本下降，但冻结配装与附近事件不足以归属于已建模机制。"

            settlement_hp = _settlement_frontier_hp(
                state,
                fallback_hp=(
                    state.last_observed_hp
                    if state.last_observed_hp is not None
                    else hp_before
                ),
            )
            hp_ratio = min(1.0, max(0.0, settlement_hp / old_max_hp))
            effective_hp_loss = max_hp_reduction * hp_ratio
            basis += (
                f" 结算前生命取同一旧 HPMax 附近逐击的最小可靠 HPAfter "
                f"{settlement_hp:g}；下降后首行正式伤害不重复扣除此结算。"
            )

            evidence_ids = tuple(
                dict.fromkeys(
                    (*(_event_id(candidate) for candidate in source_rows), _event_id(row))
                )
            )
            events.append(
                BattleMaxHpReductionEvent(
                    event_id=f"max-hp:{target_id}:{_sequence(row)}",
                    target_id=target_id,
                    target_name=target_name,
                    observed_at_us=time_us,
                    old_max_hp=old_max_hp,
                    new_max_hp=observed_max,
                    max_hp_reduction=max_hp_reduction,
                    hp_before_settlement=settlement_hp,
                    hp_ratio_before=hp_ratio,
                    effective_hp_loss=effective_hp_loss,
                    source_character_id=source_character_id,
                    source_character_name=source_character_name,
                    mechanic_kind=mechanic_kind,
                    mechanic_name=mechanic_name,
                    source_skill_name=source_skill_name,
                    evidence_event_ids=evidence_ids,
                    attribution_confidence=attribution_confidence,
                    calculation_confidence="中" if hp_before is not None else "低",
                    inference_basis=basis,
                    scope_half=target_scope[0],
                )
            )
            state.confirmed_max_hp = observed_max
            state.last_observed_hp = hp_after if hp_after is not None else hp_before
            state.settlement_frontier_hp = None
            _remember_hp_sample(
                state,
                hp_before=hp_before,
                hp_after=hp_after,
            )
            pending_lacrimosa[target_scope].clear()
            pending_fadia[target_scope] = [
                candidate
                for candidate in pending_fadia[target_scope]
                if candidate not in fadia_candidates
                and time_us - int(candidate.get("relative_time_us") or 0)
                <= _FADIA_MATCH_WINDOW_US
            ]

        return tuple(events)

    @staticmethod
    def estimate_from_descriptions(
        *,
        rows: Sequence[Mapping[str, Any]],
        build: Mapping[str, Any] | None,
        observed_events: Sequence[BattleMaxHpReductionEvent],
    ) -> tuple[BattleMaxHpReductionEvent, ...]:
        """Estimate uncovered triggers without adding them to formal effective damage."""

        identity_mode = battle_target_identity_mode(rows)
        lacrimosa_enabled = _lacrimosa_awaken_five_enabled(build)
        fadia_enabled = BattleCharacterPassiveService.is_unlocked(
            build,
            _FADIA_ID,
            2,
        )
        fadia_hp = resolve_fadia_inherent_hp(build) if fadia_enabled else None
        observed_evidence_ids = {
            event_id
            for event in observed_events
            for event_id in event.evidence_event_ids
        }
        estimates: list[BattleMaxHpReductionEvent] = []
        for row in sorted(
            rows,
            key=lambda item: (
                int(item.get("relative_time_us") or 0),
                _sequence(item),
            ),
        ):
            if _text(row.get("direction"), "unknown") != "outgoing":
                continue
            if identity_mode == "mixed_guarded" and not _text(
                row.get("target_id")
            ):
                continue
            event_id = _event_id(row)
            effect = _text(row.get("gameplay_effect_name")).casefold()
            character_id = _character_id(row)
            is_fadia = (
                fadia_enabled
                and character_id == _FADIA_ID
                and effect == _FADIA_DARK_STAR_EFFECT
                and bool(_text(row.get("target_id")))
            )
            if event_id in observed_evidence_ids:
                continue
            hp_before = _number(row.get("target_hp_before"))
            max_hp = _number(row.get("target_max_hp"))
            if hp_before is None or max_hp is None or max_hp <= 0:
                continue

            damage = max(0.0, float(row.get("damage") or 0.0))
            if (
                lacrimosa_enabled
                and character_id == _LACRIMOSA_ID
                and effect in _LACRIMOSA_NIGHTMARE_EFFECTS
                and damage > 0
            ):
                estimated_reduction = damage * _LACRIMOSA_REDUCTION_DAMAGE_RATIO
                source_character_name = _text(
                    row.get("character_name"),
                    "安魂曲",
                )
                mechanic_kind = "lacrimosa_nightmare_awaken_5_estimated"
                mechanic_name = "安魂曲五觉·噩梦生命上限削减（描述预计）"
                source_skill_name = "噩梦"
                basis = (
                    "冻结配装已激活 Effect5；按技能描述以本次噩梦伤害的 200% 预计最大生命削减。"
                    "未验证目标是否实际生效、免疫或已被其他状态覆盖。"
                )
            elif (
                is_fadia
                and fadia_hp is not None
            ):
                estimated_reduction = fadia_hp * _FADIA_REDUCTION_HP_RATIO
                source_character_name = _text(
                    row.get("character_name"),
                    "法帝娅",
                )
                mechanic_kind = "fadia_dark_star_max_hp_transfer_estimated"
                mechanic_name = "法帝娅被动·黯星生命上限汲取（描述预计）"
                source_skill_name = "罪感熔炉"
                basis = (
                    "按技能描述以冻结法帝娅固有生命上限的 200% 预计目标最大生命损失；"
                    "敌方损失不受我方 5 次生命获取上限限制。"
                    "固有生命只采用人物与弧盘基础生命，并在已选择三觉时整体乘 1.30。"
                )
            else:
                continue

            if identity_mode == "single_target_assumed":
                basis += (
                    " 当前行没有目标实例 ID，本预计只使用该行生命样本，"
                    "不把前后血量变化当作同一敌人的事实。"
                )

            target_id, target_name = resolve_battle_target_identity(row)
            estimated_reduction = min(max_hp, max(0.0, estimated_reduction))
            hp_ratio = min(1.0, max(0.0, hp_before / max_hp))
            estimates.append(
                BattleMaxHpReductionEvent(
                    event_id=f"max-hp-estimate:{target_id}:{_sequence(row)}",
                    target_id=target_id,
                    target_name=target_name,
                    observed_at_us=int(row.get("relative_time_us") or 0),
                    old_max_hp=max_hp,
                    new_max_hp=max(0.0, max_hp - estimated_reduction),
                    max_hp_reduction=estimated_reduction,
                    hp_before_settlement=max(0.0, hp_before),
                    hp_ratio_before=hp_ratio,
                    effective_hp_loss=estimated_reduction * hp_ratio,
                    source_character_id=character_id,
                    source_character_name=source_character_name,
                    mechanic_kind=mechanic_kind,
                    mechanic_name=mechanic_name,
                    source_skill_name=source_skill_name,
                    evidence_event_ids=(event_id,),
                    attribution_confidence="中",
                    calculation_confidence="低",
                    inference_basis=basis,
                    evidence_kind="description_estimated",
                    included_in_effective_damage=False,
                    scope_half=_target_scope(row, target_id)[0],
                )
            )
        return tuple(estimates)

    @staticmethod
    def timeline_groups(
        events: Sequence[BattleMaxHpReductionEvent],
    ) -> tuple[BattleTimelineDamageGroup, ...]:
        """Project derived settlements as clickable public timeline bars."""

        return tuple(
            BattleTimelineDamageGroup(
                group_id=f"vital:{event.event_id}",
                character_id=event.source_character_id,
                character_name=event.source_character_name,
                direction="outgoing",
                channel_key=(
                    "max_hp_reduction"
                    if event.included_in_effective_damage
                    else "max_hp_reduction_estimated"
                ),
                channel_label=(
                    "生命上限结算"
                    if event.included_in_effective_damage
                    else "生命上限估算"
                ),
                damage_name=event.mechanic_name,
                source_skill_name=event.source_skill_name,
                ability_id=event.mechanic_kind,
                start_us=event.observed_at_us,
                end_us=event.observed_at_us + 1,
                hits=1,
                damage=event.effective_hp_loss,
                evidence_event_ids=(),
                detail_lines=(
                    f"最大生命 {event.old_max_hp:,.0f} → {event.new_max_hp:,.0f}",
                    f"结算前生命比例 {event.hp_ratio_before * 100:.2f}%",
                    f"归因置信度 {event.attribution_confidence} / 结算置信度 {event.calculation_confidence}",
                    event.inference_basis,
                ),
            )
            for event in events
        )
