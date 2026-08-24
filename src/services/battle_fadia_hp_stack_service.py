# 依据法帝娅黯星正式逐击推算全队生命上限汲取层数。
"""Fadia Dark Star HP-transfer intervals for deterministic hit replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import median
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleMaxHpReductionEvent,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)


FADIA_HP_STACK_MODEL_VERSION = "fadia-dark-star-hp-stack-v3"

_FADIA_ID = 1039
_DARK_STAR_EFFECT = "buff_reaction_4_new"
_TRANSFER_RATIO = 0.10
_MAX_STACKS = 5
_AWAKEN_THREE_RATIO = 0.30


def _fadia_character(
    build: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    for character in (build or {}).get("characters") or ():
        try:
            character_id = int(character.get("character_id"))
        except (TypeError, ValueError):
            continue
        if character_id == _FADIA_ID:
            return character
    return None


def _awaken_three_enabled(character: Mapping[str, Any]) -> bool:
    profile = character.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        selected = {
            str(value)
            for value in profile.get("selected_awaken_effect_ids") or ()
        }
        return "Effect3" in selected
    try:
        awakening_level = int(
            profile.get("awakening_level")
            or character.get("awakening_level")
            or 0
        )
    except (TypeError, ValueError):
        awakening_level = 0
    return awakening_level >= 3


def resolve_fadia_inherent_hp(
    build: Mapping[str, Any] | None,
    *,
    observed_events: Sequence[BattleMaxHpReductionEvent] = (),
) -> float | None:
    """Resolve Fadia's inherent HP, preferring formal target-transfer evidence."""

    observed_bases = tuple(
        event.max_hp_reduction / 2.0
        for event in observed_events
        if event.mechanic_kind == "fadia_dark_star_max_hp_transfer"
        and event.evidence_kind == "observed"
        and event.max_hp_reduction > 0.0
    )
    if observed_bases:
        return float(median(observed_bases))

    character = _fadia_character(build)
    if character is None:
        return None
    stats = tuple(character.get("stats") or ())
    inherent_hp = sum(
        float(row.get("value") or 0.0)
        for row in stats
        if str(row.get("source_group") or "") in {"character", "fork"}
        and str(row.get("property_id") or "") == "HPMaxBase"
    )
    if inherent_hp <= 0.0:
        inherent_hp = next(
            (
                float(row.get("value") or 0.0)
                for row in stats
                if str(row.get("source_group") or "") == "resolved"
                and str(row.get("property_id") or "") == "HPMaxBase"
            ),
            0.0,
        )
    if inherent_hp <= 0.0:
        return None
    if _awaken_three_enabled(character):
        inherent_hp *= 1.0 + _AWAKEN_THREE_RATIO
    return inherent_hp


