# 定义仓库固定快照的无 Qt 筛选契约。
"""Qt-free filtering contract for one projected warehouse snapshot."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class WarehouseFilterSpec:
    search: str = ""
    kind: str = "all"
    item_type_ids: frozenset[str] = frozenset()
    qualities: frozenset[str] = frozenset()
    statuses: frozenset[str] = frozenset()
    main_property_ids: frozenset[str] = frozenset()
    sub_property_ids: frozenset[str] = frozenset()
    min_sub_stat_matches: int | None = None

    def with_search(self, search: str) -> WarehouseFilterSpec:
        return replace(self, search=str(search or ""))

    @property
    def active_group_count(self) -> int:
        return sum(
            (
                self.kind != "all",
                bool(self.item_type_ids),
                bool(self.qualities),
                bool(self.statuses),
                bool(self.main_property_ids),
                bool(self.sub_property_ids) and self.min_sub_stat_matches is not None,
            )
        )


@dataclass(frozen=True, slots=True)
class WarehouseFilterOption:
    value: str
    label: str
    count: int
    icon_path: str = ""


@dataclass(frozen=True, slots=True)
class WarehouseFilterCatalog:
    item_types: tuple[WarehouseFilterOption, ...] = ()
    qualities: tuple[WarehouseFilterOption, ...] = ()
    statuses: tuple[WarehouseFilterOption, ...] = ()
    main_properties: tuple[WarehouseFilterOption, ...] = ()
    sub_properties: tuple[WarehouseFilterOption, ...] = ()


_QUALITY_LABELS = {"blue": "蓝色", "purple": "紫色", "gold": "金色"}
_QUALITY_ORDER = {"blue": 0, "purple": 1, "gold": 2}
_STATUS_LABELS = {
    "equipped": "已装备",
    "locked": "已锁定",
    "discarded": "已弃置",
    "other": "其他",
}
_STATUS_ORDER = {value: index for index, value in enumerate(_STATUS_LABELS)}

_MAIN_PROPERTY_ORDER = {
    value: index
    for index, value in enumerate(
        (
            "HPMaxUp",
            "AtkUp",
            "DefUp",
            "CritBase",
            "CritDamageBase",
            "MagBase",
            "UnbalIntensityBase",
            "HealUp",
            "DamageUpCosmosBase",
            "DamageUpNatureBase",
            "DamageUpIncantationBase",
            "DamageUpChaosBase",
            "DamageUpPsycheBase",
            "DamageUpLakshanaBase",
            "DamageUpPsychicallyBase",
        )
    )
}
_SUB_PROPERTY_ORDER = {
    value: index
    for index, value in enumerate(
        (
            "HPMaxUp",
            "AtkUp",
            "DefUp",
            "HPMaxAdd",
            "AtkAdd",
            "DefAdd",
            "CritBase",
            "CritDamageBase",
            "MagBase",
            "UnbalIntensityBase",
            "DamageUpGeneralBase",
            "DamageUpGeneralAdd",
        )
    )
}
_SHAPE_ORDER = {
    value: index
    for index, value in enumerate(
        (
            "EquipmentGeometry_Hen2",
            "EquipmentGeometry_Shu2",
            "EquipmentGeometry_Hen3",
            "EquipmentGeometry_Shu3",
            "EquipmentGeometry_ZhiJiao1",
            "EquipmentGeometry_ZhiJiao2",
            "EquipmentGeometry_ZhiJiao3",
            "EquipmentGeometry_ZhiJiao4",
            "EquipmentGeometry_Hen4",
            "EquipmentGeometry_Shu4",
            "EquipmentGeometry_Z3",
            "EquipmentGeometry_Z4",
        )
    )
}
_SHAPE_ORDER_CASEFOLD = {key.casefold(): value for key, value in _SHAPE_ORDER.items()}


def _normalized_shape_id(value: object) -> str:
    text = str(value or "")
    if text.casefold().startswith("module:"):
        text = text.split(":", 1)[1]
    return text.casefold()


def warehouse_shape_order(value: object) -> int:
    """Return the game's II/III/IV shape-filter order for an official ID."""

    return _SHAPE_ORDER_CASEFOLD.get(_normalized_shape_id(value), len(_SHAPE_ORDER))


def warehouse_shape_size(value: object) -> int | None:
    """Return the visible drive group (II/III/IV) for an official shape ID."""

    rank = warehouse_shape_order(value)
    if rank < 2:
        return 2
    if rank < 8:
        return 3
    if rank < len(_SHAPE_ORDER):
        return 4
    return None


def warehouse_item_sort_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
    """Group cards by suit and drives by the game's official shape order."""

    kind = str(item.get("kind") or "")
    if kind == "core":
        label = str(item.get("item_type_label") or item.get("suit_id") or "")
        return (0, 0, label.casefold())
    if kind == "module":
        shape_id = item.get("shape_id") or item.get("item_type_id") or ""
        return (1, warehouse_shape_order(shape_id), str(shape_id).casefold())
    return (2, 0, str(item.get("item_type_label") or "").casefold())


