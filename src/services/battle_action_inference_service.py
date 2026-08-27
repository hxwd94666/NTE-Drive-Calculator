# 根据正式逐击生成可删除重算的角色动作窗口，不冒充实测输入。
"""Pure, versioned inference of character action windows from damage evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import replace
import re
from statistics import median
from typing import Literal

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction


ACTION_INFERENCE_MODEL_VERSION = "battle-action-window-v12"

_Q_TIME_STOP_FOLLOW_TOLERANCE_US = 350_000

_SECONDARY_EFFECT_MARKERS = (
    "buff_",
    "reaction",
    "_dot",
    "blood_damage",
    "tenacity",
)

_GAP_LIMITS_US = {
    "A": 900_000,
    "E": 1_250_000,
    "Q": 1_600_000,
    "QTE": 1_250_000,
    "G": 900_000,
}

_RAW_DURATION_MAX_US = 12_000_000
_RAW_DURATION_MAX_TAIL_US = 2_500_000
_EQUIVALENT_BOUNDARY_TOLERANCE_US = 2_000
_STATIC_HIT_ALIGNMENT_TOLERANCE_US = 120_000


@dataclass(frozen=True, slots=True)
class BattleActionAnimationCandidate:
    """Immutable GA/GE/Montage evidence prepared outside pure inference."""

    ability_id: str
    selector_key: str
    montage_asset_path: str
    effect_hit_offsets_us: tuple[tuple[str, tuple[int, ...]], ...]
    trigger_end_offsets_us: tuple[int, ...]
    end_event_offsets_us: tuple[int, ...]
    section_end_offsets_us: tuple[int, ...]
    duration_us: int
    hold_damage_mode: Literal["none", "during_hold", "after_hold"] = "none"
    hold_prelude_us: int = 0


@dataclass(frozen=True, slots=True)
class _ResolvedAnimationWindow:
    candidate: BattleActionAnimationCandidate
    start_us: int
    end_us: int
    matched_effects: int
    end_source: str


def _action_kind(hit: BattleAnalysisHit) -> str:
    ability = hit.ability_id.casefold()
    attack = hit.attack_type.casefold()
    effect = hit.gameplay_effect_id.casefold()
    if "ai_qte" in ability or "ai_qte" in effect:
        return ""
    if "qte" in ability or attack.startswith("环合·"):
        return "QTE"
    if (
        "fadia_ultraskillmelee" in effect
        or ability.endswith("ga_fadia_ultraskill_melee")
    ):
        return "A"
    if (
        "switchskill" in ability
        or "switchmod" in effect
        or (ability.startswith("ga_") and ability.endswith("_appear"))
    ):
        return "G"
    if "ultraskill" in ability or "q技能" in attack or attack == "ultra":
        return "Q"
    if ability.endswith("_skill") or "e技能" in attack or attack == "skill":
        return "E"
    if "extremevade" in ability or "perfectevade" in effect or "闪避反击" in attack:
        return "A"
    if "parry" in effect or "格挡反击" in attack:
        return "A"
    if "melee" in ability or "普攻" in attack or attack in {"normal", "melee"}:
        return "A"
    return ""


def _is_action_evidence(hit: BattleAnalysisHit) -> bool:
    if (
        hit.direction != "outgoing"
        or hit.is_follow_up
        or hit.character_id is None
    ):
        return False
    effect = hit.gameplay_effect_id.casefold()
    if any(marker in effect for marker in _SECONDARY_EFFECT_MARKERS):
        return False
    if hit.classification == "weave" and _action_kind(hit) != "QTE":
        return False
    if hit.classification == "reaction" and _action_kind(hit) != "QTE":
        return False
    return bool(_action_kind(hit))


def _phase_token(hit: BattleAnalysisHit, kind: str) -> str:
    effect = hit.gameplay_effect_id
    patterns = {
        "Q": r"UltraSkill(?:Mod[A-Z])?(\d+)",
        "E": r"(?<!Ultra)Skill(?:Mod[A-Z])?(\d+)",
        "A": r"(?:Magic)?Melee(\d+)",
    }
    pattern = patterns.get(kind)
    if pattern:
        match = re.search(pattern, effect, flags=re.IGNORECASE)
        if match:
            return f"{kind}{match.group(1)}"
    if kind == "A":
        branch = re.search(r"Branch(\d+)", effect, flags=re.IGNORECASE)
        if branch:
            return f"A分支{branch.group(1)}"
    return kind


def _input_sequence(hits: Sequence[BattleAnalysisHit], kind: str) -> str:
    tokens: list[str] = []
    for hit in hits:
        token = _phase_token(hit, kind)
        if not tokens or tokens[-1] != token:
            tokens.append(token)
    if len(tokens) > 10:
        hidden = len(tokens) - 9
        tokens = [*tokens[:9], f"…+{hidden}"]
    return " ".join(tokens) or kind


def _phase_ordinal(hit: BattleAnalysisHit, kind: str) -> int | None:
    token = _phase_token(hit, kind)
    match = re.fullmatch(rf"{re.escape(kind)}(\d+)", token)
    return int(match.group(1)) if match else None


def _action_key(hit: BattleAnalysisHit) -> tuple[int, str, str]:
    kind = _action_kind(hit)
    stable_source = "A" if kind == "A" else hit.ability_id or kind
    ability = hit.ability_id.casefold()
    effect = hit.gameplay_effect_id.casefold()
    if kind == "QTE" and "_qte" in ability:
        stable_source = f"{hit.ability_id[:ability.index('_qte')]}_QTE"
    elif kind != "A" and ability == "ga_lacrimosa_skill" and "steal" in effect:
        stable_source = f"{hit.ability_id}|{hit.gameplay_effect_id}"
    elif kind != "A" and ability in {
        "ga_lacrimosa_melee",
        "ga_lacrimosa_ultraskill",
    }:
        mode = "mod_b" if "modb" in effect or "lacrimosa_b_" in effect else "mod_a"
        stable_source = f"{hit.ability_id}|{mode}"
    return int(hit.character_id), stable_source, kind


def _build_action(
    ordinal: int,
    key: tuple[int, str, str],
    hits: Sequence[BattleAnalysisHit],
) -> BattleInferredAction:
    first = hits[0]
    last = hits[-1]
    kind = key[2]
    effects = tuple(
        dict.fromkeys(
            hit.gameplay_effect_id
            for hit in hits
            if hit.gameplay_effect_id
        )
    )
    identity_confidence = (
        "中"
        if first.ability_id.startswith("GA_") and "未识别" not in first.skill_name
        else "低"
    )
    godslayer = any(
        "fadia_ultraskillmelee" in effect.casefold()
        for effect in effects
    )
    action_name = "敌神者" if godslayer else (
        first.damage_name
        if "ga_lacrimosa_skill|" in key[1].casefold()
        and first.damage_name not in {"", "未识别伤害"}
        else first.skill_name
    )
    return BattleInferredAction(
        action_id=f"action:{first.character_id}:{first.sequence}:{ordinal}",
        character_id=int(first.character_id),
        character_name=first.character_name,
        action_name=action_name,
        input_kind=kind,
        input_sequence=_input_sequence(hits, kind),
        start_us=first.relative_time_us,
        end_us=max(first.relative_time_us + 1, last.relative_time_us + 1),
        hits=len(hits),
        damage=sum(hit.damage for hit in hits),
        identity_confidence=identity_confidence,
        timing_confidence="低",
        inference_basis=(
            "同角色同输入类别与稳定技能来源的连续出伤窗口；同刻普攻和反击"
            "归为一次 A 操作。起止点是首末伤害证据，"
            "不代表精确按键、前摇或后摇。"
        ),
        evidence_event_ids=tuple(hit.event_id for hit in hits),
        gameplay_effect_ids=effects,
    )


def _candidate_effect_offsets(
    candidate: BattleActionAnimationCandidate,
) -> dict[str, tuple[int, ...]]:
    return {
        effect_id.casefold(): tuple(sorted(set(int(value) for value in offsets)))
        for effect_id, offsets in candidate.effect_hit_offsets_us
        if effect_id and offsets
    }


def _aligned_first_offset_us(
    hits: Sequence[BattleAnalysisHit],
    offsets_by_effect: dict[str, tuple[int, ...]],
) -> int | None:
    """Find one Notify anchor that covers every observed hit in this release."""

    if not hits:
        return None
    first = hits[0]
    first_offsets = offsets_by_effect.get(first.gameplay_effect_id.casefold(), ())
    for first_offset_us in first_offsets:
        aligned = True
        for hit in hits[1:]:
            expected_offset_us = (
                first_offset_us + hit.relative_time_us - first.relative_time_us
            )
            if not any(
                abs(offset_us - expected_offset_us)
                <= _STATIC_HIT_ALIGNMENT_TOLERANCE_US
                for offset_us in offsets_by_effect.get(
                    hit.gameplay_effect_id.casefold(),
                    (),
                )
            ):
                aligned = False
                break
        if aligned:
            return first_offset_us
    return None


def _static_sequence_decision(
    hits: Sequence[BattleAnalysisHit],
    candidates: Sequence[BattleActionAnimationCandidate],
) -> bool | None:
    """Return whether exact static evidence can cover the hits in one release."""

    if not hits:
        return None
    ability_id = hits[0].ability_id.casefold()
    relevant: list[tuple[BattleActionAnimationCandidate, dict[str, tuple[int, ...]]]] = []
    for candidate in candidates:
        if candidate.ability_id.casefold() != ability_id:
            continue
        offsets_by_effect = _candidate_effect_offsets(candidate)
        if all(hit.gameplay_effect_id.casefold() in offsets_by_effect for hit in hits):
            relevant.append((candidate, offsets_by_effect))
    if not relevant:
        return None

    for _candidate, offsets_by_effect in relevant:
        if _aligned_first_offset_us(hits, offsets_by_effect) is not None:
            return True
    return False


def _continues_action(
    current_key: tuple[int, str, str] | None,
    current: Sequence[BattleAnalysisHit],
    hit: BattleAnalysisHit,
    key: tuple[int, str, str],
    candidates: Sequence[BattleActionAnimationCandidate],
) -> bool:
    if current_key != key or not current:
        return False
    previous = current[-1]
    gap_limit = _GAP_LIMITS_US.get(key[2], 900_000)
    if hit.relative_time_us - previous.relative_time_us > gap_limit:
        return False
    if hit.relative_time_us == previous.relative_time_us:
        return True

    kind = key[2]
    if kind == "QTE":
        return True
    static_decision = _static_sequence_decision((*current, hit), candidates)
    if static_decision is not None:
        return static_decision
    previous_phase = _phase_ordinal(previous, kind)
    current_phase = _phase_ordinal(hit, kind)
    if kind == "A" and previous_phase != current_phase:
        return False
    if (
        previous_phase is not None
        and current_phase is not None
        and current_phase < previous_phase
    ):
        return False

    return True


def _during_hold_input_end_us(
    hits: Sequence[BattleAnalysisHit],
    *,
    animation_end_us: int,
) -> int:
    if not hits:
        return animation_end_us
    repeated_gaps = [
        current.relative_time_us - previous.relative_time_us
        for previous, current in zip(hits, hits[1:])
        if current.gameplay_effect_id.casefold()
        == previous.gameplay_effect_id.casefold()
        and 0 < current.relative_time_us - previous.relative_time_us <= 750_000
    ]
    if not repeated_gaps:
        return animation_end_us
    inferred_tail_us = max(1, round(float(median(repeated_gaps))))
    return min(animation_end_us, hits[-1].relative_time_us + inferred_tail_us)


def _first_usable_end(
    offsets: Sequence[int],
    *,
    after_offset_us: int,
) -> int | None:
    return next(
        (
            int(offset)
            for offset in sorted(set(offsets))
            if int(offset) >= after_offset_us
        ),
        None,
    )


def _resolve_animation_candidate(
    action: BattleInferredAction,
    hits: Sequence[BattleAnalysisHit],
    candidate: BattleActionAnimationCandidate,
) -> _ResolvedAnimationWindow | None:
    if not hits or candidate.ability_id.casefold() != hits[0].ability_id.casefold():
        return None
    offsets_by_effect = _candidate_effect_offsets(candidate)
    first_hit_offset_us = _aligned_first_offset_us(hits, offsets_by_effect)
    if first_hit_offset_us is None:
        return None
    animation_start_us = max(0, hits[0].relative_time_us - first_hit_offset_us)
    matched_effects = len(
        {
            hit.gameplay_effect_id.casefold()
            for hit in hits
            if hit.gameplay_effect_id.casefold() in offsets_by_effect
        }
    )
    last_hit_offset_us = max(
        first_hit_offset_us,
        first_hit_offset_us + hits[-1].relative_time_us - hits[0].relative_time_us,
    )
    end_offset_us = _first_usable_end(
        candidate.trigger_end_offsets_us,
        after_offset_us=last_hit_offset_us,
    )
    end_source = "BP_TriggerEndAbilityEffect"
    if end_offset_us is None:
        end_offset_us = _first_usable_end(
            candidate.end_event_offsets_us,
            after_offset_us=last_hit_offset_us,
        )
        end_source = "结束技能事件"
    if end_offset_us is None:
        section_end_us = _first_usable_end(
            candidate.section_end_offsets_us,
            after_offset_us=last_hit_offset_us,
        )
        if (
            section_end_us is not None
            and section_end_us <= _RAW_DURATION_MAX_US
            and section_end_us - last_hit_offset_us <= _RAW_DURATION_MAX_TAIL_US
        ):
            end_offset_us = section_end_us
            end_source = "命中所在动画 Section"
    if (
        end_offset_us is None
        and last_hit_offset_us <= candidate.duration_us <= _RAW_DURATION_MAX_US
        and candidate.duration_us - last_hit_offset_us <= _RAW_DURATION_MAX_TAIL_US
    ):
        end_offset_us = candidate.duration_us
        end_source = "受限资源时长回退"
    if end_offset_us is None or end_offset_us <= first_hit_offset_us:
        return None
    animation_end_us = animation_start_us + end_offset_us
    if animation_end_us < action.end_us:
        return None
    return _ResolvedAnimationWindow(
        candidate=candidate,
        start_us=animation_start_us,
        end_us=animation_end_us,
        matched_effects=matched_effects,
        end_source=end_source,
    )


def _expand_action_from_animation(
    action: BattleInferredAction,
    hits: Sequence[BattleAnalysisHit],
    candidates: Sequence[BattleActionAnimationCandidate],
) -> BattleInferredAction:
    resolved = tuple(
        result
        for candidate in candidates
        if (result := _resolve_animation_candidate(action, hits, candidate)) is not None
    )
    if not resolved:
        return action
    best_match_count = max(item.matched_effects for item in resolved)
    best = tuple(
        item for item in resolved if item.matched_effects == best_match_count
    )
    reference = best[0]
    if any(
        abs(item.start_us - reference.start_us)
        > _EQUIVALENT_BOUNDARY_TOLERANCE_US
        or abs(item.end_us - reference.end_us)
        > _EQUIVALENT_BOUNDARY_TOLERANCE_US
        for item in best[1:]
    ):
        return action
    animation_start_us = (
        action.start_us if action.input_kind == "Q" and "时停头" in action.inference_basis
        else reference.start_us
    )
    hold_modes = {item.candidate.hold_damage_mode for item in best}
    hold_mode = (
        next(iter(hold_modes))
        if action.input_kind in {"A", "E", "Q"}
        and len(hold_modes) == 1
        and "none" not in hold_modes
        else "none"
    )
    hold_preludes = {item.candidate.hold_prelude_us for item in best}
    hold_prelude_us = (
        next(iter(hold_preludes)) if len(hold_preludes) == 1 else 0
    )
    input_start_us: int | None = None
    input_end_us: int | None = None
    projected_start_us = animation_start_us
    if hold_mode == "after_hold":
        input_end_us = animation_start_us
        input_start_us = max(0, input_end_us - hold_prelude_us)
        projected_start_us = input_start_us
    elif hold_mode == "during_hold":
        input_start_us = max(0, animation_start_us - hold_prelude_us)
        input_end_us = _during_hold_input_end_us(
            hits,
            animation_end_us=max(action.end_us, reference.end_us),
        )
        projected_start_us = input_start_us
    return replace(
        action,
        start_us=projected_start_us,
        end_us=max(action.end_us, reference.end_us),
        timing_confidence=(
            "中"
            if hold_mode == "none"
            and reference.end_source
            in {"BP_TriggerEndAbilityEffect", "结束技能事件"}
            else "低"
        ),
        inference_basis=(
            f"{action.inference_basis} 正式 GE 经 GA 事件绑定唯一对齐静态动画 "
            f"{reference.candidate.selector_key} 的命中 Notify；"
            f"结束采用{reference.end_source}。"
            + (
                " 静态长按程序表明伤害发生在持续按住期间。"
                if hold_mode == "during_hold"
                else " 静态长按程序表明伤害发生在松手后的输出段。"
                if hold_mode == "after_hold"
                else ""
            )
        ),
        input_gesture="hold" if hold_mode != "none" else "tap",
        input_start_us=input_start_us,
        input_end_us=input_end_us,
        hold_damage_mode=hold_mode,
    )


def _first_evidence_sequence(action: BattleInferredAction) -> int:
    try:
        return int(action.evidence_event_ids[0].split(":", 1)[0])
    except (IndexError, ValueError):
        return 0


def _truncate_interrupted_actions(
    actions: Sequence[BattleInferredAction],
) -> tuple[BattleInferredAction, ...]:
    by_character: dict[int, list[BattleInferredAction]] = defaultdict(list)
    for action in actions:
        by_character[action.character_id].append(action)
    result: list[BattleInferredAction] = []
    for character_actions in by_character.values():
        ordered = sorted(
            character_actions,
            key=lambda item: (_first_evidence_sequence(item), item.start_us),
        )
        for index, action in enumerate(ordered):
            if index + 1 >= len(ordered):
                result.append(action)
                continue
            next_action = ordered[index + 1]
            if action.start_us < next_action.start_us < action.end_us:
                input_end_us = action.input_end_us
                if input_end_us is not None:
                    input_end_us = min(input_end_us, next_action.start_us)
                action = replace(
                    action,
                    end_us=next_action.start_us,
                    input_end_us=input_end_us,
                    inference_basis=(
                        f"{action.inference_basis} 后续动作开始，截断此前动作后摇。"
                    ),
                )
            result.append(action)
    return tuple(
        sorted(
            result,
            key=lambda item: (item.start_us, item.character_id, item.action_id),
        )
    )


def _anchor_q_actions_to_time_stops(
    actions: Sequence[BattleInferredAction],
    intervals: Sequence[tuple[int | None, int | None]],
) -> tuple[BattleInferredAction, ...]:
    usable_intervals = tuple(
        sorted(
            (int(start_us), int(end_us))
            for start_us, end_us in intervals
            if start_us is not None
            and end_us is not None
            and int(end_us) > int(start_us)
        )
    )
    used_intervals: set[int] = set()
    anchored: list[BattleInferredAction] = []
    for action in sorted(
        actions,
        key=lambda item: (item.start_us, item.character_id, item.action_id),
    ):
        if action.input_kind != "Q":
            anchored.append(action)
            continue
        candidates = [
            (index, start_us, end_us)
            for index, (start_us, end_us) in enumerate(usable_intervals)
            if index not in used_intervals
            and start_us <= action.start_us <= (
                end_us + _Q_TIME_STOP_FOLLOW_TOLERANCE_US
            )
        ]
        if not candidates:
            anchored.append(action)
            continue
        index, start_us, end_us = min(
            candidates,
            key=lambda row: (
                0 if action.start_us <= row[2] else 1,
                max(0, action.start_us - row[2]),
                action.start_us - row[1],
            ),
        )
        used_intervals.add(index)
        anchored.append(
            replace(
                action,
                start_us=start_us,
                end_us=max(action.end_us, end_us),
                inference_basis=(
                    f"{action.inference_basis} Q 动作边界关联正式时停区间，"
                    "开始锚定到时停头、结束至少覆盖时停尾；不代表精确按键时刻。"
                ),
            )
        )
    return tuple(
        sorted(
            anchored,
            key=lambda item: (item.start_us, item.character_id, item.action_id),
        )
    )


class BattleActionInferenceService:
    """Infer action windows without mutating or replacing hit evidence."""

    @staticmethod
    def infer(
        hits: Sequence[BattleAnalysisHit],
        *,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        animation_candidates: Sequence[BattleActionAnimationCandidate] = (),
    ) -> tuple[BattleInferredAction, ...]:
        by_character: dict[int, list[BattleAnalysisHit]] = defaultdict(list)
        for hit in hits:
            if hit.character_id is not None and _is_action_evidence(hit):
                by_character[hit.character_id].append(hit)

        actions: list[BattleInferredAction] = []
        action_hits: dict[str, tuple[BattleAnalysisHit, ...]] = {}
        ordinal = 0
        for character_hits in by_character.values():
            current_key: tuple[int, str, str] | None = None
            current: list[BattleAnalysisHit] = []
            for hit in sorted(
                character_hits,
                key=lambda item: (item.relative_time_us, item.sequence),
            ):
                key = _action_key(hit)
                continues = _continues_action(
                    current_key,
                    current,
                    hit,
                    key,
                    animation_candidates,
                )
                if not continues and current_key is not None:
                    action = _build_action(ordinal, current_key, current)
                    actions.append(action)
                    action_hits[action.action_id] = tuple(current)
                    ordinal += 1
                    current = []
                current_key = key
                current.append(hit)
            if current_key is not None and current:
                action = _build_action(ordinal, current_key, current)
                actions.append(action)
                action_hits[action.action_id] = tuple(current)
                ordinal += 1

        anchored = _anchor_q_actions_to_time_stops(
            actions,
            time_stop_intervals,
        )
        expanded = tuple(
            _expand_action_from_animation(
                action,
                action_hits.get(action.action_id, ()),
                animation_candidates,
            )
            for action in anchored
        )
        return _truncate_interrupted_actions(expanded)
