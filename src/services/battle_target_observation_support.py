# 规范化目标观测且不修改持久化逐击证据。
"""Target-observation normalization that never mutates persisted hit evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence


def _number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _duplicate_fingerprint(row: Mapping[str, object]) -> tuple[object, ...] | None:
    values = tuple(
        _number(row.get(key))
        for key in (
            "target_max_hp",
            "target_hp_before",
            "target_hp_after",
            "total_damage",
        )
    )
    if any(value is None for value in values):
        return None
    return (
        str(row.get("abyss_half") or "").strip().casefold(),
        *(round(float(value), 6) for value in values if value is not None),
    )


def target_observation_aliases(
    rows: Sequence[Mapping[str, object]],
) -> dict[tuple[str, str], str]:
    """Collapse evidence-proven duplicate or continuous wire handles."""

    counts = Counter(
        (
            str(row.get("abyss_half") or "").strip().casefold(),
            str(row.get("target_id") or "").strip(),
        )
        for row in rows
        if str(row.get("target_id") or "").strip()
    )
    aliases: dict[tuple[str, str], str] = {}
    first_by_target: dict[tuple[str, str], Mapping[str, object]] = {}
    for row in sorted(rows, key=lambda item: int(item.get("relative_time_us") or 0)):
        half = str(row.get("abyss_half") or "").strip().casefold()
        target_id = str(row.get("target_id") or "").strip()
        if target_id:
            first_by_target.setdefault((half, target_id), row)

    # Some combat effects expose a second wire handle for the same live boss.
    # Accept the alias only when the new handle starts exactly from a prior
    # handle's observed HP endpoint and both report the same maximum HP.
    for (half, target_id), first in first_by_target.items():
        first_time = int(first.get("relative_time_us") or 0)
        first_before = _number(first.get("target_hp_before"))
        first_max = _number(first.get("target_max_hp"))
        if first_before is None or first_max is None or first_before >= first_max:
            continue
        matches = {
            str(candidate.get("target_id") or "").strip()
            for candidate in rows
            if str(candidate.get("abyss_half") or "").strip().casefold() == half
            and str(candidate.get("target_id") or "").strip()
            and str(candidate.get("target_id") or "").strip() != target_id
            and int(candidate.get("relative_time_us") or 0) <= first_time
            and first_time - int(candidate.get("relative_time_us") or 0) <= 5_000_000
            and _number(candidate.get("target_max_hp")) is not None
            and abs(float(candidate.get("target_max_hp")) - first_max) <= 1.0
            and _number(candidate.get("target_hp_after")) is not None
            and abs(float(candidate.get("target_hp_after")) - first_before) <= 1.0
        }
        if len(matches) == 1:
            aliases[(half, target_id)] = next(iter(matches))

    for row in rows:
        half = str(row.get("abyss_half") or "").strip().casefold()
        target_id = str(row.get("target_id") or "").strip()
        fingerprint = _duplicate_fingerprint(row)
        if (
            not target_id
            or counts[(half, target_id)] != 1
            or row.get("target_context")
            or fingerprint is None
        ):
            continue
        time_us = int(row.get("relative_time_us") or 0)
        matches = {
            str(candidate.get("target_id") or "").strip()
            for candidate in rows
            if candidate.get("target_context")
            and str(candidate.get("target_id") or "").strip() != target_id
            and _duplicate_fingerprint(candidate) == fingerprint
            and abs(int(candidate.get("relative_time_us") or 0) - time_us) <= 5_000
        }
        if len(matches) == 1:
            aliases.setdefault((half, target_id), next(iter(matches)))
    return aliases
