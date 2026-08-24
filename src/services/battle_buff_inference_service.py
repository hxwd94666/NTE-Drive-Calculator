# 为战报分析提供保守的静态 Buff 推断。

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit, BattleBuffModifierEvidence, BattleInferredAction,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_interval_support import BattleBuffIntervalSupportMixin
from src.services.battle_buff_semantic_service import (
    confirmed_buff_target_scope,
    render_buff_name,
    resolve_buff_calculation,
    source_effect_parameter,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService, MITSUKI_GRADUAL_BUFF_IDENTITY,
)
from src.services.battle_character_skill_buff_service import BattleCharacterSkillBuffService
from src.services.battle_character_awakening_hit_service import (
    FADIA_GODSLAYER_REQUIREMENT,
    MITSUKI_ULTRA_REQUIREMENT,
    ZERO_FIRST_GAZE_REQUIREMENT,
)
from src.services.battle_equipment_suit_service import BattleEquipmentSuitService
from src.services.battle_fork_refinement_service import BattleForkRefinementService

BUFF_INFERENCE_MODEL_VERSION = "battle-static-buff-v22"
_CONFIRMED_REPLACES_GENERIC = frozenset({
    "character_awaken:1036:Effect5", "character_awaken:1039:resonance_6",
    "character_awaken:1070:Effect5", "character_awaken:1003:Effect4",
})


@dataclass(frozen=True, slots=True)
class BattleStaticBuffRule:
    rule_id: str
    source_effect_definition_id: str
    source_kind: str
    source_character_id: int
    source_character_name: str
    source_asset_path: str
    target_asset_path: str
    target_name: str
    target_scope: str
    event_type: str
    effect_type: str
    duration_policy: str
    duration_seconds: float | None
    stack_count: int
    modifiers: tuple[BattleBuffModifierEvidence, ...]
    stacking_type: str = ""
    stack_limit_count: int = 1
    cooldown_seconds: float | None = None
    application_requirement_asset_path: str = ""


@dataclass(frozen=True, slots=True)
class _SelectedEffect:
    character_id: int
    character_name: str
    effect_definition_id: str
    definition: Mapping[str, Any] | None = None


def _canonicalize_shared_buff_rule(rule: BattleStaticBuffRule) -> BattleStaticBuffRule:
    if (
        rule.source_effect_definition_id == "character_awaken:1070:Effect6"
        and "mitsuki070_passive2_atkup_level6"
        in rule.target_asset_path.casefold()
    ):
        return replace(
            rule,
            target_asset_path=MITSUKI_GRADUAL_BUFF_IDENTITY,
            target_name="渐强",
            stack_limit_count=20,
        )
    return rule


def _scalable_value(
    static_dao: Any,
    scalable: Mapping[str, Any] | None,
    source_definition: Mapping[str, Any] | None,
) -> float | None:
    if not isinstance(scalable, Mapping):
        return None
    multiplier = scalable.get("Value", 1.0)
    coefficient = float(multiplier) if isinstance(multiplier, (int, float)) else 1.0
    curve = scalable.get("Curve")
    row_name = str(curve.get("RowName") or "") if isinstance(curve, Mapping) else ""
    if row_name and row_name.casefold() != "none":
        curve_table = curve.get("CurveTable") if isinstance(curve, Mapping) else None
        table_path = (
            str(curve_table.get("ObjectPath") or "").split(".", 1)[0]
            if isinstance(curve_table, Mapping)
            else ""
        )
        static_curve = (
            static_dao.get_combat_curve(table_path, row_name)
            if table_path and hasattr(static_dao, "get_combat_curve")
            else static_dao.get_equipment_buff_curve(row_name)
        )
        points = (static_curve or {}).get("points") or ()
        if len(points) == 1 and isinstance(points[0].get("value"), (int, float)):
            return coefficient * float(points[0]["value"])
        source_value = source_effect_parameter(source_definition, row_name)
        return None if source_value is None else coefficient * source_value
    return coefficient if isinstance(multiplier, (int, float)) else None