def sort_projected_warehouse_items(
    items: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return a stable type-grouped projection without mutating snapshot rows."""

    rows = [dict(item) for item in items]
    return sorted(rows, key=warehouse_item_sort_key)


def warehouse_item_statuses(item: Mapping[str, Any]) -> frozenset[str]:
    result: set[str] = set()
    if item.get("locked"):
        result.add("locked")
    if item.get("discarded"):
        result.add("discarded")
    if item.get("equipped"):
        result.add("equipped")
    if not result:
        result.add("other")
    return frozenset(result)


def _property_ids(item: Mapping[str, Any], field: str) -> frozenset[str]:
    return frozenset(
        str(stat.get("property_id") or "")
        for stat in item.get(field) or ()
        if isinstance(stat, Mapping) and stat.get("property_id")
    )


def filter_projected_warehouse_items(
    items: Iterable[Mapping[str, Any]],
    spec: WarehouseFilterSpec,
) -> list[dict[str, Any]]:
    """Apply OR within each group and AND across groups."""
    needle = spec.search.strip().casefold()
    result: list[dict[str, Any]] = []
    for source in items:
        item = dict(source)
        if spec.kind != "all" and item.get("kind") != spec.kind:
            continue
        if spec.item_type_ids and item.get("item_type_id") not in spec.item_type_ids:
            continue
        if spec.qualities and item.get("quality") not in spec.qualities:
            continue
        if spec.statuses and warehouse_item_statuses(item).isdisjoint(spec.statuses):
            continue
        if spec.main_property_ids and _property_ids(item, "main_stats").isdisjoint(
            spec.main_property_ids
        ):
            continue
        if spec.sub_property_ids and spec.min_sub_stat_matches is not None:
            matches = len(_property_ids(item, "sub_stats") & spec.sub_property_ids)
            if matches < spec.min_sub_stat_matches:
                continue
        if needle and needle not in str(item.get("search_text") or ""):
            continue
        result.append(item)
    return sort_projected_warehouse_items(result)


def build_warehouse_filter_catalog(
    items: Iterable[Mapping[str, Any]],
    *,
    kind: str | None = None,
) -> WarehouseFilterCatalog:
    rows = [
        dict(item)
        for item in items
        if kind is None or str(item.get("kind") or "") == kind
    ]
    type_labels: dict[str, str] = {}
    type_icons: dict[str, str] = {}
    property_labels: dict[str, str] = {}
    type_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter({value: 0 for value in _STATUS_LABELS})
    main_counts: Counter[str] = Counter()
    sub_counts: Counter[str] = Counter()
    for item in rows:
        item_type_id = str(item.get("item_type_id") or "")
        if item_type_id:
            type_counts[item_type_id] += 1
            type_labels[item_type_id] = str(item.get("item_type_label") or item_type_id)
            if not type_icons.get(item_type_id):
                type_icons[item_type_id] = str(item.get("item_icon_path") or "")
        quality = str(item.get("quality") or "")
        if quality:
            quality_counts[quality] += 1
        status_counts.update(warehouse_item_statuses(item))
        for field, counter in (("main_stats", main_counts), ("sub_stats", sub_counts)):
            seen: set[str] = set()
            for stat in item.get(field) or ():
                if not isinstance(stat, Mapping):
                    continue
                property_id = str(stat.get("property_id") or "")
                if not property_id or property_id in seen:
                    continue
                seen.add(property_id)
                counter[property_id] += 1
                property_labels[property_id] = str(stat.get("label") or property_id)

    def options(
        counts: Counter[str],
        labels: Mapping[str, str],
        *,
        order: Mapping[str, int] | None = None,
        icons: Mapping[str, str] | None = None,
    ) -> tuple[WarehouseFilterOption, ...]:
        sort_order = order or {}
        icon_paths = icons or {}
        return tuple(
            WarehouseFilterOption(
                value,
                labels.get(value, value),
                count,
                icon_paths.get(value, ""),
            )
            for value, count in sorted(
                counts.items(),
                key=lambda pair: (
                    sort_order.get(
                        pair[0],
                        sort_order.get(pair[0].casefold(), 999),
                    ),
                    labels.get(pair[0], pair[0]),
                    pair[0],
                ),
            )
        )

    return WarehouseFilterCatalog(
        item_types=options(
            type_counts,
            type_labels,
            order={f"module:{key}".casefold(): value for key, value in _SHAPE_ORDER.items()},
            icons=type_icons,
        ),
        qualities=options(quality_counts, _QUALITY_LABELS, order=_QUALITY_ORDER),
        statuses=options(status_counts, _STATUS_LABELS, order=_STATUS_ORDER),
        main_properties=options(main_counts, property_labels, order=_MAIN_PROPERTY_ORDER),
        sub_properties=options(sub_counts, property_labels, order=_SUB_PROPERTY_ORDER),
    )


def normalize_warehouse_filter_spec(
    spec: WarehouseFilterSpec,
    catalog: WarehouseFilterCatalog,
) -> WarehouseFilterSpec:
    def available(options: tuple[WarehouseFilterOption, ...]) -> frozenset[str]:
        return frozenset(option.value for option in options)

    kind = spec.kind if spec.kind in {"all", "core", "module"} else "all"
    item_type_ids = spec.item_type_ids & available(catalog.item_types)
    if kind != "all":
        item_type_ids = frozenset(
            value for value in item_type_ids if value.startswith(f"{kind}:")
        )
    sub_property_ids = spec.sub_property_ids & available(catalog.sub_properties)
    return replace(
        spec,
        kind=kind,
        item_type_ids=item_type_ids,
        qualities=spec.qualities & available(catalog.qualities),
        statuses=spec.statuses & available(catalog.statuses),
        main_property_ids=(
            spec.main_property_ids & available(catalog.main_properties)
            if kind != "module"
            else frozenset()
        ),
        sub_property_ids=sub_property_ids,
        min_sub_stat_matches=(
            min(4, max(0, spec.min_sub_stat_matches))
            if sub_property_ids and spec.min_sub_stat_matches is not None
            else None
        ),
    )
