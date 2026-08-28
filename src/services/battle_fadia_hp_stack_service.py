# 依据法帝娅黯星正式逐击推算全队生命上限汲取层数。
"""Fadia Dark Star HP-transfer intervals for deterministic hit replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


FADIA_HP_STACK_MODEL_VERSION = "fadia-dark-star-hp-stack-v4"

_FADIA_ID = 1039
_DARK_STAR_EFFECT = "buff_reaction_4_new"
_TRANSFER_RATIO = 0.10
_MAX_STACKS = 5
_AWAKEN_THREE_RATIO = 0.30
_OBSERVED_SOURCE_HP_MIN_RATIO = 0.25
_OBSERVED_SOURCE_HP_MAX_RATIO = 4.00


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


def _resolve_fadia_inherent_base_hp(
    build: Mapping[str, Any] | None,
) -> float | None:
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
    return inherent_hp


def resolve_fadia_inherent_hp(
    build: Mapping[str, Any] | None,
) -> float | None:
    """Resolve the configured inherent HP; runtime target loss is not this value."""

    inherent_hp = _resolve_fadia_inherent_base_hp(build)
    character = _fadia_character(build)
    if inherent_hp is None or character is None:
        return None
    if _awaken_three_enabled(character):
        inherent_hp *= 1.0 + _AWAKEN_THREE_RATIO
    return inherent_hp


def resolve_fadia_source_max_hp(
    build: Mapping[str, Any] | None,
) -> float | None:
    """Resolve Fadia's pre-combat current MAXHP from the frozen panel."""

    character = _fadia_character(build)
    if character is None:
        return None
    stats = tuple(character.get("stats") or ())
    panel_hp = next(
        (
            float(row.get("value") or 0.0)
            for row in stats
            if str(row.get("source_group") or "") == "resolved"
            and str(row.get("property_id") or "") == "PanelHP"
            and float(row.get("value") or 0.0) > 0.0
        ),
        0.0,
    )
    if panel_hp <= 0.0:
        panel_hp = next(
            (
                float(row.get("value") or 0.0)
                for row in stats
                if str(row.get("source_group") or "") == "resolved"
                and str(row.get("property_id") or "") == "HPMaxBase"
                and float(row.get("value") or 0.0) > 0.0
            ),
            0.0,
        )
    if panel_hp <= 0.0:
        panel_hp = _resolve_fadia_inherent_base_hp(build) or 0.0
    inherent_base = _resolve_fadia_inherent_base_hp(build) or 0.0
    if _awaken_three_enabled(character):
        panel_hp += inherent_base * _AWAKEN_THREE_RATIO
    return panel_hp if panel_hp > 0.0 else None


def is_plausible_fadia_observed_source_hp(
    observed_source_hp: float,
    reference_source_hp: float | None,
) -> bool:
    """Reject nearby HP transitions that cannot be this formal extraction."""

    if observed_source_hp <= 0.0:
        return False
    if reference_source_hp is None or reference_source_hp <= 0.0:
        return True
    ratio = observed_source_hp / reference_source_hp
    return _OBSERVED_SOURCE_HP_MIN_RATIO <= ratio <= _OBSERVED_SOURCE_HP_MAX_RATIO


def _observed_source_hp_by_dark_star(
    dark_stars: Sequence[BattleAnalysisHit],
    events: Sequence[BattleMaxHpReductionEvent],
) -> dict[str, float]:
    """Distribute one observed settlement over its formal Dark Star triggers."""

    by_id = {hit.event_id: hit for hit in dark_stars}
    result: dict[str, float] = {}
    for event in events:
        if (
            event.mechanic_kind != "fadia_dark_star_max_hp_transfer"
            or event.evidence_kind != "observed"
            or event.max_hp_reduction <= 0.0
        ):
            continue
        matched = tuple(sorted(
            (
                by_id[event_id]
                for event_id in event.evidence_event_ids
                if event_id in by_id
            ),
            key=lambda hit: (hit.relative_time_us, hit.sequence, hit.event_id),
        ))
        if not matched:
            continue
        # A delayed HP sample can retain several nearby candidates.  The
        # transition belongs to the latest eligible Dark Star before it; older
        # candidates keep their event-time fallback instead of sharing one
        # observed settlement.
        result[matched[-1].event_id] = event.max_hp_reduction / 2.0
    return result


class BattleFadiaHpStackService:
    """Infer persistent 10%-of-current-MAXHP gains after formal Dark Stars."""

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
        source_max_hp = resolve_fadia_source_max_hp(build)
        if source_max_hp is None or battle_end_us <= 0:
            return ()
        intervals: list[BattleInferredBuffInterval] = []
        dark_stars = tuple(
            hit
            for hit in hits
            if hit.direction == "outgoing"
            and hit.character_id == _FADIA_ID
            and hit.gameplay_effect_id.casefold() == _DARK_STAR_EFFECT
        )
        observed_source_hp = _observed_source_hp_by_dark_star(
            dark_stars,
            max_hp_events,
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
            current_source_hp = source_max_hp
            for hit in half_hits[:_MAX_STACKS]:
                ordinal += 1
                observed_hp = observed_source_hp.get(hit.event_id)
                if observed_hp is not None and is_plausible_fadia_observed_source_hp(
                    observed_hp,
                    current_source_hp,
                ):
                    current_source_hp = observed_hp
                else:
                    observed_hp = None
                stack_hp = current_source_hp * _TRANSFER_RATIO
                observed_basis = observed_hp is not None
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
                        "正式 HTExtractAttributeGEComp 以来源当时 MAXHP 的 10%"
                        "给全队加生命上限，最多 5 次；"
                        + (
                            f"按{half}半场独立累计并在换半时清空；"
                            if half else ""
                        )
                        + (
                            "本次目标最大生命正式下降值按 200% 反推来源当时 MAXHP。"
                            if observed_basis
                            else "缺少本次目标下降值，使用冻结 PanelHP、三觉与前序本机制层数递推。"
                        )
                    ),
                    trigger_event_type="FORMAL_DARK_STAR_SETTLED",
                    evidence_action_ids=(),
                    evidence_event_ids=(hit.event_id,),
                    modifiers=(BattleBuffModifierEvidence(
                        property_id="HPMaxAdd",
                        modifier_operation="EGameplayModOp::Additive",
                        magnitude_kind="derived:fadia_source_current_max_hp*0.10",
                        magnitude_value=stack_hp,
                        calculation_asset_path="",
                        value_confidence="高" if observed_basis else "中",
                    ),),
                    stacking_type="AggregateBySource",
                    stack_limit_count=_MAX_STACKS,
                ))
                current_source_hp += stack_hp
        return tuple(intervals)