def _duration_seconds(
    static_dao: Any,
    definition: Mapping[str, Any],
    source_definition: Mapping[str, Any] | None,
) -> float | None:
    magnitude = definition.get("duration_magnitude")
    if not isinstance(magnitude, Mapping):
        return None
    value = _scalable_value(
        static_dao,
        magnitude.get("ScalableFloatMagnitude"),
        source_definition,
    )
    if value is None:
        direct = magnitude.get("Value")
        value = float(direct) if isinstance(direct, (int, float)) else None
    if value is not None and value > 0:
        return value
    return None


def _modifier_rows(
    static_dao: Any,
    definition: Mapping[str, Any],
    source_definition: Mapping[str, Any] | None,
) -> tuple[BattleBuffModifierEvidence, ...]:
    result = []
    for row in definition.get("modifiers") or ():
        property_id = str(row.get("property_id") or "").strip()
        if not property_id:
            continue
        calculation = str(row.get("calculation_asset_path") or "").strip()
        magnitude = row.get("magnitude")
        scalable = (
            magnitude.get("ScalableFloatMagnitude")
            if isinstance(magnitude, Mapping)
            else None
        )
        resolution = resolve_buff_calculation(calculation, source_definition)
        direct_value = resolution.value
        if direct_value is None and not calculation:
            direct_value = _scalable_value(
                static_dao,
                scalable,
                source_definition,
            )
        if direct_value is None and not calculation and not scalable:
            value = row.get("magnitude_value")
            direct_value = float(value) if isinstance(value, (int, float)) else None
        result.append(BattleBuffModifierEvidence(
            property_id=property_id,
            modifier_operation=str(row.get("modifier_operation") or "unknown"),
            magnitude_kind=str(row.get("magnitude_kind") or "unknown"),
            magnitude_value=direct_value,
            calculation_asset_path=calculation,
            value_confidence=(
                resolution.confidence if calculation
                else "中" if direct_value is not None
                else "低"
            ),
            modifier_group_ordinal=int(row.get("modifier_group_ordinal") or 0),
            application_requirement_asset_path=str(
                row.get("application_requirement_asset_path") or ""
            ),
            source_require_tags=tuple(row.get("source_require_tags") or ()),
            source_ignore_tags=tuple(row.get("source_ignore_tags") or ()),
            target_require_tags=tuple(row.get("target_require_tags") or ()),
            target_ignore_tags=tuple(row.get("target_ignore_tags") or ()),
        ))
    return tuple(result)


def _target_scope(
    trigger: Mapping[str, Any],
    source_definition: Mapping[str, Any] | None,
) -> str:
    event_type = str(trigger.get("event_type") or "").casefold()
    if "all_player" in event_type:
        return "team"
    description = str(
        (source_definition or {}).get("description_zh") or ""
    )
    if "全队角色获得" in description or "全队角色提升" in description:
        return "team"
    if bool(trigger.get("target_trigger")):
        return "target"
    if bool(trigger.get("by_self")):
        return "self"
    return "unknown"


def _definition_value_confidence(rule: BattleStaticBuffRule) -> str:
    if not rule.modifiers:
        return "未解析"
    levels = {row.value_confidence for row in rule.modifiers}
    if levels == {"高"}:
        return "高"
    if not levels.difference({"高", "中"}):
        return "中"
    return "低"


def _geometry_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized.casefold() == "core":
        return ""
    if normalized.startswith("EquipmentGeometry_"):
        return normalized
    return f"EquipmentGeometry_{normalized}"


