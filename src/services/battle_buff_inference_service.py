# 为战报分析提供保守的静态 Buff 推断。

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_buff_rule import BattleStaticBuffRule
from src.domain.battle_report import (
    BattleAnalysisHit, BattleBuffModifierEvidence, BattleInferredAction,
    BattleInferredBuffInterval, BattleTreatmentEvent,
)
from src.services.battle_buff_interval_support import BattleBuffIntervalSupportMixin
from src.services.battle_buff_semantic_service import (
    confirmed_buff_target_scope,
    render_buff_name,
    resolve_buff_calculation,
    source_effect_parameter,
)
from src.services.battle_buff_target_scope_service import (
    BattleBuffTargetScopeService,
)
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService, MITSUKI_GRADUAL_BUFF_IDENTITY,
)
from src.services.battle_character_skill_buff_service import BattleCharacterSkillBuffService
from src.services.battle_confirmed_awakening_buff_service import (
    BattleConfirmedAwakeningBuffService,
)
from src.services.battle_equipment_suit_service import BattleEquipmentSuitService
from src.services.battle_fork_refinement_service import BattleForkRefinementService
from src.services.battle_target_control_policy_service import (
    BattleTargetControlPolicyService, CONTROL_CONFIRMED_ALL_BOSS,
)

BUFF_INFERENCE_MODEL_VERSION = "battle-static-buff-v30"
@dataclass(frozen=True, slots=True)
class _SelectedEffect:
    character_id: int
    character_name: str
    effect_definition_id: str
    definition: Mapping[str, Any] | None = None


def _consume_formal_boss_requirement(
    intervals: Sequence[BattleInferredBuffInterval],
    target_control_policy: str,
) -> list[BattleInferredBuffInterval]:
    if target_control_policy != CONTROL_CONFIRMED_ALL_BOSS:
        return list(intervals)
    results = []
    for interval in intervals:
        changed = False
        modifiers = []
        for modifier in interval.modifiers:
            remaining = tuple(
                tag for tag in modifier.target_require_tags
                if tag.casefold() != "con_isboss"
            )
            changed |= remaining != modifier.target_require_tags
            modifiers.append(replace(modifier, target_require_tags=remaining))
        results.append(replace(
            interval,
            modifiers=tuple(modifiers),
            inference_basis=(
                interval.inference_basis
                + " 正式怪物目录已确认当前解析目标均为 Boss。"
                if changed else interval.inference_basis
            ),
        ))
    return results


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
            active_count = len({
                _geometry_id(shape_id)
                for item in equipment
                if str(item.get("kind") or "") == "module"
                for shape_id in (
                    item.get("graduation_assumed_shape_ids")
                    or (item.get("geometry"),)
                )
            }.intersection(required_shapes))
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
            input_kind = BattleBuffTargetScopeService.ability_input_kind(
                binding.get("input_id"), ability_id,
            )
            event_type = "STATIC_EQUIPPED_SOURCE" if passive else (
                f"ABILITY_EVENT|{input_kind}|{ability_id}|{binding.get('event_tag') or ''}"
            )
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
                target_scope=BattleBuffTargetScopeService.for_skill_binding(binding),
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
            confirmed = BattleConfirmedAwakeningBuffService.get(
                effect_definition_id
            )
            if confirmed is not None:
                rules.append(BattleStaticBuffRule(
                    rule_id=f"{effect_definition_id}:confirmed-adapter",
                    source_effect_definition_id=effect_definition_id,
                    source_kind="confirmed_character_text",
                    source_character_id=character_id,
                    source_character_name=character_name,
                    source_asset_path=f"combat-effect:{effect_definition_id}",
                    target_asset_path=f"confirmed:{effect_definition_id}",
                    target_name=confirmed.name,
                    target_scope=confirmed.scope,
                    event_type=confirmed.event_type,
                    effect_type="ADD",
                    duration_policy=(
                        "HasDuration"
                        if confirmed.duration_seconds
                        else "Equipped"
                    ),
                    duration_seconds=confirmed.duration_seconds,
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
                        for modifier_value in confirmed.modifier_values
                    ),
                    stacking_type=confirmed.stacking_type,
                    stack_limit_count=1,
                ))
            if BattleConfirmedAwakeningBuffService.replaces_generic(
                effect_definition_id
            ):
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
                    inferred_scope = BattleBuffTargetScopeService.for_trigger(
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
        treatment_events: Sequence[BattleTreatmentEvent] = (),
        critical_events: Sequence[Any] = (),
        target_control_policy: str = "eligible_default",
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
                control_policy_basis = ""
                if BattleTargetControlPolicyService.is_mofeikesi_control_requirement(
                    rule.application_requirement_asset_path
                ):
                    succeeds, control_policy_basis = (
                        BattleTargetControlPolicyService.default_control_succeeds(
                            target_control_policy
                        )
                    )
                    if not succeeds:
                        rule = replace(rule, target_scope="unknown")
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
                        inference_basis=(
                            cls._basis(rule, occurrence)
                            + (
                                f" {control_policy_basis}"
                                if control_policy_basis
                                else ""
                            )
                        ),
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
            treatment_events=treatment_events,
            critical_events=critical_events,
        ))
        intervals = _consume_formal_boss_requirement(
            intervals,
            target_control_policy,
        )
        base_mofeikesi = tuple(
            row
            for row in intervals
            if "mofeikesi-q-team-attack" in row.buff_asset_path.casefold()
        )
        intervals = [
            replace(
                row,
                end_us=min(
                    row.end_us,
                    next(
                        (
                            base.end_us
                            for base in base_mofeikesi
                            if base.source_character_id == row.source_character_id
                            and base.start_us <= row.start_us < base.end_us
                        ),
                        row.end_us,
                    ),
                ),
            )
            if "mofeikesi-controlled-extra" in row.buff_asset_path.casefold()
            else row
            for row in intervals
        ]
        return tuple(sorted(
            intervals,
            key=lambda row: (
                row.start_us,
                row.end_us,
                row.source_character_id,
                row.buff_asset_path,
            ),
        ))
