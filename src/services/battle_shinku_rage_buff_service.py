# 以正式升腾技能命中证据投影真红增伤，不猜测未观测的完整状态区间。
"""Conservative per-hit Shinku Rage damage-up projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_character_awakening_hit_service import (
    SHINKU_RAGE_DAMAGE_IDS,
    SHINKU_RAGE_REQUIREMENT,
)
from src.services.official_role_awakening_service import active_awaken_effects

_CURVE_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_ShinkuEffectFigure"
_BUFF_ASSET = (
    "/Game/Blueprints/Abilities/Player/Ability_076_Shinku/Buff/Buff_Shinku_Rage"
)
_CALC_ASSET = (
    "/Game/Blueprints/Abilities/Calculation/Shinku/Calc_Shinku_RageDmgUp"
)


@dataclass(frozen=True, slots=True)
class BattleShinkuRageConfig:
    damage_up: float
    resonance_damage_up: float
    awakenings: tuple[Mapping[str, Any], ...]


def _curve_value(static_dao: Any, curve_id: str) -> float:
    points = tuple((static_dao.get_combat_curve(_CURVE_TABLE, curve_id) or {}).get(
        "points"
    ) or ())
    if len(points) != 1 or not isinstance(points[0].get("value"), (int, float)):
        raise ValueError(f"真红升腾曲线 {curve_id} 缺少唯一数值证据")
    value = float(points[0]["value"])
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"真红升腾曲线 {curve_id} 数值无效")
    return value


class BattleShinkuRageBuffService:
    @staticmethod
    def load_config(static_dao: Any) -> BattleShinkuRageConfig:
        return BattleShinkuRageConfig(
            damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUp"),
            resonance_damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUpEx_L3"),
            awakenings=tuple(static_dao.list_character_awaken_effects(1076)),
        )

    @staticmethod
    def infer(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        config: BattleShinkuRageConfig | None,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = next((
            row for row in (build or {}).get("characters") or ()
            if int(row.get("character_id") or 0) == 1076
        ), None)
        if character is None:
            return ()
        profile = dict(character.get("profile") or {})
        profile.setdefault("awakening_level", int(character.get("awakening_level") or 0))
        resonance = config is not None and any(
            row.get("effect_id") == "resonance_3"
            for row in active_awaken_effects(profile, config.awakenings)
        )
        grouped: dict[int, list[str]] = {}
        for hit in hits:
            if (
                hit.character_id == 1076
                and hit.direction == "outgoing"
                and hit.gameplay_effect_id.casefold() in SHINKU_RAGE_DAMAGE_IDS
            ):
                grouped.setdefault(hit.relative_time_us, []).append(hit.event_id)
        if config is None:
            value = None
            basis = "缺少真红升腾正式曲线，增伤数值保持未知。"
        else:
            value = config.damage_up + (config.resonance_damage_up if resonance else 0.0)
            basis = (
                f"正式曲线 Shinku_Rage_DmgUp={config.damage_up:g}"
                + (
                    f" + Shinku_Rage_DmgUpEx_L3={config.resonance_damage_up:g}"
                    if resonance else ""
                )
                + " 加入 DamageUpGeneralBase。"
            )
        return tuple(
            BattleInferredBuffInterval(
                interval_id=f"buff:shinku-rage:{at_us}",
                buff_asset_path=_BUFF_ASSET,
                buff_name="升腾之赤（含三觉共鸣）" if resonance else "升腾之赤",
                source_effect_definition_id="character-rage:1076",
                source_kind="confirmed_character_form",
                source_character_id=1076,
                source_character_name=str(character.get("observed_name") or "真红"),
                target_scope="self",
                start_us=at_us,
                end_us=at_us + 1,
                stacks=1,
                duration_policy="ObservedRageHitOnly",
                state_confidence="中",
                value_confidence="未解析" if value is None else "高",
                inference_basis=(
                    "正式 Rage 伤害项确认该击属于升腾技能；仅投影命中时点，"
                    "不补齐未观测的持续状态，不外推威慑凝视、独行或普通技能。"
                    + basis
                ),
                trigger_event_type="FORMAL_SHINKU_RAGE_HIT",
                evidence_action_ids=(),
                evidence_event_ids=tuple(event_ids),
                modifiers=(BattleBuffModifierEvidence(
                    property_id="DamageUpGeneralBase",
                    modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="confirmed_static_curve",
                    magnitude_value=value,
                    calculation_asset_path=_CALC_ASSET,
                    value_confidence="未解析" if value is None else "高",
                    application_requirement_asset_path=SHINKU_RAGE_REQUIREMENT,
                ),),
                stacking_type="AggregateByTarget",
                stack_limit_count=1,
            )
            for at_us, event_ids in sorted(grouped.items())
        )