def _selected_effects(
    static_dao: Any,
    build: Mapping[str, Any] | None,
) -> tuple[_SelectedEffect, ...]:
    if not build:
        return ()
    forks = {
        str(row.get("fork_id")): str(row.get("star_pack_id") or "")
        for row in static_dao.list_forks()
    }
    selected: list[_SelectedEffect] = []

    def effect_definition(
        owner_kind: str,
        owner_id: str,
        effect_definition_id: str,
    ) -> Mapping[str, Any] | None:
        return next((
            row for row in static_dao.list_combat_effect_definitions(
                owner_kind=owner_kind,
                owner_id=owner_id,
            )
            if str(row.get("effect_definition_id") or "") == effect_definition_id
        ), None)

    for character in build.get("characters") or ():
        character_id = int(character["character_id"])
        character_name = str(
            character.get("observed_name") or character_id
        )
        fork_id = str(character.get("fork_id") or "").strip()
        refinement = int(character.get("fork_refinement_level") or 0)
        star_pack_id = forks.get(fork_id, "")
        if star_pack_id and refinement > 0:
            effect_id = f"fork_star:{star_pack_id}:{refinement}"
            selected.append(_SelectedEffect(
                character_id,
                character_name,
                effect_id,
                effect_definition("fork_star", star_pack_id, effect_id),
            ))
        profile = character.get("profile") or {}
        awakenings = (
            profile.get("selected_awaken_effect_ids")
            if isinstance(profile, Mapping)
            else ()
        ) or ()
        resonance_effects = []
        selection_initialized = (
            bool(profile.get("awakening_selection_initialized"))
            if isinstance(profile, Mapping)
            else False
        )
        awakening_level = (
            len(awakenings)
            if selection_initialized
            else int(
                character.get("awakening_level")
                or (
                    profile.get("awakening_level")
                    if isinstance(profile, Mapping)
                    else 0
                )
                or 0
            )
        )
        if awakening_level >= 3:
            resonance_effects.append("resonance_3")
        if awakening_level >= 6:
            resonance_effects.append("resonance_6")
        for effect_id in (*awakenings, *resonance_effects):
            definition_id = f"character_awaken:{character_id}:{effect_id}"
            selected.append(_SelectedEffect(
                character_id,
                character_name,
                definition_id,
                effect_definition(
                    "character_awaken",
                    f"{character_id}:{effect_id}",
                    definition_id,
                ),
            ))
        equipment = tuple(character.get("equipment") or ())
        core_suit_ids = {
            str(item.get("suit_id") or "").strip()
            for item in equipment
            if str(item.get("kind") or "") == "core"
            and str(item.get("suit_id") or "").strip()
        }
        for suit_id in sorted(core_suit_ids):
            suit = static_dao.get_suit(suit_id)
            required_shapes = {
                _geometry_id(shape_id)
                for shape_id in (suit or {}).get("required_shape_ids") or ()
            }
            active_count = sum(
                str(item.get("kind") or "") == "module"
                and _geometry_id(item.get("geometry")) in required_shapes
                for item in equipment
            )
            definitions = static_dao.list_combat_effect_definitions(
                owner_kind="equipment_suit",
                owner_id=suit_id,
            )
            for definition in definitions:
                parameters = definition.get("parameters") or {}
                required = int(parameters.get("required_count") or 0)
                if required > 0 and active_count >= required:
                    selected.append(_SelectedEffect(
                        character_id,
                        character_name,
                        str(definition["effect_definition_id"]),
                        definition,
                    ))
    deduplicated: dict[tuple[int, str], _SelectedEffect] = {}
    for row in selected:
        deduplicated[(row.character_id, row.effect_definition_id)] = row
    return tuple(deduplicated.values())


def _ability_input_kind(input_id: Any, ability_id: str) -> str:
    value = str(input_id or "")
    if "UltraSkill" in value:
        return "Q"
    if "GSkill" in value:
        return "G"
    if value.endswith("_Skill"):
        return "E"
    if "Melee" in value:
        return "A"
    if "QTE" in ability_id:
        return "QTE"
    if "PerfectEvade" in ability_id:
        return "PERFECT_EVADE"
    return "UNKNOWN"


