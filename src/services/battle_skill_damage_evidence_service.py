# 将静态 skill_damage 数组按战报冻结技能等级解析为逐击倍率证据。
"""Static skill-damage assembly for deterministic battle-hit replay."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisSnapshot,
    BattleSkillDamageEvidence,
)
from src.services.damage_calculation_service import (
    reaction_tier_for_character_level,
    skill_tier_for_effective_level,
)
from src.services.battle_dot_stack_state_service import (
    reconstruct_dot_stack_states,
    zankou_scorch_variant_for_build,
)
from src.services.battle_zankou_awakening_state_service import (
    reconstruct_zankou_q_final_damage,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)
from src.services.battle_character_awakening_hit_service import (
    character_awakening_damage_multiplier,
)
from src.services.battle_damage_composition_service import (
    explicit_reaction_channel_for_hit,
)
from src.services.battle_audited_treatment_adapter_service import (
    is_kuhara_q_settlement_hit,
)
from src.services.official_role_awakening_service import (
    active_awaken_effects,
    awaken_skill_level_delta,
)


_NON_CRITICAL_DAMAGE_IDS = frozenset({
    "ge_player_kuhara_targetactivateskill_damage",
    "ge_reaction_4_new_1070_damage",
    "ge_reaction_3_new_1071_damage",
})
_NO_SKILL_LEVEL_DAMAGE_IDS = frozenset({"ge_reaction_3_new_1071_damage"})
_KUHARA_EFFECT_CURVE_TABLE = (
    "/Game/DataTable/Skill/GlobalCharacterData/DT_KuharaEffectFigure"
)
_KUHARA_Q_SETTLEMENT_CURVE_ID = "Kuhara_BudBoom_CoefAddUltraSkill"
_ORDINARY_SCORCH_DAMAGE_ID = "Buff_Reaction_5_new"
_ZANKOU_SCORCH_DAMAGE_ID = "Buff_Reaction_5_new_1036"


def _single_point_curve_value(
    static_dao: Any,
    table_path: str,
    curve_id: str,
) -> float | None:
    if not hasattr(static_dao, "get_combat_curve"):
        return None
    points = (static_dao.get_combat_curve(table_path, curve_id) or {}).get(
        "points"
    ) or ()
    values = tuple(
        float(row["value"])
        for row in points
        if isinstance(row.get("value"), (int, float))
    )
    return values[0] if len(values) == 1 else None


def _reaction_level_multiplier(
    static_dao: Any,
    damage_id: str,
    character_level: int,
) -> tuple[float | None, str]:
    curve = static_dao.get_reaction_damage_curve(damage_id)
    points = (curve or {}).get("points") or ()
    if len(points) != 16:
        return None, ""
    tier = reaction_tier_for_character_level(character_level)
    value = points[tier].get("value")
    if not isinstance(value, (int, float)):
        return None, ""
    return float(value), f"reaction_damage[{tier}]={float(value):g}"


def _co_timed_damage_ids(
    analysis: BattleAnalysisSnapshot,
) -> dict[str, tuple[str, str]]:
    """Infer a missing GE only from one unique same-character microsecond sibling."""

    grouped: dict[tuple[int | None, int], set[str]] = {}
    for hit in analysis.hits:
        damage_id = hit.gameplay_effect_id.strip()
        if hit.direction != "outgoing" or not damage_id:
            continue
        grouped.setdefault(
            (hit.character_id, hit.relative_time_us),
            set(),
        ).add(damage_id)
    inferred = {}
    for hit in analysis.hits:
        if hit.gameplay_effect_id.strip() or hit.direction != "outgoing":
            continue
        candidates = grouped.get((hit.character_id, hit.relative_time_us), set())
        if len(candidates) != 1:
            continue
        damage_id = next(iter(candidates))
        inferred[hit.event_id] = (
            damage_id,
            "同角色同一微秒只有一个已识别伤害项，补充其 GE 作为弱证据",
        )
    return inferred


def _character_builds(build: Mapping[str, Any] | None) -> dict[int, Mapping[str, Any]]:
    return {
        int(row["character_id"]): row
        for row in (build or {}).get("characters") or ()
    }


def _canonical_reaction_damage_id(
    *,
    channel_id: str,
    observed_damage_id: str,
    build: Mapping[str, Any] | None,
) -> str | None:
    """Choose a formal reaction row without collapsing both scorch variants."""

    if channel_id == "reaction_nova":
        return "Buff_Reaction_4_new"
    observed = observed_damage_id.casefold()
    is_observed_scorch = observed in {
        _ORDINARY_SCORCH_DAMAGE_ID.casefold(),
        _ZANKOU_SCORCH_DAMAGE_ID.casefold(),
    }
    if channel_id != "reaction_scorch" and not is_observed_scorch:
        return None
    variant = zankou_scorch_variant_for_build(build)
    if variant == "ordinary":
        return _ORDINARY_SCORCH_DAMAGE_ID
    if variant == "zankou":
        return _ZANKOU_SCORCH_DAMAGE_ID
    if observed == _ORDINARY_SCORCH_DAMAGE_ID.casefold():
        return _ORDINARY_SCORCH_DAMAGE_ID
    if observed == _ZANKOU_SCORCH_DAMAGE_ID.casefold():
        return _ZANKOU_SCORCH_DAMAGE_ID
    return None


def _effective_level(
    character: Mapping[str, Any],
    ability_id: str,
    awakenings: tuple[Mapping[str, Any], ...],
) -> int:
    levels = {
        str(row.get("skill_id") or ""): int(row.get("skill_level") or 0)
        for row in character.get("skills") or ()
    }
    base = levels.get(ability_id)
    if base is None or base < 1:
        profile = character.get("profile") or {}
        base = int((profile.get("skill_levels") or {}).get(ability_id) or 1)
    profile = dict(character.get("profile") or {})
    profile.setdefault(
        "awakening_level",
        int(character.get("awakening_level") or 0),
    )
    return base + awaken_skill_level_delta(profile, awakenings, ability_id)


def _skill_level_ability_id(
    static_dao: Any,
    *,
    character_id: int,
    damage_id: str,
    observed_ability_id: str,
    imported_ability_id: str,
) -> tuple[str, str]:
    """Resolve a derived GE to the player-levelled parent ability when bounded."""

    if damage_id.casefold() in _NO_SKILL_LEVEL_DAMAGE_IDS:
        return "", "正式被动派生倍率不读取 Q 或其他技能等级"

    if not hasattr(static_dao, "list_skill_level_ability_candidates"):
        return imported_ability_id or observed_ability_id, ""
    candidates = tuple(
        str(value)
        for value in static_dao.list_skill_level_ability_candidates(
            character_id,
            damage_id,
        )
        if str(value)
    )
    if observed_ability_id in candidates:
        selected = observed_ability_id
    elif len(candidates) == 1:
        selected = candidates[0]
    elif imported_ability_id in candidates:
        selected = imported_ability_id
    else:
        selected = imported_ability_id or observed_ability_id
    if not selected or selected == imported_ability_id:
        return selected, ""
    return (
        selected,
        f"正式技能等级提示将派生伤害 {imported_ability_id or damage_id} "
        f"归入 {selected}",
    )


def _scaling(
    row: Mapping[str, Any],
    tier: int,
) -> tuple[str, float] | None:
    candidates = (
        ("Atk", row.get("atk_rate_base") or ()),
        ("Def", row.get("def_rate_base") or ()),
        ("HPMax", row.get("hp_rate_base") or ()),
    )
    selected = []
    for property_id, values in candidates:
        if not values:
            continue
        value = float(values[min(tier, len(values) - 1)])
        if value != 0:
            selected.append((property_id, value))
    return selected[0] if len(selected) == 1 else None


class BattleSkillDamageEvidenceService:
    """Resolve each immutable hit to one static, effective-level multiplier."""

    @classmethod
    def load(
        cls,
        static_dao: Any,
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, Any] | None,
    ) -> tuple[BattleSkillDamageEvidence, ...]:
        builds = _character_builds(build)
        awakenings_by_character = {
            character_id: tuple(static_dao.list_character_awaken_effects(character_id))
            if hasattr(static_dao, "list_character_awaken_effects")
            else ()
            for character_id in builds
        }
        dot_states = reconstruct_dot_stack_states(
            analysis,
            build,
        )
        zankou_q_final_damage = reconstruct_zankou_q_final_damage(
            analysis,
            builds.get(1036),
        )
        co_timed_damage_ids = _co_timed_damage_ids(analysis)
        cache: dict[str, dict[str, Any] | None] = {}
        evidence = []
        for hit in analysis.hits:
            damage_id = hit.gameplay_effect_id.strip()
            inferred_basis = ""
            explicit_reaction = explicit_reaction_channel_for_hit(hit)
            reaction_channel = explicit_reaction[0] if explicit_reaction else ""
            canonical_reaction_damage_id = _canonical_reaction_damage_id(
                channel_id=reaction_channel,
                observed_damage_id=damage_id,
                build=build,
            )
            if reaction_channel == "reaction_scorch" and (
                canonical_reaction_damage_id is None
            ):
                # A reused Core GE plus the word "浊燃" proves the public damage
                # channel, but not whether the ordinary or Zankou formula applies.
                continue
            if canonical_reaction_damage_id is not None:
                original_damage_id = damage_id
                damage_id = canonical_reaction_damage_id
                if explicit_reaction is not None:
                    inferred_basis = (
                        f"显式{explicit_reaction[1]}结算身份优先于 Core 复用的来源 GE "
                        f"{original_damage_id}"
                    )
                elif original_damage_id.casefold() != damage_id.casefold():
                    inferred_basis = (
                        f"冻结残虹突破把正式浊燃记录 {original_damage_id} "
                        f"替换为 {damage_id}"
                    )
            if not damage_id and hit.event_id in co_timed_damage_ids:
                damage_id, inferred_basis = co_timed_damage_ids[hit.event_id]
            if not damage_id:
                continue
            if damage_id not in cache:
                cache[damage_id] = static_dao.get_skill_damage(damage_id)
            row = cache[damage_id]
            if row is None:
                continue
            source_character_id = (
                1036
                if damage_id.casefold() == _ZANKOU_SCORCH_DAMAGE_ID.casefold()
                else hit.character_id
            )
            if hasattr(static_dao, "list_skill_damage_owner_character_ids"):
                formal_owner_ids = tuple(
                    int(value)
                    for value in static_dao.list_skill_damage_owner_character_ids(
                        damage_id
                    )
                )
                if len(formal_owner_ids) == 1:
                    formal_owner_id = formal_owner_ids[0]
                    if formal_owner_id != source_character_id:
                        inferred_basis = (
                            f"{inferred_basis}；" if inferred_basis else ""
                        ) + (
                            f"正式伤害技能属于角色 {formal_owner_id}，"
                            f"不采用 Core 会话归属角色 {source_character_id} 的面板"
                        )
                    source_character_id = formal_owner_id
            character = builds.get(source_character_id or -1)
            if character is None:
                continue
            imported_ability_id = str(row.get("ability_id") or "")
            ability_id, level_owner_basis = _skill_level_ability_id(
                static_dao,
                character_id=int(character["character_id"]),
                damage_id=damage_id,
                observed_ability_id=str(hit.ability_id or ""),
                imported_ability_id=imported_ability_id,
            )
            if ability_id:
                effective_level = _effective_level(
                    character,
                    ability_id,
                    awakenings_by_character.get(int(character["character_id"]), ()),
                )
                tier = skill_tier_for_effective_level(
                    effective_level,
                    max(
                        len(row.get("atk_rate_base") or ()),
                        len(row.get("def_rate_base") or ()),
                        len(row.get("hp_rate_base") or ()),
                        1,
                    ),
                )
            else:
                effective_level = int(character.get("character_level") or 80)
                tier = max(
                    len(row.get("atk_rate_base") or ()),
                    len(row.get("def_rate_base") or ()),
                    len(row.get("hp_rate_base") or ()),
                    1,
                ) - 1
            scaling = _scaling(row, tier)
            if scaling is None:
                continue
            # FTAtkRateBaseCoefficient is retained as imported asset metadata,
            # but observed Oneiroi hits prove that HP damage uses the raw
            # AtkRateBaseArray value. Only explicit awakening/passive adapters
            # may modify the replay multiplier below.
            coefficient = 1.0
            fixed_crit_rate = float(row.get("fixed_crit_rate") or 0.0)
            damage_attribute = str(row.get("damage_type") or "unknown").casefold()
            critical_policy = (
                "unknown" if damage_attribute == "true"
                else "disabled"
                if damage_id.casefold() in _NON_CRITICAL_DAMAGE_IDS
                else "fixed" if fixed_crit_rate > 0.0
                else "character"
            )
            profile = dict(character.get("profile") or {})
            profile.setdefault(
                "awakening_level",
                int(character.get("awakening_level") or 0),
            )
            active_awakening_ids = {
                str(row.get("effect_id") or "")
                for row in active_awaken_effects(
                    profile,
                    awakenings_by_character.get(int(character["character_id"]), ()),
                )
            }
            basis = (
                f"skill_damage[{tier}] 原始倍率数组，"
                f"有效等级 {effective_level}"
            )
            if inferred_basis:
                basis += f"；{inferred_basis}：{damage_id}"
            if level_owner_basis:
                basis += f"；{level_owner_basis}"
            if damage_id.casefold() == _ZANKOU_SCORCH_DAMAGE_ID.casefold():
                basis += "；残虹被动生成并刷新浊燃，公式归属固定为残虹"
            elif damage_id.casefold() == _ORDINARY_SCORCH_DAMAGE_ID.casefold():
                basis += "；Core hit.character_id 作为本次实测环合公式归属"
            character_level = int(character.get("character_level") or 80)
            reaction_level_multiplier, reaction_basis = _reaction_level_multiplier(
                static_dao,
                damage_id,
                character_level,
            )
            if reaction_basis:
                basis += f"；{reaction_basis}"
            if (
                int(character["character_id"]) == 1036
                and "resonance_3" in active_awakening_ids
                and ability_id == "GA_Zankou_UltraSkill"
            ):
                coefficient *= 1.20
                basis += "；三觉极轨终结倍率整体 ×1.20"
            awakening_coefficient, awakening_basis = (
                character_awakening_damage_multiplier(
                    character,
                    damage_id=damage_id,
                )
            )
            coefficient *= awakening_coefficient
            if awakening_basis:
                basis += f"；{awakening_basis}"
            passive_coefficient, passive_basis = (
                BattleCharacterPassiveService.skill_multiplier_adjustment(
                    character,
                    damage_id=damage_id,
                    ability_id=ability_id,
                )
            )
            coefficient *= passive_coefficient
            if passive_basis:
                basis += f"；{passive_basis}"
            zankou_q_final = zankou_q_final_damage.get(hit.event_id)
            if is_kuhara_q_settlement_hit(
                hit,
                getattr(analysis, "inferred_actions", ()),
            ):
                q_settlement_coefficient = _single_point_curve_value(
                    static_dao,
                    _KUHARA_EFFECT_CURVE_TABLE,
                    _KUHARA_Q_SETTLEMENT_CURVE_ID,
                )
                if q_settlement_coefficient is not None:
                    coefficient *= q_settlement_coefficient
                    basis += (
                        "；正式九原 Q 主动玫约清算状态系数 "
                        f"{_KUHARA_Q_SETTLEMENT_CURVE_ID}="
                        f"{q_settlement_coefficient:g}"
                    )
            evidence.append(BattleSkillDamageEvidence(
                event_id=hit.event_id,
                damage_id=damage_id,
                ability_id=ability_id,
                damage_attribute=damage_attribute,
                damage_source_category=str(
                    row.get("damage_source_category") or "unknown"
                ),
                fixed_crit_rate=fixed_crit_rate,
                scaling_property_id=scaling[0],
                scaling_multiplier=scaling[1],
                multiplier_coefficient=coefficient,
                effective_skill_level=effective_level,
                evidence_basis=basis,
                source_character_id=source_character_id,
                formula_kind=(
                    "reaction" if reaction_level_multiplier is not None else "skill"
                ),
                level_multiplier=reaction_level_multiplier,
                state_multiplier=float(
                    dot_states.get(hit.event_id).coefficient
                    if hit.event_id in dot_states else 1.0
                ),
                state_multiplier_label=(
                    dot_states[hit.event_id].label
                    if hit.event_id in dot_states else ""
                ),
                state_multiplier_basis=(
                    dot_states[hit.event_id].evidence_basis
                    if hit.event_id in dot_states else ""
                ),
                state_confidence=(
                    dot_states[hit.event_id].confidence
                    if hit.event_id in dot_states else ""
                ),
                dot_final_multiplier=float(
                    dot_states[hit.event_id].dot_final_multiplier
                    if hit.event_id in dot_states else 1.0
                ),
                dot_final_multiplier_basis=(
                    dot_states[hit.event_id].dot_final_multiplier_basis
                    if hit.event_id in dot_states else ""
                ),
                critical_policy=critical_policy,
                skill_final_multiplier=(
                    zankou_q_final.multiplier
                    if zankou_q_final is not None else 1.0
                ),
                skill_final_multiplier_basis=(
                    zankou_q_final.evidence_basis
                    if zankou_q_final is not None else ""
                ),
            ))
        return tuple(evidence)
