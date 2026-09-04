# 把灵可同频合击派生为按目标和发起角色元素刷新的十二秒减抗区间。
"""Derived Linko coattack target-state projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleLinkoCoattackInference,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)


LINKO_COATTACK_BUFF_MODEL_VERSION = "linko-coattack-buff-v2"
_PASSIVE_ID = "PASSIVE-1072-GA_Radio072_Passive_2"
_DURATION_US = 12_000_000
_ELEMENT_SUFFIXES = frozenset({
    "chaos", "cosmos", "incantation", "lakshana", "nature", "psyche",
})
_ELEMENT_LABELS = {
    "chaos": "暗", "cosmos": "光", "incantation": "咒",
    "lakshana": "相", "nature": "灵", "psyche": "魂",
}
_ELEMENT_ASSET_SUFFIXES = {
    "chaos": "an", "cosmos": "guang", "incantation": "zhou",
    "lakshana": "xiang", "nature": "ling", "psyche": "hun",
}


def _element_suffix(value: str) -> str:
    normalized = str(value or "").strip()
    marker = "CHARACTER_ELEMENT_TYPE_"
    if marker in normalized:
        normalized = normalized.rsplit(marker, 1)[-1]
    normalized = normalized.casefold()
    return normalized if normalized in _ELEMENT_SUFFIXES else ""


class BattleLinkoCoattackBuffService:
    """Project the audited 8%/12s passive from versioned coattack inference."""

    @staticmethod
    def infer(
        *,
        build: Mapping[str, object] | None,
        inferences: Sequence[BattleLinkoCoattackInference],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        enabled = next(
            (
                row
                for row in BattleCharacterPassiveService.enabled_passives(build)
                if row.definition.passive_id == _PASSIVE_ID
            ),
            None,
        )
        if enabled is None:
            return ()
        hits_by_event = {hit.event_id: hit for hit in hits}
        triggers: list[tuple[int, str, str, BattleLinkoCoattackInference]] = []
        seen_action_targets: set[tuple[str, str, str]] = set()
        for inference in sorted(
            inferences,
            key=lambda row: (
                hits_by_event.get(row.event_id).relative_time_us
                if row.event_id in hits_by_event else battle_end_us,
                row.event_id,
            ),
        ):
            action_key = inference.qte_action_id or inference.event_id
            if inference.event_id not in hits_by_event:
                continue
            hit = hits_by_event[inference.event_id]
            element = _element_suffix(inference.damage_attribute)
            if not element or not hit.target_id:
                continue
            action_target_key = (action_key, hit.target_id, element)
            if action_target_key in seen_action_targets:
                continue
            seen_action_targets.add(action_target_key)
            triggers.append((hit.relative_time_us, hit.target_id, element, inference))

        result: list[BattleInferredBuffInterval] = []
        for index, (start_us, target_id, element, inference) in enumerate(triggers):
            if start_us >= battle_end_us:
                continue
            start_active = project_timeline_time_us(
                start_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            expiry_us = unproject_timeline_time_us(
                start_active + _DURATION_US,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )
            refresh_us = min(
                (
                    later_start
                    for later_start, later_target, later_element, _later
                    in triggers[index + 1:]
                    if later_target == target_id and later_element == element
                ),
                default=expiry_us,
            )
            end_us = min(expiry_us, refresh_us)
            if end_us <= start_us:
                continue
            result.append(BattleInferredBuffInterval(
                interval_id=(
                    f"buff:linko:precision-tuning:{target_id}:{element}:"
                    f"{inference.qte_action_id or inference.event_id}"
                ),
                buff_asset_path=(
                    "/Game/Blueprints/Abilities/Player/Ability_072_Radio/"
                    "PassiveEffect/Passive3/Buff_Radio072_Passive3_"
                    f"{_ELEMENT_ASSET_SUFFIXES[element]}"
                ),
                buff_name=f"精确调频·{_ELEMENT_LABELS[element]}属性抗性降低",
                source_effect_definition_id=_PASSIVE_ID,
                source_kind="derived_linko_coattack_inference",
                source_character_id=1072,
                source_character_name=enabled.source_character_name,
                target_scope="target",
                start_us=start_us,
                end_us=end_us,
                stacks=1,
                duration_policy="RefreshWholeStackActiveClock12Seconds",
                state_confidence=inference.confidence,
                value_confidence="高",
                inference_basis=(
                    "官方被动确认同频合击按发起角色属性降低目标 8% 异能抗性、"
                    "持续 12 秒且同属性只刷新；实机战报校验支持触发该次"
                    "同频合击的首击即消费减抗；"
                    "触发时刻与元素来自版本化同频合击"
                    f"推论（{inference.confidence}），不是 Core 原生状态事件。"
                ),
                trigger_event_type="INFERRED_LINKO_COATTACK",
                evidence_action_ids=tuple(
                    value
                    for value in (inference.trigger_action_id, inference.qte_action_id)
                    if value
                ),
                evidence_event_ids=inference.evidence_event_ids,
                modifiers=(BattleBuffModifierEvidence(
                    property_id=f"DamageResist{element.title()}Base",
                    modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="confirmed_character_passive",
                    magnitude_value=-0.08,
                    calculation_asset_path="",
                    value_confidence="高",
                ),),
                stacking_type="AggregateByTarget+RefreshWholeStack",
                stack_limit_count=1,
                target_id=target_id,
            ))
        return tuple(result)


__all__ = [
    "BattleLinkoCoattackBuffService",
    "LINKO_COATTACK_BUFF_MODEL_VERSION",
]