def _skill_rules(static_dao: Any, build: Mapping[str, Any] | None) -> list[BattleStaticBuffRule]:
    rules: list[BattleStaticBuffRule] = []
    specialized_targets = frozenset({
        "/game/blueprints/abilities/player/ability_019_mint/upgrade/level5/"
        "buff_mint019_level5_1",
    })
    for character in (build or {}).get("characters") or ():
        character_id = int(character["character_id"])
        character_name = str(character.get("observed_name") or character_id)
        for binding in static_dao.list_character_bound_modifier_effects(character_id):
            ability_id = str(binding.get("ability_id") or "")
            ability_path = str(binding.get("ability_asset_path") or "")
            target_path = str(binding.get("effect_asset_path") or "")
            if (
                target_path.casefold() in specialized_targets
                or BattleCharacterSkillBuffService.owns_target(target_path)
            ):
                continue
            effect_id = str(binding.get("effect_id") or "")
            target = static_dao.get_buff_definition(target_path)
            if target is None:
                continue
            modifiers = _modifier_rows(static_dao, target, None)
            if not modifiers:
                continue
            passive = binding.get("binding_kind") == "passive_buff"
            input_kind = _ability_input_kind(binding.get("input_id"), ability_id)
            event_type = "STATIC_EQUIPPED_SOURCE" if passive else (
                f"ABILITY_EVENT|{input_kind}|{ability_id}|{binding.get('event_tag') or ''}"
            )
            target_type = str(binding.get("target_type_asset_path") or "")
            rules.append(BattleStaticBuffRule(
                rule_id=f"character-skill:{character_id}:{ability_id}:{effect_id}",
                source_effect_definition_id=f"character_skill:{character_id}:{ability_id}",
                source_kind="skill_effect",
                source_character_id=character_id,
                source_character_name=character_name,
                source_asset_path=ability_path,
                target_asset_path=target_path,
                target_name=render_buff_name(
                    str(target.get("definition_id") or effect_id or target_path),
                    str(target.get("definition_id") or effect_id or target_path),
                ),
                target_scope="target" if "enemy" in target_type.casefold() else "self",
                event_type=event_type,
                effect_type="ADD",
                duration_policy=str(target.get("duration_policy") or ""),
                duration_seconds=_duration_seconds(static_dao, target, None),
                stack_count=1,
                modifiers=modifiers,
                stacking_type=str(target.get("stacking_type") or ""),
                stack_limit_count=max(1, int(target.get("stack_limit_count") or 1)),
            ))
    return rules


