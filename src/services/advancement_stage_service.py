# 人物与弧盘等级必须保留同一上限等级的突破前后歧义。
"""Qt-free advancement-stage resolution shared by role editors and formulas."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (float, str)):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def legacy_fork_breakthrough_stage(level: int) -> int:
    """Resolve old level-only data to the pre-breakthrough cap state."""

    wanted_level = max(1, min(80, int(level)))
    return max(0, min(6, (wanted_level - 1) // 10 - 1))


def character_growth_choices(
    growth_rows: Sequence[Mapping[str, Any]],
    level: int,
) -> tuple[Mapping[str, Any], ...]:
    """Return every official character-growth state at ``level``.

    Ordinary levels have one row.  At 20/30/.../70 the static catalog has a
    breakthrough-before row and a breakthrough-after row, distinguished by
    ``breakthrough_stage``.
    """

    wanted_level = int(level)
    return tuple(sorted(
        (
            row
            for row in growth_rows
            if _integer(row.get("level")) == wanted_level
        ),
        key=lambda row: _integer(row.get("breakthrough_stage")),
    ))


def select_character_growth(
    growth_rows: Sequence[Mapping[str, Any]],
    level: int,
    *,
    preferred_stage: int | None = None,
) -> Mapping[str, Any] | None:
    """Resolve one exact character-growth row without discarding cap states.

    A stored or currently selected stage wins.  If a caller supplies an old or
    invalid stage, the closest valid stage preserves edit continuity.  With no
    preference, the historical role-page default remains breakthrough-after.
    """

    choices = character_growth_choices(growth_rows, level)
    if not choices:
        return None
    if preferred_stage is not None:
        wanted_stage = int(preferred_stage)
        return min(
            choices,
            key=lambda row: abs(
                _integer(row.get("breakthrough_stage")) - wanted_stage
            ),
        )
    return choices[-1]


def fork_breakthrough_choices(
    breakthrough_rows: Sequence[Mapping[str, Any]],
    level: int,
) -> tuple[Mapping[str, Any], ...]:
    """Return the valid fork stages at a level.

    ``max_fork_level`` is the cap unlocked by a stage.  The ordinary stage is
    therefore the first row whose cap contains the selected level.  Exactly at
    a cap, the next stage is also valid because breakthrough changes the panel
    before the fork advances to the following numeric level.
    """

    wanted_level = int(level)
    rows = tuple(sorted(
        breakthrough_rows,
        key=lambda row: (
            _integer(row.get("max_fork_level")),
            _integer(row.get("stage")),
        ),
    ))
    base_index = next(
        (
            index
            for index, row in enumerate(rows)
            if _integer(row.get("max_fork_level")) >= wanted_level
        ),
        None,
    )
    if base_index is None:
        return ()
    base = rows[base_index]
    choices = [base]
    if (
        _integer(base.get("max_fork_level")) == wanted_level
        and base_index + 1 < len(rows)
    ):
        choices.append(rows[base_index + 1])
    return tuple(choices)


def select_fork_breakthrough(
    breakthrough_rows: Sequence[Mapping[str, Any]],
    level: int,
    *,
    preferred_stage: int | None = None,
) -> Mapping[str, Any] | None:
    """Resolve the explicit fork stage, defaulting legacy cap levels to before."""

    choices = fork_breakthrough_choices(breakthrough_rows, level)
    if not choices:
        return None
    if preferred_stage is not None:
        wanted_stage = int(preferred_stage)
        return min(
            choices,
            key=lambda row: abs(_integer(row.get("stage")) - wanted_stage),
        )
    return choices[0]


def fork_panel_stats(
    template: Mapping[str, Any] | None,
    level: int,
    *,
    breakthrough_stage: int | None = None,
) -> dict[str, float]:
    """Combine one exact fork level with its explicit breakthrough stage."""

    if not template:
        return {}
    wanted_level = int(level)
    upgrade = next(
        (
            row
            for row in template.get("upgrade_levels") or ()
            if _integer(row.get("level")) == wanted_level
        ),
        None,
    )
    breakthrough = select_fork_breakthrough(
        template.get("breakthroughs") or (),
        wanted_level,
        preferred_stage=breakthrough_stage,
    )
    if upgrade is None or breakthrough is None:
        return {}
    totals: dict[str, float] = {}
    for row in (upgrade, breakthrough):
        for modifier in row.get("modifiers") or ():
            property_id = str(modifier.get("property_id") or "")
            if property_id:
                totals[property_id] = (
                    totals.get(property_id, 0.0)
                    + float(modifier.get("value") or 0.0)
                )
    return totals


def fork_permanent_stats(
    template: Mapping[str, Any] | None,
    refinement_level: int | None,
) -> dict[str, float]:
    """Resolve the captured unconditional panel bonus for one refinement."""

    if not template or refinement_level is None:
        return {}
    row = next(
        (
            item
            for item in template.get("permanent_properties") or ()
            if _integer(item.get("refinement_level")) == int(refinement_level)
        ),
        None,
    )
    if row is None:
        return {}
    property_id = str(row.get("property_id") or "")
    if not property_id:
        return {}
    return {property_id: float(row.get("property_value") or 0.0)}


def fork_active_panel_stats(
    template: Mapping[str, Any] | None,
    level: int,
    *,
    breakthrough_stage: int | None = None,
    refinement_level: int | None = None,
) -> dict[str, float]:
    """Combine growth, breakthrough and unconditional refinement panel stats."""

    totals = fork_panel_stats(
        template,
        level,
        breakthrough_stage=breakthrough_stage,
    )
    for property_id, value in fork_permanent_stats(template, refinement_level).items():
        totals[property_id] = totals.get(property_id, 0.0) + value
    return totals