class BattleFadiaHpStackService:
    """Infer persistent 10%-of-inherent-HP gains after formal Dark Star hits."""

    @staticmethod
    def infer(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        max_hp_events: Sequence[BattleMaxHpReductionEvent] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        if not BattleCharacterPassiveService.is_unlocked(build, _FADIA_ID, 2):
            return ()
        inherent_hp = resolve_fadia_inherent_hp(
            build,
            observed_events=max_hp_events,
        )
        if inherent_hp is None or battle_end_us <= 0:
            return ()
        observed_basis = any(
            event.mechanic_kind == "fadia_dark_star_max_hp_transfer"
            and event.evidence_kind == "observed"
            and event.max_hp_reduction > 0.0
            for event in max_hp_events
        )
        stack_hp = inherent_hp * _TRANSFER_RATIO
        intervals: list[BattleInferredBuffInterval] = []
        configured_hp = resolve_fadia_inherent_hp(build)
        if (
            observed_basis
            and configured_hp is not None
            and abs(inherent_hp - configured_hp) > 0.5
        ):
            evidence_ids = tuple(dict.fromkeys(
                event_id
                for event in max_hp_events
                if event.mechanic_kind == "fadia_dark_star_max_hp_transfer"
                for event_id in event.evidence_event_ids
            ))
            intervals.append(BattleInferredBuffInterval(
                interval_id="fadia:inherent-hp:observed-calibration",
                buff_asset_path="observed:character:1039:inherent-hp",
                buff_name="法帝娅固有生命（战报实测补正）",
                source_effect_definition_id=(
                    "character_passive:1039:GA_Fadia_Passive_1"
                ),
                source_kind="observed_target_max_hp_calibration",
                source_character_id=_FADIA_ID,
                source_character_name="法帝娅",
                target_scope="self",
                start_us=0,
                end_us=battle_end_us,
                stacks=1,
                duration_policy="battle_observed_calibration",
                state_confidence="高",
                value_confidence="高",
                inference_basis=(
                    "本场归因明确的目标最大生命下降按 200% 反推法帝娅固有生命；"
                    "冻结配置与实测不一致，因此只在本场重放中补正差额。"
                ),
                trigger_event_type="OBSERVED_FADIA_INHERENT_HP_CALIBRATION",
                evidence_action_ids=(),
                evidence_event_ids=evidence_ids,
                modifiers=(BattleBuffModifierEvidence(
                    property_id="HPMaxAdd",
                    modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="observed:target_max_hp_reduction/2",
                    magnitude_value=inherent_hp - configured_hp,
                    calculation_asset_path="",
                    value_confidence="高",
                ),),
                stacking_type="AggregateBySource",
                stack_limit_count=1,
            ))
        dark_stars = tuple(
            hit
            for hit in hits
            if hit.direction == "outgoing"
            and hit.character_id == _FADIA_ID
            and hit.gameplay_effect_id.casefold() == _DARK_STAR_EFFECT
        )
        half_starts: dict[str, int] = {}
        for hit in hits:
            half = hit.scope_half.casefold()
            if half in {"upper", "lower"}:
                half_starts[half] = min(
                    half_starts.get(half, hit.relative_time_us),
                    hit.relative_time_us,
                )
        ordered_halves = sorted(half_starts, key=half_starts.__getitem__)
        half_ends = {
            half: (
                half_starts[ordered_halves[index + 1]]
                if index + 1 < len(ordered_halves)
                else battle_end_us
            )
            for index, half in enumerate(ordered_halves)
        }
        grouped: dict[str, list[BattleAnalysisHit]] = {}
        for hit in dark_stars:
            half = hit.scope_half.casefold()
            if half not in half_ends:
                if len(ordered_halves) > 1:
                    continue
                half = ordered_halves[0] if ordered_halves else ""
            grouped.setdefault(half, []).append(hit)
        ordinal = 0
        for half, half_hits in grouped.items():
            for hit in half_hits[:_MAX_STACKS]:
                ordinal += 1
                end_us = half_ends.get(half, battle_end_us)
                start_us = hit.relative_time_us + 1
                if start_us >= end_us:
                    continue
                intervals.append(BattleInferredBuffInterval(
                    interval_id=f"fadia:dark-star-hp:{ordinal}:{hit.event_id}",
                    buff_asset_path="confirmed:character_passive:1039:dark-star-hp",
                    buff_name="法帝娅被动：生命上限汲取",
                    source_effect_definition_id=(
                        "character_passive:1039:GA_Fadia_Passive_1"
                    ),
                    source_kind="confirmed_character_text_and_formal_hit",
                    source_character_id=_FADIA_ID,
                    source_character_name="法帝娅",
                    target_scope="team",
                    start_us=start_us,
                    end_us=end_us,
                    stacks=1,
                    duration_policy="until_battle_end",
                    state_confidence="高",
                    value_confidence="高" if observed_basis else "中",
                    inference_basis=(
                        "正式 Buff_Reaction_4_new 逐击表明黯星已结算；"
                        "技能说明确认全队各获得法帝娅固有生命上限的 10%、"
                        "最多 5 次；"
                        + (
                            f"按{half}半场独立累计并在换半时清空；"
                            if half else ""
                        )
                        + (
                            "本场目标最大生命正式下降值按 200% 反推固有生命。"
                            if observed_basis
                            else "固有生命由人物与弧盘基础生命及已选择三觉计算。"
                        )
                    ),
                    trigger_event_type="FORMAL_DARK_STAR_SETTLED",
                    evidence_action_ids=(),
                    evidence_event_ids=(hit.event_id,),
                    modifiers=(BattleBuffModifierEvidence(
                        property_id="HPMaxAdd",
                        modifier_operation="EGameplayModOp::Additive",
                        magnitude_kind="derived:fadia_inherent_hp*0.10",
                        magnitude_value=stack_hp,
                        calculation_asset_path="",
                        value_confidence="高" if observed_basis else "中",
                    ),),
                    stacking_type="AggregateBySource",
                    stack_limit_count=_MAX_STACKS,
                ))
        return tuple(intervals)