class BattleBuffInferenceService(BattleBuffIntervalSupportMixin):
    @classmethod
    def load_rules(
        cls,
        static_dao: Any,
        build: Mapping[str, Any] | None,
    ) -> tuple[BattleStaticBuffRule, ...]:
        definition_cache: dict[str, dict[str, Any] | None] = {}

        def definition(asset_path: str) -> dict[str, Any] | None:
            if asset_path not in definition_cache:
                definition_cache[asset_path] = static_dao.get_buff_definition(
                    asset_path
                )
            return definition_cache[asset_path]

        selected_effects = _selected_effects(static_dao, build)
        rules: list[BattleStaticBuffRule] = list(
            BattleEquipmentSuitService.load_rules(
                static_dao,
                selected_effects,
                BattleStaticBuffRule,
            )
        )
        for selected in selected_effects:
            character_id = selected.character_id
            character_name = selected.character_name
            effect_definition_id = selected.effect_definition_id
            if effect_definition_id.startswith("equipment_suit:"):
                continue
            if BattleForkRefinementService.owns_effect(effect_definition_id):
                rules.extend(BattleForkRefinementService.rules_for_selected_effect(
                    selected,
                    BattleStaticBuffRule,
                ))
                continue
            raw_parameters = (selected.definition or {}).get("parameters") or {}
            parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}
            confirmed = {
                "character_awaken:1046:Effect1": (
                    "初明凝视：铭隙鉴刻额外伤害（觉醒一）",
                    "self", "STATIC_EQUIPPED_SOURCE", None,
                    (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
                ),
                "character_awaken:1051:Effect1": (
                    "初明凝视：铭隙鉴刻额外伤害（觉醒一）",
                    "self", "STATIC_EQUIPPED_SOURCE", None,
                    (("DefIgnore", 0.75, ZERO_FIRST_GAZE_REQUIREMENT),),
                ),
                "character_awaken:1036:Effect1": (
                    "狩（觉醒一）", "self", "STATIC_EQUIPPED_SOURCE", None,
                    (("DamageUpGeneralBase", 0.40),),
                ),
                "character_awaken:1036:Effect5": (
                    "花开见血（觉醒五）", "team", "STATIC_EQUIPPED_SOURCE", None,
                    (("ToppleDamageUp", 3.00),),
                ),
                "character_awaken:1036:resonance_6": (
                    "鸩火灼心（六觉共鸣）", "self",
                    "EBuffEventType::BUFF_EVENT_SKILL_AFTER_DAMAGE", 20.0,
                    (("AtkUp", 0.40),),
                ),
                "character_awaken:1004:Effect2": (
                    "闹钟响彻四方（觉醒二）", "self",
                    "EBuffEventType::BUFF_EVENT_QTE_BEGIN", 15.0,
                    (("DamageUpGeneralBase", 0.15),),
                ),
                "character_awaken:1019:Effect5": (
                    "第一直觉（觉醒五）", "self",
                    (
                        "PASSIVE_HIT|GE_Player_Mint_Skill1_Damage_New,"
                        "GE_Player_Mint_Skill1_Damage_Test1"
                    ),
                    6.0,
                    (("CritDamageBase", 0.25),),
                ),
                "character_awaken:1039:Effect3": (
                    "诅咒祝福之人（觉醒三）", "self",
                    "STATIC_EQUIPPED_SOURCE", None, (("HPMaxUp", 0.30),),
                ),
                "character_awaken:1039:Effect5": (
                    "敌神者暴击提升（觉醒五）", "self",
                    "EBuffEventType::BUFF_EVENT_Q_SKILL_BEGIN", 5.0,
                    (("CritBase", 0.50, FADIA_GODSLAYER_REQUIREMENT),),
                ),
                "character_awaken:1039:resonance_6": (
                    "归一的圣洁之人（六觉共鸣）", "team",
                    "STATIC_EQUIPPED_SOURCE", None, (("HPMaxUp", 0.10),),
                ),
                "character_awaken:1070:Effect5": (
                    "华彩乐章（觉醒五）", "self", "STATIC_EQUIPPED_SOURCE", None,
                    (("CritBase", 0.15, MITSUKI_ULTRA_REQUIREMENT),),
                ),
            }.get(effect_definition_id)
            if confirmed is not None:
                name, scope, event_type, duration, modifier_values = confirmed
                rules.append(BattleStaticBuffRule(
                    rule_id=f"{effect_definition_id}:confirmed-adapter",
                    source_effect_definition_id=effect_definition_id,
                    source_kind="confirmed_character_text",
                    source_character_id=character_id,
                    source_character_name=character_name,
                    source_asset_path=f"combat-effect:{effect_definition_id}",
                    target_asset_path=f"confirmed:{effect_definition_id}",
                    target_name=name,
                    target_scope=scope,
                    event_type=event_type,
                    effect_type="ADD",
                    duration_policy=("HasDuration" if duration else "Equipped"),
                    duration_seconds=duration,
                    stack_count=1,
                    modifiers=tuple(
                        BattleBuffModifierEvidence(
                            property_id=str(modifier_value[0]),
                            modifier_operation="EGameplayModOp::Additive",
                            magnitude_kind="confirmed_text",
                            magnitude_value=float(modifier_value[1]),
                            calculation_asset_path="",
                            value_confidence="高",
                            application_requirement_asset_path=(
                                str(modifier_value[2])
                                if len(modifier_value) > 2
                                else ""
                            ),
                        )
                        for modifier_value in modifier_values
                    ),
                    stacking_type="AggregateByTarget",
                    stack_limit_count=1,
                ))
            if effect_definition_id in _CONFIRMED_REPLACES_GENERIC:
                continue
            modify_pack_id = str(parameters.get("modify_pack_id") or "").strip()
            if modify_pack_id and modify_pack_id.casefold() != "none":
                pack = static_dao.get_equipment_modify_pack(modify_pack_id)
                modifiers = tuple(
                    BattleBuffModifierEvidence(
                        property_id=str(row.get("property_id") or ""),
                        modifier_operation=str(row.get("operation") or "unknown"),
                        magnitude_kind="constant",
                        magnitude_value=float(row.get("value") or 0.0),
                        calculation_asset_path="",
                        value_confidence="高",
                    )
                    for row in (pack or {}).get("modifiers") or ()
                    if str(row.get("property_id") or "").strip()
                )
                if modifiers:
                    description = str(
                        (selected.definition or {}).get("description_zh")
                        or modify_pack_id
                    )
                    rules.append(BattleStaticBuffRule(
                        rule_id=f"{effect_definition_id}:modify-pack",
                        source_effect_definition_id=effect_definition_id,
                        source_kind="modify_pack",
                        source_character_id=character_id,
                        source_character_name=character_name,
                        source_asset_path=f"combat-effect:{effect_definition_id}",
                        target_asset_path=f"modify-pack:{modify_pack_id}",
                        target_name=description,
                        target_scope="self",
                        event_type="STATIC_EQUIPPED_SOURCE",
                        effect_type="ADD",
                        duration_policy="Equipped",
                        duration_seconds=None,
                        stack_count=1,
                        modifiers=modifiers,
                    ))
            links = static_dao.list_combat_effect_buff_links(
                effect_definition_id
            )
            for link_ordinal, link in enumerate(links):
                if not bool(link.get("target_available")):
                    continue
                source_path = str(link["target_asset_path"])
                source = definition(source_path)
                if source is None:
                    continue
                source_modifiers = _modifier_rows(
                    static_dao,
                    source,
                    selected.definition,
                )
                if source_modifiers:
                    rules.append(BattleStaticBuffRule(
                        rule_id=f"{effect_definition_id}:{link_ordinal}:source",
                        source_effect_definition_id=effect_definition_id,
                        source_kind=str(link.get("link_kind") or "unknown"),
                        source_character_id=character_id,
                        source_character_name=character_name,
                        source_asset_path=source_path,
                        target_asset_path=source_path,
                        target_name=render_buff_name(
                            str(source.get("definition_id") or ""),
                            str(source.get("definition_id") or source_path),
                        ),
                        target_scope="self",
                        event_type="STATIC_EQUIPPED_SOURCE",
                        effect_type="ADD",
                        duration_policy="Equipped",
                        duration_seconds=None,
                        stack_count=1,
                        modifiers=source_modifiers,
                        stacking_type=str(source.get("stacking_type") or ""),
                        stack_limit_count=max(
                            1, int(source.get("stack_limit_count") or 1)
                        ),
                    ))
                for trigger_ordinal, trigger in enumerate(
                    source.get("triggers") or ()
                ):
                    target_path = str(
                        trigger.get("target_effect_asset_path") or ""
                    ).strip()
                    target = definition(target_path) if target_path else None
                    if target is None:
                        continue
                    stack = trigger.get("stack_count")
                    target_definition_id = str(
                        target.get("definition_id") or target_path
                    )
                    inferred_scope = _target_scope(
                        trigger,
                        selected.definition,
                    )
                    rules.append(BattleStaticBuffRule(
                        rule_id=(
                            f"{effect_definition_id}:{link_ordinal}:"
                            f"trigger:{trigger_ordinal}"
                        ),
                        source_effect_definition_id=effect_definition_id,
                        source_kind=str(link.get("link_kind") or "unknown"),
                        source_character_id=character_id,
                        source_character_name=character_name,
                        source_asset_path=source_path,
                        target_asset_path=target_path,
                        target_name=render_buff_name(
                            target_definition_id,
                            target_definition_id,
                        ),
                        target_scope=confirmed_buff_target_scope(
                            target_definition_id,
                            inferred_scope,
                        ),
                        event_type=str(trigger.get("event_type") or "unknown"),
                        effect_type=str(trigger.get("effect_type") or "unknown"),
                        duration_policy=str(target.get("duration_policy") or ""),
                        duration_seconds=_duration_seconds(
                            static_dao,
                            target,
                            selected.definition,
                        ),
                        stack_count=(
                            int(stack) if isinstance(stack, int) and stack > 0 else 1
                        ),
                        modifiers=_modifier_rows(
                            static_dao,
                            target,
                            selected.definition,
                        ),
                        stacking_type=str(target.get("stacking_type") or ""),
                        stack_limit_count=max(
                            1, int(target.get("stack_limit_count") or 1)
                        ),
                        application_requirement_asset_path=str(
                            trigger.get("application_requirement_asset_path") or ""
                        ),
                    ))
        rules.extend(_skill_rules(static_dao, build))
        rules.extend(BattleCharacterSkillBuffService.load_rules(
            static_dao, build, BattleStaticBuffRule, BattleBuffModifierEvidence,
        ))
        rules.extend(BattleCharacterPassiveService.load_rules(
            build,
            BattleStaticBuffRule,
        ))
        return tuple(_canonicalize_shared_buff_rule(rule) for rule in rules)

    @classmethod
    def infer(
        cls,
        rules: Sequence[BattleStaticBuffRule],
        *,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        critical_events: Sequence[Any] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        grouped: dict[
            tuple[int, str, str],
            list[BattleStaticBuffRule],
        ] = {}
        for rule in rules:
            key = (
                rule.source_character_id,
                rule.source_effect_definition_id,
                rule.target_asset_path,
            )
            grouped.setdefault(key, []).append(rule)
        intervals: list[BattleInferredBuffInterval] = []
        ordinal = 0
        for rule_group in grouped.values():
            add_rules = [
                row for row in rule_group if "remove" not in row.effect_type.casefold()
            ]
            remove_occurrences = sorted(
                (
                    occurrence
                    for row in rule_group
                    if "remove" in row.effect_type.casefold()
                    for occurrence in cls._occurrences(
                        row,
                        actions=actions,
                        hits=hits,
                        battle_end_us=battle_end_us,
                        time_stop_intervals=time_stop_intervals,
                    )
                ),
                key=lambda row: row.time_us,
            )
            for rule in add_rules:
                occurrences = cls._occurrences(
                    rule,
                    actions=actions,
                    hits=hits,
                    battle_end_us=battle_end_us,
                    time_stop_intervals=time_stop_intervals,
                )
                for occurrence, end_us in cls._occurrence_ends(
                    rule,
                    occurrences,
                    remove_occurrences,
                    battle_end_us,
                    time_stop_intervals,
                ):
                    intervals.append(BattleInferredBuffInterval(
                        interval_id=f"buff:{ordinal}:{rule.rule_id}",
                        buff_asset_path=rule.target_asset_path,
                        buff_name=rule.target_name,
                        source_effect_definition_id=(
                            rule.source_effect_definition_id
                        ),
                        source_kind=rule.source_kind,
                        source_character_id=rule.source_character_id,
                        source_character_name=rule.source_character_name,
                        target_scope=rule.target_scope,
                        start_us=occurrence.time_us,
                        end_us=end_us,
                        stacks=rule.stack_count,
                        duration_policy=rule.duration_policy,
                        state_confidence=occurrence.state_confidence,
                        value_confidence=_definition_value_confidence(rule),
                        inference_basis=cls._basis(rule, occurrence),
                        trigger_event_type=rule.event_type,
                        evidence_action_ids=occurrence.action_ids,
                        evidence_event_ids=occurrence.event_ids,
                        modifiers=rule.modifiers,
                        stacking_type=rule.stacking_type,
                        stack_limit_count=rule.stack_limit_count,
                        target_id=occurrence.target_id,
                    ))
                    ordinal += 1
        intervals.extend(BattleForkRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
            critical_events=critical_events,
        ))
        return tuple(sorted(
            intervals,
            key=lambda row: (
                row.start_us,
                row.end_us,
                row.source_character_id,
                row.buff_asset_path,
            ),
        ))
