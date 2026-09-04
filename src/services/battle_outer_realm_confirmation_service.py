# 将轨外之境的单半手选结果补全为同层上下半冻结目标档案。
"""Complete a user-confirmed outer-realm floor without mixing half identities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.battle_target_profile_snapshot_service import (
    battle_target_profile_snapshots,
)


_FIRST_HALF = "EAbyssFightStage::FirstHalf"
_SECOND_HALF = "EAbyssFightStage::SecondHalf"


def _captured_halves(evidence: Mapping[str, Any] | None) -> set[str]:
    result = set()
    for hit in (evidence or {}).get("hits") or ():
        if str(hit.get("direction") or "") != "outgoing":
            continue
        half = str(hit.get("abyss_half") or "").casefold()
        if half in {"upper", "lower"}:
            result.add(half)
    return result


def _outer_level(
    catalog: Mapping[str, Any],
    config_id: str,
    floor: int,
) -> Mapping[str, Any] | None:
    config = next((
        row
        for row in catalog.get("outer_realm") or ()
        if str(row.get("level_config_id") or "") == config_id
    ), None)
    if not isinstance(config, Mapping):
        return None
    level = next((
        row
        for row in config.get("levels") or ()
        if int(row.get("level_id") or 0) == floor
    ), None)
    return level if isinstance(level, Mapping) else None


def needs_outer_realm_confirmation(
    condition: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> bool:
    """Return whether one saved half should be completed from the static floor."""

    parts = str(condition.get("environment_ref") or "").split("|")
    return (
        str(condition.get("environment_kind") or "") == "outer_realm"
        and len(parts) >= 3
        and parts[2] in {_FIRST_HALF, _SECOND_HALF}
        and _captured_halves(evidence) == {"upper", "lower"}
    )


def complete_outer_realm_confirmation(
    condition: Mapping[str, Any],
    catalog: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Freeze both configured halves when the captured report contains both."""

    result = dict(condition)
    if not needs_outer_realm_confirmation(condition, evidence):
        return result
    parts = str(condition.get("environment_ref") or "").split("|")
    if len(parts) < 3 or parts[2] not in {_FIRST_HALF, _SECOND_HALF}:
        return result
    try:
        floor = int(parts[1])
    except ValueError:
        return result
    level = _outer_level(catalog, parts[0], floor)
    if level is None:
        return result
    halves = {
        str(row.get("stage") or ""): row
        for row in level.get("halves") or ()
        if isinstance(row, Mapping)
    }
    if not {_FIRST_HALF, _SECOND_HALF}.issubset(halves):
        return result

    targets = [
        target
        for stage in (_FIRST_HALF, _SECOND_HALF)
        for target in halves[stage].get("targets") or ()
        if isinstance(target, dict)
    ]
    target_ids = tuple(
        dict.fromkeys(
            str(target.get("target_id") or "")
            for target in targets
            if str(target.get("target_id") or "")
        )
    )
    profiles = [
        profile
        for target in targets
        for profile in battle_target_profile_snapshots(target)
    ]
    if not target_ids or not profiles:
        return result

    existing_profiles = {
        str(row.get("selection_target_id") or ""): row
        for row in condition.get("selected_target_profiles") or ()
        if isinstance(row, Mapping)
    }
    result.update({
        "environment_ref": f"{parts[0]}|{floor}|mixed",
        "environment_name": f"轨外之境第{floor}层上下半",
        "selected_target_ids": target_ids,
        "selected_target_profiles": [
            dict(existing_profiles.get(profile["selection_target_id"], profile))
            for profile in profiles
        ],
    })
    return result
