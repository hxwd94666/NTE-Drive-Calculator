# 把静态 GA、GE 与 Montage Notify 投影为纯动作推断可消费的不可变候选。
"""Prepare exact static animation evidence without leaking DAO access into domain logic."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
import re
from typing import Any, Literal

from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)


_AUXILIARY_SELECTOR_MARKERS = (
    "dissolve",
    "appear",
)


def _microseconds(value: Any) -> int:
    return max(0, round(float(value or 0.0) * 1_000_000))


def _is_auxiliary_selector(selector_key: str) -> bool:
    normalized = str(selector_key).strip().casefold()
    return (
        not normalized
        or any(marker in normalized for marker in _AUXILIARY_SELECTOR_MARKERS)
        or normalized == "loop"
        or normalized.endswith("pre")
        or "_pre" in normalized
    )


def _effect_ids_from_references(references: Sequence[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for reference in references:
        target = str(reference.get("target_asset_path") or "").strip()
        effect_id = target.rsplit("/", 1)[-1]
        if effect_id.casefold().startswith("ge_") and effect_id not in values:
            values.append(effect_id)
    return values


def _reference_effect_ids(static_dao: Any, graph: dict[str, Any]) -> tuple[str, ...]:
    references = tuple(graph.get("references") or ())
    values = _effect_ids_from_references(references)
    asset_reader = getattr(static_dao, "get_combat_blueprint_asset", None)
    if not callable(asset_reader):
        return tuple(values)
    visited: set[str] = set()
    for reference in references:
        target = str(reference.get("target_asset_path") or "").strip()
        target_key = target.casefold()
        if (
            not target
            or target_key in visited
            or "/actor/" not in target_key
        ):
            continue
        visited.add(target_key)
        child = asset_reader(target)
        if not isinstance(child, dict):
            continue
        for effect_id in _effect_ids_from_references(
            tuple(child.get("references") or ())
        ):
            if effect_id not in values:
                values.append(effect_id)
    return tuple(values)


def _fallback_event_effects(
    event_tag: str,
    reference_effect_ids: Sequence[str],
) -> tuple[str, ...]:
    match = re.search(r"\.(branch)\.(\d+)$", event_tag, flags=re.IGNORECASE)
    if match is None:
        return ()
    token = f"{match.group(1)}{match.group(2)}".casefold()
    return tuple(
        effect_id
        for effect_id in reference_effect_ids
        if token in re.sub(r"[^a-z0-9]", "", effect_id.casefold())
        and "damage" in effect_id.casefold()
    )


def _hold_trigger_targets(graph: dict[str, Any]) -> dict[str, int]:
    grouped: dict[str, dict[str, Any]] = defaultdict(dict)
    for item in graph.get("semantic_properties") or ():
        property_path = str(item.get("property_path") or "")
        parent_path = property_path.rsplit(".", 1)[0]
        property_name = str(item.get("property_name") or "")
        grouped[parent_path][property_name] = item.get("value")
    targets: dict[str, int] = {}
    for properties in grouped.values():
        target = str(properties.get("NextSectionName") or "").strip()
        hold_seconds = properties.get("HoldTimeTrigger")
        if target and isinstance(hold_seconds, (int, float)) and hold_seconds > 0:
            targets[target.casefold()] = _microseconds(hold_seconds)
    return targets


def _has_loop_section(sections: Sequence[dict[str, Any]]) -> bool:
    return any(
        "loop" in str(section.get("section_name") or "").casefold()
        for section in sections
    )


def _selector_section_window(
    montage: dict[str, Any],
    selector_key: str,
) -> tuple[int, int, tuple[dict[str, Any], ...], int | None]:
    """Return the section chain entered by one selector, in montage time."""

    sections = tuple(montage.get("sections") or ())
    duration_us = _microseconds(montage.get("duration_seconds"))
    by_name = {
        str(section.get("section_name") or "").strip().casefold(): section
        for section in sections
        if str(section.get("section_name") or "").strip()
    }
    first = by_name.get(str(selector_key).strip().casefold())
    if first is None:
        return 0, duration_us, sections, None

    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    current: dict[str, Any] | None = first
    while current is not None:
        current_name = str(current.get("section_name") or "").strip().casefold()
        if not current_name or current_name in visited:
            break
        visited.add(current_name)
        chain.append(current)
        next_name = str(current.get("next_section_name") or "").strip().casefold()
        current = by_name.get(next_name) if next_name else None

    start_us = _microseconds(first.get("start_seconds"))
    initial_end_us = _microseconds(first.get("end_seconds"))
    end_us = max(
        (int(_microseconds(section.get("end_seconds"))) for section in chain),
        default=duration_us,
    )
    return start_us, max(start_us, end_us), tuple(chain), initial_end_us


def _repeats_damage(effect_offsets: dict[str, list[int]]) -> bool:
    return any(
        len(set(offsets)) >= 5 and max(offsets) - min(offsets) >= 500_000
        for offsets in effect_offsets.values()
    )


def _branch_charge_prelude_us(
    montage_rows: Sequence[tuple[str, str, dict[str, Any]]],
) -> int:
    for selector_key, _asset_path, montage in montage_rows:
        if selector_key.casefold() != "branch":
            continue
        for section in montage.get("sections") or ():
            if str(section.get("section_name") or "").casefold() == "atk":
                return _microseconds(section.get("start_seconds"))
    return 0


class BattleAnimationWindowService:
    """Load a frozen static animation catalog for characters in one battle."""

    @staticmethod
    def load_candidates(
        static_dao: Any,
        *,
        character_ids: Sequence[int],
        ability_ids: Sequence[str] = (),
    ) -> tuple[BattleActionAnimationCandidate, ...]:
        requested_abilities = {
            str(ability_id).strip().casefold()
            for ability_id in ability_ids
            if str(ability_id).strip()
        }
        ability_bindings: dict[str, tuple[str, str]] = {}
        for character_id in dict.fromkeys(int(value) for value in character_ids):
            for binding in static_dao.list_character_combat_bindings(character_id):
                ability_id = str(binding.get("ability_id") or "").strip()
                asset_path = str(binding.get("ability_asset_path") or "").strip()
                if not ability_id or not asset_path:
                    continue
                if (
                    requested_abilities
                    and ability_id.casefold() not in requested_abilities
                ):
                    continue
                ability_bindings.setdefault(
                    asset_path.casefold(),
                    (ability_id, asset_path),
                )

        candidates: list[BattleActionAnimationCandidate] = []
        loaded_montages: dict[str, dict[str, Any] | None] = {}
        for ability_id, ability_asset_path in ability_bindings.values():
            graph = static_dao.get_combat_ability_graph(ability_asset_path)
            if not graph:
                continue
            effects_by_event: dict[str, list[str]] = defaultdict(list)
            for effect in graph.get("effects") or ():
                event_tag = str(effect.get("event_tag") or "").strip().casefold()
                effect_id = str(effect.get("effect_id") or "").strip()
                if event_tag and effect_id and effect_id not in effects_by_event[event_tag]:
                    effects_by_event[event_tag].append(effect_id)

            montage_rows: list[tuple[str, str, dict[str, Any]]] = []
            for binding in graph.get("montages") or ():
                selector_key = str(binding.get("selector_key") or "").strip()
                if _is_auxiliary_selector(selector_key):
                    continue
                montage_asset_path = str(
                    binding.get("montage_asset_path") or ""
                ).strip()
                if not montage_asset_path:
                    continue
                montage_key = montage_asset_path.casefold()
                if montage_key not in loaded_montages:
                    loaded_montages[montage_key] = static_dao.get_combat_montage(
                        montage_asset_path
                    )
                montage = loaded_montages[montage_key]
                if not montage:
                    continue
                montage_rows.append((selector_key, montage_asset_path, montage))

            explicit_hold_duration_us = max(
                (
                    _microseconds(montage.get("duration_seconds"))
                    for selector_key, _asset_path, montage in montage_rows
                    if "hold" in selector_key.casefold()
                ),
                default=0,
            )
            hold_trigger_targets = _hold_trigger_targets(graph)
            branch_prelude_us = _branch_charge_prelude_us(montage_rows)
            reference_effect_ids = _reference_effect_ids(static_dao, graph)

            for selector_key, montage_asset_path, montage in montage_rows:
                (
                    selector_start_us,
                    selector_end_us,
                    selector_sections,
                    selector_initial_end_us,
                ) = _selector_section_window(montage, selector_key)
                effect_offsets: dict[str, list[int]] = defaultdict(list)
                trigger_ends: list[int] = []
                end_events: list[int] = []
                for notify in montage.get("notifies") or ():
                    absolute_start_us = _microseconds(notify.get("start_seconds"))
                    if not selector_start_us <= absolute_start_us <= selector_end_us:
                        continue
                    start_us = absolute_start_us - selector_start_us
                    notify_name = str(notify.get("notify_name") or "").casefold()
                    event_tag = str(notify.get("event_tag") or "").strip()
                    event_key = event_tag.casefold()
                    event_effects = tuple(effects_by_event.get(event_key, ()))
                    if not event_effects:
                        event_effects = _fallback_event_effects(
                            event_tag,
                            reference_effect_ids,
                        )
                    for effect_id in event_effects:
                        effect_offsets[effect_id].append(start_us)
                    if "triggerendabilityeffect" in notify_name:
                        trigger_ends.append(start_us)
                    if "endabilityskill" in event_key:
                        end_events.append(start_us)
                if not effect_offsets:
                    continue
                normalized_selector = selector_key.casefold()
                has_effect_in_selector_section = (
                    selector_initial_end_us is not None
                    and any(
                        offset <= selector_initial_end_us - selector_start_us
                        for offsets in effect_offsets.values()
                        for offset in offsets
                    )
                )
                hold_damage_mode: Literal[
                    "none", "during_hold", "after_hold"
                ] = "none"
                hold_prelude_us = 0
                if normalized_selector in hold_trigger_targets:
                    hold_damage_mode = "after_hold"
                    hold_prelude_us = hold_trigger_targets[normalized_selector]
                elif "hold" in normalized_selector and has_effect_in_selector_section:
                    hold_damage_mode = "during_hold"
                elif _has_loop_section(selector_sections) or (
                    _repeats_damage(effect_offsets)
                    and (
                        "begin" in normalized_selector
                        or normalized_selector.startswith("branch")
                    )
                ):
                    hold_damage_mode = "during_hold"
                    if normalized_selector.startswith("branch"):
                        hold_prelude_us = branch_prelude_us
                elif (
                    explicit_hold_duration_us > 0
                    and normalized_selector.startswith("branch")
                ):
                    hold_damage_mode = "after_hold"
                    hold_prelude_us = explicit_hold_duration_us
                section_ends = tuple(
                    sorted(
                        {
                            _microseconds(section.get("end_seconds"))
                            - selector_start_us
                            for section in selector_sections
                        }
                    )
                )
                candidates.append(
                    BattleActionAnimationCandidate(
                        ability_id=ability_id,
                        selector_key=selector_key,
                        montage_asset_path=montage_asset_path,
                        effect_hit_offsets_us=tuple(
                            (
                                effect_id,
                                tuple(sorted(set(offsets))),
                            )
                            for effect_id, offsets in sorted(
                                effect_offsets.items(),
                                key=lambda item: item[0].casefold(),
                            )
                        ),
                        trigger_end_offsets_us=tuple(sorted(set(trigger_ends))),
                        end_event_offsets_us=tuple(sorted(set(end_events))),
                        section_end_offsets_us=section_ends,
                        duration_us=selector_end_us - selector_start_us,
                        hold_damage_mode=hold_damage_mode,
                        hold_prelude_us=hold_prelude_us,
                    )
                )
        return tuple(
            sorted(
                candidates,
                key=lambda item: (
                    item.ability_id.casefold(),
                    item.selector_key.casefold(),
                    item.montage_asset_path.casefold(),
                ),
            )
        )
