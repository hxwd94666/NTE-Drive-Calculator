# 合并同一次正式技能被其他逐击切开的动作片段。
"""Post-process inferred battle actions without changing hit evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction


ZANKOU_DUAL_FORM_MAX_GAP_US = 8_000_000


def merge_zankou_dual_form_actions(
    actions: Sequence[BattleInferredAction],
    action_hits: dict[str, tuple[BattleAnalysisHit, ...]],
) -> tuple[BattleInferredAction, ...]:
    """Join the fixed Magic/Force halves of one Zankou Q action."""

    consumed: set[str] = set()
    merged: list[BattleInferredAction] = []
    ordered = tuple(sorted(actions, key=lambda row: (row.start_us, row.action_id)))
    for action in ordered:
        if action.action_id in consumed:
            continue
        effects = tuple(value.casefold() for value in action.gameplay_effect_ids)
        if (
            action.input_kind != "Q"
            or action.character_id != 1036
            or not effects
            or not all("magicultraskill" in value for value in effects)
        ):
            merged.append(action)
            continue
        matches = tuple(
            candidate
            for candidate in ordered
            if candidate.action_id not in consumed
            and candidate.action_id != action.action_id
            and candidate.input_kind == "Q"
            and candidate.character_id == action.character_id
            and 0 < candidate.start_us - action.end_us
            <= ZANKOU_DUAL_FORM_MAX_GAP_US
            and candidate.gameplay_effect_ids
            and all(
                "forceultraskill" in value.casefold()
                for value in candidate.gameplay_effect_ids
            )
        )
        if not matches:
            merged.append(action)
            continue
        force = min(matches, key=lambda row: (row.start_us, row.action_id))
        consumed.add(force.action_id)
        action_hits[action.action_id] = (
            *action_hits.get(action.action_id, ()),
            *action_hits.get(force.action_id, ()),
        )
        merged.append(replace(
            action,
            end_us=max(action.end_us, force.end_us),
            hits=action.hits + force.hits,
            damage=action.damage + force.damage,
            inference_basis=(
                f"{action.inference_basis} 残虹 Q 的 Magic/Force 两段按同一次"
                "正式技能来源和相邻固定形态顺序合并。"
            ),
            evidence_event_ids=(
                *action.evidence_event_ids,
                *force.evidence_event_ids,
            ),
            gameplay_effect_ids=tuple(dict.fromkeys((
                *action.gameplay_effect_ids,
                *force.gameplay_effect_ids,
            ))),
        ))
    return tuple(sorted(merged, key=lambda row: (row.start_us, row.action_id)))
