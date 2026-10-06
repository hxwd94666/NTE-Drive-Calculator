# 根据卡带对应套装和实际装备的不同形状判定两件、四件效果，缺失与零件分开。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _geometry(value: Any) -> str:
    return str(value or '').strip().removeprefix('EquipmentGeometry_').casefold()


def active_set_count(
    items: Sequence[Mapping[str, Any]] | None,
    suits: Mapping[str, Mapping[str, Any]],
    core_suits: Mapping[str, str],
    valid_shapes: frozenset[str],
) -> int | None:
    """Return the highest confirmed 2/4-piece threshold, 0 inactive, or None unknown."""
    if items is None or any(not isinstance(item, Mapping) or item.get('kind') not in {'core', 'module'} for item in items):
        return None
    cores = [item for item in items if item['kind'] == 'core']
    if not cores:
        return 0
    if len(cores) != 1:
        return None
    core = cores[0]
    suit_id = str(core.get('suit_id') or core_suits.get(str(core.get('item_id') or '')) or '')
    suit = suits.get(suit_id)
    if suit is None:
        return None
    required = {_geometry(shape) for shape in suit.get('required_shape_ids', ())}
    known_shapes = {_geometry(shape) for shape in valid_shapes}
    if not required or not required <= known_shapes:
        return None
    equipped = {_geometry(item.get('geometry')) for item in items if item['kind'] == 'module'}
    if not equipped <= known_shapes:
        return None
    thresholds = {row.get('required_count') for row in suit.get('effects', ())}
    if not {2, 4} <= thresholds:
        return None
    matched = len(required & equipped)
    return 4 if matched >= 4 else 2 if matched >= 2 else 0
