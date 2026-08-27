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
    """Collapse one-off contextless copies of an identical observed HP transition."""

    counts = Counter(
        (
            str(row.get("abyss_half") or "").strip().casefold(),
            str(row.get("target_id") or "").strip(),
        )
        for row in rows
        if str(row.get("target_id") or "").strip()
    )
    aliases: dict[tuple[str, str], str] = {}
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
            aliases[(half, target_id)] = next(iter(matches))
    return aliases
