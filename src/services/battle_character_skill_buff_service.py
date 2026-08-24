# 依据正式曲线与冻结人物/弧盘基础攻击力生成角色技能的固定攻击 Buff。
"""Character-skill AtkAdd rules that require source-panel calculations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.damage_calculation_service import skill_tier_for_effective_level
from src.services.official_role_awakening_service import awaken_skill_level_delta


_SAGIRI_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_SagiriEffectFigure"
_HANIEL_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_HanielEffectFigure"
_SPECIALIZED_TARGETS = frozenset({
    "/game/blueprints/abilities/player/ability_020_haniel/buff/"
    "buff_haniel_skill_ultraskill",
    "/game/blueprints/abilities/player/ability_020_haniel/buff/"
    "buff_haniel_ultraskill_atkup",
})


def _curve_values(static_dao: Any, table: str, curve_id: str) -> tuple[float, ...]:
    curve = static_dao.get_combat_curve(table, curve_id)
    values = tuple(float(row["value"]) for row in (curve or {}).get("points") or ())
    if not values:
        raise ValueError(f"角色技能曲线缺失：{curve_id}")
    return values


def _constant_curve(static_dao: Any, table: str, curve_id: str) -> float:
    values = _curve_values(static_dao, table, curve_id)
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f"角色技能曲线不是常数：{curve_id}")
    return values[0]


def _character(build: Mapping[str, Any] | None, character_id: int) -> Mapping[str, Any] | None:
    return next((
        row for row in (build or {}).get("characters") or ()
        if int(row.get("character_id") or 0) == character_id
    ), None)


def _base_attack(character: Mapping[str, Any]) -> float | None:
    values = tuple(
        float(row.get("value") or 0.0)
        for row in character.get("stats") or ()
        if str(row.get("property_id") or "") == "AtkBase"
        and str(row.get("source_group") or "") in {"character", "fork"}
    )
    return sum(values) if values else None


def _effect_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile") or {}
    selected = {
        str(value).casefold()
        for value in profile.get("selected_awaken_effect_ids") or ()
    } if isinstance(profile, Mapping) else set()
    if bool(profile.get("awakening_selection_initialized")):
        return effect_id.casefold() in selected
    ordinal = int(effect_id.removeprefix("Effect") or 0)
    return int(character.get("awakening_level") or profile.get("awakening_level") or 0) >= ordinal


def _effective_level(
    static_dao: Any,
    character: Mapping[str, Any],
    ability_id: str,
) -> int:
    profile = dict(character.get("profile") or {})
    levels = {
        str(row.get("skill_id") or ""): int(row.get("skill_level") or 1)
        for row in character.get("skills") or ()
    }
    base = levels.get(ability_id, int((profile.get("skill_levels") or {}).get(ability_id) or 1))
    profile.setdefault("awakening_level", int(character.get("awakening_level") or 0))
    effects = tuple(static_dao.list_character_awaken_effects(int(character["character_id"])))
    return base + awaken_skill_level_delta(profile, effects, ability_id)


def _modifier(modifier_type: Any, value: float | None, calculation: str) -> Any:
    return modifier_type(
        property_id="AtkAdd",
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="formal_source_atk_base_calculation",
        magnitude_value=value,
        calculation_asset_path=calculation,
        value_confidence="高" if value is not None else "低",
    )


def _rule(
    rule_type: Any,
    modifier_type: Any,
    *,
    character: Mapping[str, Any],
    rule_id: str,
    effect_definition_id: str,
    asset_path: str,
    name: str,
    event_type: str,
    duration: float,
    value: float | None,
    calculation: str,
) -> Any:
    character_id = int(character["character_id"])
    return rule_type(
        rule_id=rule_id,
        source_effect_definition_id=effect_definition_id,
        source_kind="formal_character_skill",
        source_character_id=character_id,
        source_character_name=str(character.get("observed_name") or character_id),
        source_asset_path=asset_path,
        target_asset_path=asset_path,
        target_name=name,
        target_scope="team_others" if character_id == 1003 else "team",
        event_type=event_type,
        effect_type="ADD",
        duration_policy="HasDuration",
        duration_seconds=duration,
        stack_count=1,
        modifiers=(_modifier(modifier_type, value, calculation),),
        stacking_type="AggregateByTarget",
        stack_limit_count=1,
    )


class BattleCharacterSkillBuffService:
    """Build exact source-AtkBase character skill Buff rules."""

    @staticmethod
    def owns_target(asset_path: str) -> bool:
        return asset_path.casefold() in _SPECIALIZED_TARGETS

    @classmethod
    def load_rules(
        cls,
        static_dao: Any,
        build: Mapping[str, Any] | None,
        rule_type: Any,
        modifier_type: Any,
    ) -> tuple[Any, ...]:
        rules = []
        sagiri = _character(build, 1003)
        sagiri_base_attack = _base_attack(sagiri) if sagiri is not None else None
        if sagiri is not None and sagiri_base_attack is not None:
            base_attack = sagiri_base_attack
            q_ratio = _constant_curve(static_dao, _SAGIRI_TABLE, "Sagiri_QDamUp_AtkAdd")
            q_duration = _constant_curve(
                static_dao, _SAGIRI_TABLE, "Sagiri_QDamUp_AtkAddDur"
            )
            common = dict(
                character=sagiri,
                event_type="ABILITY_EVENT_AFTER_END|Q|GA_Sagiri_UltraSkill",
                duration=q_duration,
                value=None if base_attack is None else base_attack * q_ratio,
            )
            rules.append(_rule(
                rule_type, modifier_type,
                rule_id="character-skill:1003:q-team-atk",
                effect_definition_id="character_skill:1003:GA_Sagiri_UltraSkill",
                asset_path=(
                    "/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/Buff/"
                    "Buff_Sagiri003_QDamUp"
                ),
                name="千钧隆重的饕餮宴：队友攻击力",
                calculation=(
                    "/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/Buff/"
                    "Cau_Sagiri_QDamUp"
                ),
                **common,
            ))
            if _effect_enabled(sagiri, "Effect4"):
                rules.append(_rule(
                    rule_type, modifier_type,
                    rule_id="character-awaken:1003:effect4-team-atk",
                    effect_definition_id="character_awaken:1003:Effect4",
                    asset_path=(
                        "/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/"
                        "Upgrade/Level4/Buff_Sagiri003_Level4_1"
                    ),
                    name="祈愿性的依归（觉醒四）",
                    calculation=(
                        "/Game/Blueprints/Abilities/Player/Ability_003_Sagiri/"
                        "Upgrade/Level4/Cau_Sagiri_Level4"
                    ),
                    **common,
                ))

        haniel = _character(build, 1020)
        haniel_base_attack = _base_attack(haniel) if haniel is not None else None
        if haniel is not None and haniel_base_attack is not None:
            base_attack = haniel_base_attack
            definitions = (
                (
                    "E", "GA_Haniel_Skill", "Haniel_Skill_AtkAdd",
                    "Haniel_Skill_ActorDuaration", 0.719,
                    "Buff_Haniel_Skill_Atkup", "Cau_Haniel_AtkUp", "咕咕子：全队攻击力",
                ),
                (
                    "Q", "GA_Haniel_UltraSkill", "Haniel_UltraSkill_AtkAdd",
                    "Haniel_UltraSkillDuaration", 2.867,
                    "Buff_Haniel_UltraSkill_Atkup", "Cau_Haniel_AtkUp1",
                    "超异科王牌：全队攻击力",
                ),
            )
            for definition in definitions:
                input_kind, ability_id, ratio_curve, duration_curve = definition[:4]
                offset, buff, calc, name = definition[4:]
                ratios = _curve_values(static_dao, _HANIEL_TABLE, ratio_curve)
                level = _effective_level(static_dao, haniel, ability_id)
                ratio = ratios[skill_tier_for_effective_level(level, len(ratios))]
                duration = _constant_curve(static_dao, _HANIEL_TABLE, duration_curve)
                if input_kind == "E" and _effect_enabled(haniel, "Effect2"):
                    duration += 4.0
                root = "/Game/Blueprints/Abilities/Player/Ability_020_haniel/Buff/"
                rules.append(_rule(
                    rule_type, modifier_type,
                    character=haniel,
                    rule_id=f"character-skill:1020:{input_kind.casefold()}-team-atk",
                    effect_definition_id=f"character_skill:1020:{ability_id}",
                    asset_path=f"{root}{buff}",
                    name=name,
                    event_type=f"ABILITY_EVENT_OFFSET|{input_kind}|{offset}|{ability_id}",
                    duration=duration,
                    value=None if base_attack is None else base_attack * ratio,
                    calculation=f"/Game/Blueprints/Abilities/Calculation/Haniel/{calc}",
                ))
        return tuple(rules)
