# 从固定逐击、动作与带来源的时停证据保守推断灵可同频合击。
"""Pure inference for Linko-associated teammate QTE damage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleLinkoCoattackInference,
)
from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)
from src.services.battle_time_stop_projection_service import (
    BattleTimeStopProjection,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    projected_range_duration_us,
    time_stop_overlap_us,
)


LINKO_COATTACK_INFERENCE_MODEL_VERSION = "linko-coattack-v1"

_LINKO_CHARACTER_ID = 1072
_LINKO_SKILL_ABILITY_ID = "GA_Radio072_Skill"
_LINKO_QTE_LEVEL_ABILITY_ID = "GA_Radio072_QTE"
_LINKO_ULTRA_LTE_AOE_ABILITY_ID = "GA_Radio072_UltraSkillLTE_AOE"
_LINKO_SKILL_EFFECT_IDS = tuple(
    f"GE_Player_Radio072_Skill{ordinal}_Damage" for ordinal in range(1, 5)
)
_QTE_LTE_MAX_GAP_US = 150_000
_TYPE6_QTE_FOLLOW_TOLERANCE_US = 350_000

_CONFIDENCE_RANK = {"": 0, "低": 1, "中": 2, "高": 3}


@dataclass(frozen=True, slots=True)
class BattleLinkoType6Evidence:
    """Optional upstream type-6 event; never sufficient to prove a trigger."""

    event_id: str
    relative_time_us: int
    end_relative_time_us: int | None = None
    target_id: str = ""
    provenance: str = "nte_core_type6"
    confidence: str = "低"
    evidence_basis: str = ""


@dataclass(frozen=True, slots=True)
class _QteAction:
    action: BattleInferredAction
    hits: tuple[BattleAnalysisHit, ...]

    @property
    def first(self) -> BattleAnalysisHit:
        return self.hits[0]

    @property
    def last(self) -> BattleAnalysisHit:
        return self.hits[-1]


def _confidence_at_most(value: str, ceiling: str) -> str:
    normalized = value if value in _CONFIDENCE_RANK and value else "低"
    return min((normalized, ceiling), key=lambda item: _CONFIDENCE_RANK[item])


def _usable_intervals(
    projection: BattleTimeStopProjection,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        sorted(
            (int(start_us), int(end_us))
            for start_us, end_us in projection.intervals
            if int(end_us) > int(start_us)
        )
    )


def _inside_interval(
    start_us: int,
    end_us: int,
    interval: tuple[int, int],
) -> bool:
    return interval[0] <= start_us and end_us <= interval[1]


def _action_hits(
    action: BattleInferredAction,
    hits_by_event: dict[str, BattleAnalysisHit],
) -> tuple[BattleAnalysisHit, ...]:
    return tuple(
        sorted(
            (
                hits_by_event[event_id]
                for event_id in action.evidence_event_ids
                if event_id in hits_by_event
            ),
            key=lambda hit: (hit.relative_time_us, hit.sequence, hit.event_id),
        )
    )


def _is_primary_outgoing(hit: BattleAnalysisHit) -> bool:
    return (
        hit.direction == "outgoing"
        and not hit.is_follow_up
        and hit.event_id.endswith(":primary")
    )


def _is_teammate_qte(hit: BattleAnalysisHit) -> bool:
    return (
        _is_primary_outgoing(hit)
        and hit.character_id is not None
        and int(hit.character_id) != _LINKO_CHARACTER_ID
        and "qte" in hit.ability_id.casefold()
    )


def _qte_actions(
    actions: Sequence[BattleInferredAction],
    hits_by_event: dict[str, BattleAnalysisHit],
) -> tuple[_QteAction, ...]:
    result: list[_QteAction] = []
    for action in actions:
        if (
            action.input_kind != "QTE"
            or action.character_id == _LINKO_CHARACTER_ID
        ):
            continue
        hits = _action_hits(action, hits_by_event)
        if hits and all(_is_teammate_qte(hit) for hit in hits):
            result.append(_QteAction(action=action, hits=hits))
    return tuple(
        sorted(
            result,
            key=lambda row: (
                row.first.relative_time_us,
                row.first.sequence,
                row.action.action_id,
            ),
        )
    )


def _same_known_target(left: BattleAnalysisHit, right: BattleAnalysisHit) -> bool:
    return bool(left.target_id) and left.target_id == right.target_id


def _animation_response_end_us(
    e_hits: Sequence[BattleAnalysisHit],
    candidates: Sequence[BattleActionAnimationCandidate],
) -> int | None:
    first = e_hits[0]
    matches: list[int] = []
    for candidate in candidates:
        if candidate.ability_id.casefold() != _LINKO_SKILL_ABILITY_ID.casefold():
            continue
        offsets_by_effect = {
            effect_id.casefold(): tuple(int(value) for value in offsets)
            for effect_id, offsets in candidate.effect_hit_offsets_us
        }
        if any(
            effect_id.casefold() not in offsets_by_effect
            or not offsets_by_effect[effect_id.casefold()]
            for effect_id in _LINKO_SKILL_EFFECT_IDS
        ):
            continue
        first_offsets = offsets_by_effect[_LINKO_SKILL_EFFECT_IDS[0].casefold()]
        response_offsets = tuple(
            int(value)
            for value in (*candidate.section_end_offsets_us, candidate.duration_us)
            if int(value) > 0
        )
        if not response_offsets:
            continue
        animation_start_us = max(0, first.relative_time_us - min(first_offsets))
        matches.append(animation_start_us + max(response_offsets))
    return matches[0] if matches and len(set(matches)) == 1 else None


def _complete_linko_e_actions(
    actions: Sequence[BattleInferredAction],
    hits_by_event: dict[str, BattleAnalysisHit],
    candidates: Sequence[BattleActionAnimationCandidate],
) -> tuple[
    tuple[BattleInferredAction, tuple[BattleAnalysisHit, ...], int, int], ...
]:
    result = []
    for action in actions:
        if action.character_id != _LINKO_CHARACTER_ID or action.input_kind != "E":
            continue
        action_hits = _action_hits(action, hits_by_event)
        if len(action_hits) != len(_LINKO_SKILL_EFFECT_IDS):
            continue
        if any(not _is_primary_outgoing(hit) for hit in action_hits):
            continue
        if any(
            hit.ability_id.casefold() != _LINKO_SKILL_ABILITY_ID.casefold()
            for hit in action_hits
        ):
            continue
        if (
            tuple(hit.gameplay_effect_id.casefold() for hit in action_hits)
            != tuple(effect_id.casefold() for effect_id in _LINKO_SKILL_EFFECT_IDS)
        ):
            continue
        if not all(_same_known_target(action_hits[0], hit) for hit in action_hits[1:]):
            continue
        response_end_us = _animation_response_end_us(action_hits, candidates)
        if (
            response_end_us is None
            or response_end_us <= action_hits[-1].relative_time_us
        ):
            continue
        result.append((
            action,
            action_hits,
            action_hits[-1].relative_time_us,
            response_end_us,
        ))
    return tuple(
        sorted(result, key=lambda row: (row[1][0].relative_time_us, row[0].action_id))
    )


def _character_element(
    character_id: int,
    character_elements: Mapping[int, str],
) -> str:
    value = str(character_elements.get(character_id) or "").strip()
    if not value:
        return "unknown"
    marker = "CHARACTER_ELEMENT_TYPE_"
    marker_index = value.upper().rfind(marker)
    normalized = (
        value[marker_index + len(marker):]
        if marker_index >= 0
        else value
    )
    normalized = normalized.strip().casefold()
    return (
        normalized
        if normalized not in {"", "unknown", "none", "invalid", "max"}
        else "unknown"
    )


def _inference(
    qte: _QteAction,
    *,
    trigger_kind: str,
    confidence: str,
    basis: str,
    evidence_event_ids: Sequence[str],
    trigger_action_id: str,
    raw_gap_us: int,
    active_gap_us: int,
    time_stop_source_kind: str,
    time_stop_confidence: str,
    character_elements: Mapping[int, str],
    selection_pause_start_us: int | None = None,
    selection_pause_end_us: int | None = None,
) -> tuple[BattleLinkoCoattackInference, ...]:
    rows = []
    for hit in qte.hits:
        action_character_id = int(hit.character_id or 0)
        rows.append(BattleLinkoCoattackInference(
            event_id=hit.event_id,
            trigger_kind=trigger_kind,
            action_character_id=action_character_id,
            definition_owner_character_id=action_character_id,
            panel_character_id=_LINKO_CHARACTER_ID,
            skill_level_character_id=_LINKO_CHARACTER_ID,
            skill_level_ability_id=_LINKO_QTE_LEVEL_ABILITY_ID,
            damage_attribute_source_character_id=action_character_id,
            damage_attribute=_character_element(
                action_character_id,
                character_elements,
            ),
            damage_attribute_source="initiator_character_static_profile",
            confidence=confidence,
            inference_basis=basis,
            evidence_event_ids=tuple(dict.fromkeys(evidence_event_ids)),
            trigger_action_id=trigger_action_id,
            qte_action_id=qte.action.action_id,
            raw_gap_us=raw_gap_us,
            active_gap_us=active_gap_us,
            time_stop_source_kind=time_stop_source_kind,
            time_stop_confidence=time_stop_confidence,
            selection_pause_start_us=selection_pause_start_us,
            selection_pause_end_us=selection_pause_end_us,
        ))
    return tuple(rows)


class BattleLinkoCoattackInferenceService:
    """Infer bounded Linko coattack candidates without rewriting raw hits."""

    @classmethod
    def infer(
        cls,
        hits: Sequence[BattleAnalysisHit],
        actions: Sequence[BattleInferredAction],
        *,
        time_stop_projection: BattleTimeStopProjection,
        animation_candidates: Sequence[BattleActionAnimationCandidate] = (),
        type6_evidence: Sequence[BattleLinkoType6Evidence] = (),
        allow_legacy_e_fallback: bool = True,
        character_elements: Mapping[int, str] | None = None,
    ) -> tuple[BattleLinkoCoattackInference, ...]:
        resolved_character_elements = character_elements or {}
        hits_by_event = {hit.event_id: hit for hit in hits}
        qte_actions = _qte_actions(actions, hits_by_event)
        intervals = _usable_intervals(time_stop_projection)
        aoe_hits = tuple(
            hit
            for hit in sorted(
                hits,
                key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
            )
            if _is_primary_outgoing(hit)
            and hit.character_id == _LINKO_CHARACTER_ID
            and hit.ability_id.casefold()
            == _LINKO_ULTRA_LTE_AOE_ABILITY_ID.casefold()
        )

        claimed_qte_action_ids: set[str] = set()
        results: list[BattleLinkoCoattackInference] = []
        pending_pair_results: dict[
            str,
            tuple[BattleLinkoCoattackInference, ...],
        ] = {}

        # The QTE -> Linko LTE AOE pair is the independent signal. A time-stop
        # projection only constrains its context and can never prove Q by itself.
        pair_candidates_by_action: dict[
            str,
            tuple[tuple[BattleAnalysisHit, int, tuple[tuple[int, int], ...]], ...],
        ] = {}
        candidate_actions_by_aoe: dict[str, set[str]] = {}
        for qte in qte_actions:
            pair_candidates = []
            for aoe in aoe_hits:
                raw_gap_us = aoe.relative_time_us - qte.last.relative_time_us
                if not 0 <= raw_gap_us <= _QTE_LTE_MAX_GAP_US:
                    continue
                if not _same_known_target(qte.last, aoe):
                    continue
                matching_intervals = tuple(
                    interval
                    for interval in intervals
                    if _inside_interval(
                        qte.first.relative_time_us,
                        aoe.relative_time_us,
                        interval,
                    )
                )
                pair_candidates.append((aoe, raw_gap_us, matching_intervals))
                candidate_actions_by_aoe.setdefault(aoe.event_id, set()).add(
                    qte.action.action_id
                )
            pair_candidates_by_action[qte.action.action_id] = tuple(pair_candidates)

        for qte in qte_actions:
            pair_candidates = pair_candidates_by_action[qte.action.action_id]
            if len(pair_candidates) != 1:
                continue
            aoe, raw_gap_us, matching_intervals = pair_candidates[0]
            if len(candidate_actions_by_aoe.get(aoe.event_id, ())) != 1:
                continue
            source_kind = time_stop_projection.source_kind
            if source_kind == "nte_core" and len(matching_intervals) == 1:
                context_time_stop_source = source_kind
                context_time_stop_confidence = time_stop_projection.confidence
                confidence = _confidence_at_most(
                    time_stop_projection.confidence,
                    "中",
                )
                basis = (
                    "队友 QTE 后紧邻灵可 UltraSkillLTE_AOE，且二者位于同一 "
                    "nte-core 战报记录的时停区间；该区间仅作中置信上下文辅助，"
                    "QTE→LTE 配对才是触发推论主体。"
                )
            elif (
                source_kind == "inferred_q_action"
                and len(matching_intervals) == 1
            ):
                context_time_stop_source = source_kind
                context_time_stop_confidence = time_stop_projection.confidence
                confidence = "低"
                basis = (
                    "队友 QTE 后紧邻灵可 UltraSkillLTE_AOE；二者同时落在低置信 "
                    "Q 动作回退区间。回退区间不能单独证明触发，只与独立配对共同"
                    "形成低置信推论。"
                )
            else:
                context_time_stop_source = "none"
                context_time_stop_confidence = ""
                confidence = "低"
                basis = (
                    "队友 QTE 后在 150ms 内紧邻同目标灵可 UltraSkillLTE_AOE；"
                    "当前没有唯一且覆盖完整配对的可用时停区间，因此只记录低置信"
                    "同频响应配对，不据此证明由灵可 Q 或 E 触发。"
                )
            active_gap_us = projected_range_duration_us(
                qte.last.relative_time_us,
                aoe.relative_time_us,
                intervals=intervals,
                mode=ACTIVE_TIME_MODE,
            )
            pending_pair_results[qte.action.action_id] = _inference(
                qte,
                trigger_kind="qte_lte_pair",
                confidence=confidence,
                basis=basis,
                evidence_event_ids=(*qte.action.evidence_event_ids, aoe.event_id),
                trigger_action_id="",
                raw_gap_us=raw_gap_us,
                active_gap_us=active_gap_us,
                time_stop_source_kind=context_time_stop_source,
                time_stop_confidence=context_time_stop_confidence,
                character_elements=resolved_character_elements,
            )

        complete_e_actions = _complete_linko_e_actions(
            actions,
            hits_by_event,
            animation_candidates,
        )
        e_claims: dict[str, list[tuple[
            BattleInferredAction,
            tuple[BattleAnalysisHit, ...],
            int,
            tuple[BattleLinkoType6Evidence, ...],
        ]]] = {}
        type6_rows = tuple(type6_evidence)
        for action, e_hits, selection_start_us, response_end_us in complete_e_actions:
            last_e = e_hits[-1]
            candidates = []
            for qte in qte_actions:
                if qte.action.action_id in claimed_qte_action_ids:
                    continue
                if not (
                    last_e.relative_time_us
                    < qte.first.relative_time_us
                    <= response_end_us
                ):
                    continue
                if not _same_known_target(last_e, qte.first):
                    continue
                if time_stop_overlap_us(
                    last_e.relative_time_us,
                    qte.first.relative_time_us + 1,
                    time_stop_projection.non_type6_intervals,
                ):
                    continue
                candidates.append(qte)
            relevant_type6 = tuple(
                row
                for row in type6_rows
                if int(row.relative_time_us) <= response_end_us
                and int(row.end_relative_time_us or row.relative_time_us)
                >= last_e.relative_time_us
                and (
                    not row.target_id
                    or any(row.target_id == qte.first.target_id for qte in candidates)
                )
            )
            selected: _QteAction | None = None
            selected_type6: tuple[BattleLinkoType6Evidence, ...] = ()
            if relevant_type6:
                matches_by_action: dict[
                    str,
                    tuple[_QteAction, list[BattleLinkoType6Evidence]],
                ] = {}
                for qte in candidates:
                    qte_selection_us = (
                        qte.action.start_us
                        if qte.action.start_us > last_e.relative_time_us
                        else qte.first.relative_time_us
                    )
                    matching_rows = [
                        row
                        for row in relevant_type6
                        if int(row.relative_time_us) <= qte_selection_us
                        <= int(row.end_relative_time_us or row.relative_time_us)
                        + _TYPE6_QTE_FOLLOW_TOLERANCE_US
                        and (
                            not row.target_id
                            or row.target_id == qte.first.target_id
                        )
                    ]
                    if matching_rows:
                        matches_by_action[qte.action.action_id] = (
                            qte,
                            matching_rows,
                        )
                if len(matches_by_action) == 1:
                    selected, matched_rows = next(iter(matches_by_action.values()))
                    selected_type6 = tuple(matched_rows)
            elif allow_legacy_e_fallback and candidates:
                earliest_us = min(qte.first.relative_time_us for qte in candidates)
                earliest = tuple(
                    qte for qte in candidates
                    if qte.first.relative_time_us == earliest_us
                )
                if len(earliest) == 1:
                    selected = earliest[0]
            if selected is not None:
                e_claims.setdefault(selected.action.action_id, []).append((
                    action,
                    e_hits,
                    selection_start_us,
                    selected_type6,
                ))

        for qte in qte_actions:
            claims = e_claims.get(qte.action.action_id, ())
            if qte.action.action_id in claimed_qte_action_ids or len(claims) != 1:
                continue
            action, e_hits, selection_start_us, selected_type6 = claims[0]
            last_e = e_hits[-1]
            selection_end_us = (
                qte.action.start_us
                if qte.action.start_us > selection_start_us
                else qte.first.relative_time_us
            )
            type6_basis = (
                "；唯一 type6 选人区间覆盖或紧邻该 QTE，但不能单独证明同频触发"
                if selected_type6
                else "；旧战报无类型证据，采用窗口内最早的唯一合法 QTE 回退"
            )
            raw_gap_us = qte.first.relative_time_us - last_e.relative_time_us
            active_gap_us = projected_range_duration_us(
                last_e.relative_time_us,
                qte.first.relative_time_us,
                intervals=intervals,
                mode=ACTIVE_TIME_MODE,
            )
            results.extend(_inference(
                qte,
                trigger_kind="skill",
                confidence="中",
                basis=(
                    "完整有序的灵可 E 四段逐击后，在唯一静态动画响应窗口内"
                    "出现首个同目标队友 QTE，且没有跨越时停或其他已认领 QTE"
                    f"{type6_basis}。"
                ),
                evidence_event_ids=(
                    *action.evidence_event_ids,
                    *(row.event_id for row in selected_type6),
                    *qte.action.evidence_event_ids,
                ),
                trigger_action_id=action.action_id,
                raw_gap_us=raw_gap_us,
                active_gap_us=active_gap_us,
                time_stop_source_kind=(
                    "nte_core_type6" if selected_type6 else "none"
                ),
                time_stop_confidence="高" if selected_type6 else "",
                character_elements=resolved_character_elements,
                selection_pause_start_us=(
                    selection_start_us
                    if selection_end_us > selection_start_us
                    else None
                ),
                selection_pause_end_us=(
                    selection_end_us
                    if selection_end_us > selection_start_us
                    else None
                ),
            ))
            claimed_qte_action_ids.add(qte.action.action_id)

        for qte in qte_actions:
            if qte.action.action_id in claimed_qte_action_ids:
                continue
            results.extend(pending_pair_results.get(qte.action.action_id, ()))

        return tuple(
            sorted(results, key=lambda row: hits_by_event[row.event_id].sequence)
        )


__all__ = [
    "BattleLinkoCoattackInferenceService",
    "BattleLinkoType6Evidence",
    "LINKO_COATTACK_INFERENCE_MODEL_VERSION",
]
