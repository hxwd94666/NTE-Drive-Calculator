"""Bridge inferred encounters into the shared per-instance target contract."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.domain.battle_report import BattleTargetCondition
from src.domain.battle_target import (
    BattleSelectedTargetProfile,
    BattleTargetInstanceResolution,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_target_observation_support import target_observation_aliases
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number <= 0.0 else number


def _legacy_single_target_condition(
    evidence: Mapping[str, object] | None,
    condition: BattleTargetCondition,
    static_database_path: Path | None,
) -> BattleTargetCondition | None:
    static_ids = tuple(dict.fromkeys(condition.selected_target_ids))
    if len(static_ids) != 1 or condition.environment_kind == "manual":
        return None
    hits = evidence.get("hits") if isinstance(evidence, Mapping) else ()
    outgoing = tuple(
        row
        for row in hits or ()
        if isinstance(row, Mapping)
        and str(row.get("direction") or "").casefold() == "outgoing"
        and str(row.get("target_id") or "").strip()
    )
    aliases = target_observation_aliases(outgoing)
    instances: dict[tuple[str, str], float] = {}
    for row in outgoing:
        half = str(row.get("abyss_half") or "").strip().casefold()
        raw_id = str(row.get("target_id") or "").strip()
        target_id = aliases.get((half, raw_id), raw_id)
        max_hp = _positive(row.get("target_max_hp"))
        if max_hp is None:
            continue
        key = (half, target_id)
        instances[key] = max(instances.get(key, 0.0), max_hp)
    hp_values = {round(value, 3) for value in instances.values()}
    if not instances or len(hp_values) != 1:
        return None
    static_id = static_ids[0]
    target_name = condition.target_name
    if condition.environment_kind == "feast" and static_database_path is not None:
        try:
            with StaticGameDataDao(static_database_path) as static_dao:
                stage = next(
                    (
                        row
                        for row in static_dao.list_feast_stages()
                        if str(row.get("stage_id") or "") == condition.environment_ref
                        and str(row.get("boss_monster_id") or "") == static_id
                    ),
                    None,
                )
            difficulty = next(
                (
                    row
                    for row in (stage or {}).get("difficulties") or ()
                    if int(row.get("difficulty_id") or 0)
                    == int(condition.difficulty_id or 0)
                ),
                None,
            )
            target_name = str((difficulty or {}).get("boss_name_zh") or target_name)
        except (OSError, RuntimeError, ValueError):
            pass
    profile = BattleSelectedTargetProfile(
        static_target_id=static_id,
        selection_target_id=static_id,
        target_name=target_name,
        monster_class_path=static_id,
        monster_count=len(instances),
        max_hp=next(iter(hp_values)),
        monster_level=condition.enemy_level,
        defense_base=condition.enemy_defense_base,
        defense_up=condition.enemy_defense_up,
        defense_add=condition.enemy_defense_add,
        topple_limit=condition.enemy_topple_limit,
        resistances=condition.resistances,
    )
    return replace(
        condition,
        primary_target_id=condition.primary_target_id or static_id,
        selected_target_profiles=(profile,),
    )


def inferred_mapping_condition(
    candidate: Any,
    *,
    source_kind: str,
) -> BattleTargetCondition | None:
    """Freeze the selected default environment into the normal mapping input."""

    profiles = tuple(
        BattleSelectedTargetProfile(
            static_target_id=target.target_id,
            selection_target_id=target.target_id,
            target_name=target.target_name,
            monster_class_path=target.monster_class_path,
            monster_count=target.monster_count,
            max_hp=target.max_hp,
            monster_level=target.monster_level,
            defense_base=target.defense_base,
            defense_up=target.defense_up,
            defense_add=target.defense_add,
            topple_limit=target.topple_limit,
            resistances=target.resistances,
            profile_set=target.profile_set,
            pack_id=target.pack_id,
        )
        for target in candidate.targets
        if target.target_id and target.max_hp > 0.0
    )
    if not profiles:
        return None
    primary = profiles[0]
    condition_kind = (
        "open_world" if candidate.environment_kind == "clone"
        else candidate.environment_kind
    )
    return BattleTargetCondition(
        target_name=candidate.environment_name,
        enemy_level=primary.monster_level,
        scene=(
            "open_world"
            if candidate.environment_kind in {"open_world", "clone"}
            else "outer_realm"
        ),
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=primary.resistances,
        source_kind=source_kind,
        enemy_defense_base=primary.defense_base,
        enemy_defense_up=primary.defense_up,
        enemy_defense_add=primary.defense_add,
        enemy_topple_limit=primary.topple_limit,
        environment_kind=condition_kind,
        environment_ref=candidate.environment_ref,
        selected_target_ids=tuple(
            dict.fromkeys(profile.selection_target_id for profile in profiles)
        ),
        primary_target_id=primary.selection_target_id,
        selected_target_profiles=profiles,
    )


def project_inferred_target_evidence(
    evidence: dict[str, Any] | None,
    inferred: Any | None,
) -> None:
    """Project derived names only; Core monster identity remains untouched."""

    if evidence is None or inferred is None:
        return
    hits = tuple(evidence.get("hits") or ())
    aliases = target_observation_aliases(tuple(
        hit
        for hit in hits
        if str(hit.get("direction") or "").casefold() == "outgoing"
    ))
    identities = {
        (row.scope_half, row.captured_target_id): row
        for row in inferred.identities
    }
    for hit in hits:
        half = str(hit.get("abyss_half") or "").strip().casefold()
        target_id = str(hit.get("target_id") or "").strip()
        canonical_id = aliases.get((half, target_id), target_id)
        identity = identities.get((half, canonical_id)) or identities.get(
            ("", canonical_id)
        )
        if identity is None:
            continue
        if identity.target_name:
            hit["target_name"] = identity.target_name
        hit["target_identity_source"] = inferred.source_kind
        hit["target_identity_confidence"] = inferred.confidence
        hit["target_environment_ref"] = inferred.environment_ref


def resolve_available_target_instances(
    evidence: Mapping[str, object] | None,
    saved_condition: BattleTargetCondition | None,
    inferred: Any | None,
    *,
    static_database_path: Path | None = None,
) -> tuple[tuple[BattleTargetInstanceResolution, ...], bool]:
    """Prefer saved profiles; otherwise consume known inferred half profiles."""

    if saved_condition is not None:
        condition = saved_condition
        if not condition.selected_target_profiles:
            condition = _legacy_single_target_condition(
                evidence,
                condition,
                static_database_path,
            ) or condition
        required = bool(condition.selected_target_profiles)
        return (
            BattleTargetInstanceMappingService.resolve(evidence, condition)
            if required
            else (),
            required,
        )
    conditions = tuple(
        getattr(inferred, "target_mapping_conditions_by_half", ()) or ()
    )
    resolutions = tuple(
        resolution
        for _half, condition in conditions
        for resolution in BattleTargetInstanceMappingService.resolve(
            evidence,
            condition,
        )
    )
    return resolutions, bool(conditions)


def project_resolved_target_evidence(
    evidence: dict[str, Any] | None,
    resolutions: tuple[BattleTargetInstanceResolution, ...],
) -> None:
    """Apply frozen resolution names in memory without touching identity facts."""

    if evidence is None or not resolutions:
        return
    by_key = {
        (row.scope_half.casefold(), row.captured_target_id): row
        for row in resolutions
    }
    for hit in evidence.get("hits") or ():
        half = str(hit.get("abyss_half") or "").strip().casefold()
        target_id = str(hit.get("target_id") or "").strip()
        resolution = by_key.get((half, target_id)) or by_key.get(("", target_id))
        condition = None if resolution is None else resolution.target_condition
        if condition is not None and condition.target_name:
            hit["target_name"] = condition.target_name
