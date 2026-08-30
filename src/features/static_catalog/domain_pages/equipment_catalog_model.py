# 空幕与驱动资料库的 Qt-free 发行数据和固定库存投影。
"""Read-only archive, terminology and inventory state for the equipment page."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.features.static_catalog.contracts import CatalogLink
from src.services.static_catalog_mechanics_models import encode_record
from src.storage.sqlite.static_catalog_misc_queries import StaticCatalogMiscDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


@dataclass(frozen=True, slots=True)
class AttributeCurve:
    property_id: str
    label: str
    show_percent: bool
    points: tuple[tuple[int, float], ...]

    @property
    def max_value(self) -> float:
        return self.points[-1][1]


@dataclass(frozen=True, slots=True)
class StrengthExperience:
    levels: tuple[tuple[int, int], ...]

    @property
    def total(self) -> int:
        return sum(need_exp for _level, need_exp in self.levels)


@dataclass(frozen=True, slots=True)
class EquipmentRecord:
    item_id: str
    kind: str
    quality: str
    name: str
    shape_id: str
    area: int
    suit_id: str
    suit_name: str
    max_level: int
    main_count: int
    sub_count: int


@dataclass(frozen=True, slots=True)
class SuitEffect:
    required_count: int
    description: str
    modifiers: tuple[tuple[str, float], ...]
    has_conditional_effect: bool
    mechanics_link: CatalogLink | None


@dataclass(frozen=True, slots=True)
class SuitRecord:
    suit_id: str
    name: str
    required_shape_ids: tuple[str, ...]
    effects: tuple[SuitEffect, ...]


@dataclass(frozen=True, slots=True)
class ShapeRecord:
    shape_id: str
    name: str
    area: int
    cells: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class GraduationLink:
    suit_id: str
    character_name: str
    main_property_id: str
    drive_area: int


@dataclass(frozen=True, slots=True)
class OwnedEquipment:
    item_id: str
    kind: str
    quality: str
    suit_id: str
    shape_id: str
    level: int
    level_known: bool
    main_stats: tuple[tuple[str, object], ...]
    sub_stats: tuple[tuple[str, object], ...]
    locked: bool
    discarded: bool
    equipped_name: str
    state_known: bool
    instance_key: str


@dataclass(frozen=True, slots=True)
class FrozenInventoryProjection:
    account_id: str
    generation: int
    snapshot_id: int | None
    items: tuple[OwnedEquipment, ...]


@dataclass(frozen=True, slots=True)
class EquipmentArchive:
    equipment: tuple[EquipmentRecord, ...]
    suits: tuple[SuitRecord, ...]
    shapes: tuple[ShapeRecord, ...]
    graduations: tuple[GraduationLink, ...]
    attributes: tuple[tuple[str, str, bool], ...]


def _quality_key(value: object) -> str:
    text = str(value or "").upper()
    if "ORANGE" in text or "GOLD" in text or "金" in text or "橙" in text:
        return "ORANGE"
    if "PURPLE" in text or "紫" in text:
        return "PURPLE"
    if "BLUE" in text or "蓝" in text:
        return "BLUE"
    return text


def official_suit_number(value: str) -> int:
    suffix = value.removeprefix("Suit")
    return int(suffix) if suffix.isdigit() else 999


class ReleaseEquipmentCatalogSource:
    """Load and cache the normalized release-static equipment archive."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._archive: EquipmentArchive | None = None

    def archive(self) -> EquipmentArchive:
        if self._archive is not None:
            return self._archive
        with StaticGameDataDao(self._database_path) as dao:
            suits = self._suits(dao)
            suit_names = {row.suit_id: row.name for row in suits}
            equipment = tuple(
                EquipmentRecord(
                    item_id=str(row["item_id"]),
                    kind=str(row["kind"]),
                    quality=_quality_key(row["quality"]),
                    name=str(row["name_zh"]),
                    shape_id=str(row.get("geometry_id") or ""),
                    area=int(row.get("grid_count") or 0),
                    suit_id=str(row.get("suit_id") or ""),
                    suit_name=suit_names.get(str(row.get("suit_id") or ""), ""),
                    max_level=int(row.get("max_level") or 0),
                    main_count=int(row.get("random_base_attribute_count") or 0),
                    sub_count=int(row.get("random_sub_attribute_count") or 0),
                )
                for row in dao.list_equipment_items()
            )
            shapes = tuple(
                ShapeRecord(
                    shape_id=str(row["shape_id"]),
                    name=str(row.get("name_zh") or "名称暂未提供"),
                    area=int(row["cell_count"]),
                    cells=tuple(
                        (int(cell["x"]), int(cell["y"]))
                        for cell in row["cells"]
                    ),
                )
                for row in dao.list_shapes()
            )
            character_names = {
                int(row["character_id"]): str(row["character_name_zh"])
                for row in dao.list_equipment_plans()
            }
            graduations = tuple(
                GraduationLink(
                    suit_id=str(row.get("core_suit_id") or ""),
                    character_name=character_names.get(
                        int(row["character_id"]),
                        "角色名未提供",
                    ),
                    main_property_id=str(row.get("core_main_property_id") or ""),
                    drive_area=int(row.get("drive_area") or 0),
                )
                for row in dao.list_character_graduation_templates()
            )
            attributes = tuple(
                (
                    str(row["attribute_id"]),
                    str(
                        row.get("filter_name_zh")
                        or row.get("display_name_zh")
                        or "名称暂未提供"
                    ),
                    bool(row.get("show_percent")),
                )
                for row in dao.list_equipment_attributes()
            )
        self._archive = EquipmentArchive(
            equipment,
            suits,
            shapes,
            graduations,
            attributes,
        )
        return self._archive

    @staticmethod
    def _suits(dao: StaticGameDataDao) -> tuple[SuitRecord, ...]:
        records: list[SuitRecord] = []
        for row in dao.list_suits():
            effects: list[SuitEffect] = []
            for raw in row["effects"]:
                pack_id = str(raw.get("modify_pack_id") or "")
                pack = dao.get_equipment_modify_pack(pack_id) if pack_id else None
                modifiers = tuple(
                    (
                        str(value.get("property_id") or ""),
                        float(value.get("value") or 0.0),
                    )
                    for value in (pack or {}).get("modifiers", ())
                )
                effects.append(
                    SuitEffect(
                        required_count=int(raw["required_count"]),
                        description=str(
                            raw.get("description_zh") or "暂无正式说明"
                        ),
                        modifiers=modifiers,
                        has_conditional_effect=bool(raw.get("buff_object_path")),
                        mechanics_link=ReleaseEquipmentCatalogSource._buff_link(
                            dao,
                            raw.get("buff_object_path"),
                        ),
                    )
                )
            records.append(
                SuitRecord(
                    suit_id=str(row["suit_id"]),
                    name=str(row["name_zh"]),
                    required_shape_ids=tuple(
                        str(value) for value in row["required_shape_ids"]
                    ),
                    effects=tuple(effects),
                )
            )
        return tuple(records)

    @staticmethod
    def _buff_link(
        dao: StaticGameDataDao,
        object_path: object,
    ) -> CatalogLink | None:
        asset_path = str(object_path or "").removesuffix(".0")
        if not asset_path or dao.get_buff_definition(asset_path) is None:
            return None
        return CatalogLink(
            "combat_mechanics",
            encode_record("effect", f"buff\x1f{asset_path}"),
            "related",
        )

    def item_curves(self, item_id: str) -> tuple[AttributeCurve, ...]:
        item = next(
            row for row in self.archive().equipment if row.item_id == item_id
        )
        category = "Core" if item.kind == "core" else str(item.area)
        curves: list[AttributeCurve] = []
        with StaticGameDataDao(self._database_path) as dao:
            for property_id, label, show_percent in self.archive().attributes:
                curve_id = (
                    f"{property_id}_{category}_ITEM_QUALITY_{item.quality}"
                )
                if dao.evaluate_equipment_base_attribute_curve(
                    curve_id,
                    item.max_level,
                ) is None:
                    continue
                points = tuple(
                    (
                        level,
                        float(
                            dao.evaluate_equipment_base_attribute_curve(
                                curve_id,
                                level,
                            )
                            or 0.0
                        ),
                    )
                    for level in range(item.max_level + 1)
                )
                curves.append(
                    AttributeCurve(property_id, label, show_percent, points)
                )
        return tuple(curves)

    def strength_experience(self, item_id: str) -> StrengthExperience | None:
        with StaticCatalogMiscDao(self._database_path) as dao:
            detail = dao.get_equipment_catalog_detail("equipment_item", item_id)
        if detail is None:
            return None
        levels = tuple(
            (int(row["level"]), int(row["need_exp"]))
            for row in detail.get("strength_levels") or ()
        )
        return StrengthExperience(levels) if levels else None


class EquipmentCatalogPageController:
    """Own terminology and one immutable account inventory projection."""

    def __init__(
        self,
        source: ReleaseEquipmentCatalogSource,
        terminology: StaticCatalogTerminologyService | None,
    ) -> None:
        self._source = source
        self._terminology = terminology
        self.archive = source.archive()
        self.inventory: FrozenInventoryProjection | None = None
        self._official_item_ids = frozenset(
            row.item_id for row in self.archive.equipment
        )

    def curves(self, item_id: str) -> tuple[AttributeCurve, ...]:
        return self._source.item_curves(item_id)

    def strength_experience(self, item_id: str) -> StrengthExperience | None:
        return self._source.strength_experience(item_id)

    def suit(self, suit_id: str) -> SuitRecord | None:
        return next(
            (row for row in self.archive.suits if row.suit_id == suit_id),
            None,
        )

    def shape(self, shape_id: str) -> ShapeRecord | None:
        return next(
            (row for row in self.archive.shapes if row.shape_id == shape_id),
            None,
        )

    def property_info(self, property_id: str) -> tuple[str, bool]:
        return next(
            (
                (label, percent)
                for key, label, percent in self.archive.attributes
                if key == property_id
            ),
            ("名称暂未提供", False),
        )

    def shape_name(self, shape_id: str) -> str:
        shape = self.shape(shape_id)
        return shape.name if shape is not None else "名称暂未提供"

    def quality_name(self, quality_id: str) -> str:
        if self._terminology is None:
            return "品质名称暂未提供"
        term = self._terminology.resolve("item_quality", str(quality_id))
        if not term.display_name:
            return "品质名称暂未提供"
        return (
            term.display_name
            if term.display_name.endswith("级")
            else f"{term.display_name}级"
        )

    def graduations(self, suit_id: str) -> tuple[GraduationLink, ...]:
        return tuple(
            row for row in self.archive.graduations if row.suit_id == suit_id
        )

    def apply_inventory_snapshot(
        self,
        *,
        account_id: str,
        generation: int,
        snapshot: Mapping[str, Any],
    ) -> bool:
        raw_snapshot_id = snapshot.get("snapshot_id")
        snapshot_id = int(raw_snapshot_id) if raw_snapshot_id is not None else None
        current = self.inventory
        if current is not None and generation < current.generation:
            return False
        if (
            current is not None
            and account_id == current.account_id
            and generation == current.generation
            and current.snapshot_id is not None
            and snapshot_id is not None
            and snapshot_id < current.snapshot_id
        ):
            return False
        source = str(snapshot.get("source") or "")
        items = tuple(
            self._owned_item(row, source, index)
            for index, row in enumerate(snapshot.get("rows") or ())
            if isinstance(row, Mapping)
        )
        self.inventory = FrozenInventoryProjection(
            str(account_id),
            int(generation),
            snapshot_id,
            items,
        )
        return True

    def invalidate_inventory(self) -> None:
        """Drop the previous account projection before any refresh attempt."""

        self.inventory = None

    def _owned_item(
        self,
        row: Mapping[str, Any],
        source: str,
        index: int,
    ) -> OwnedEquipment:
        def stats(key: str) -> tuple[tuple[str, object], ...]:
            values: list[tuple[str, object]] = []
            for stat in row.get(key) or ():
                if not isinstance(stat, Mapping):
                    continue
                label, percent = self.property_info(
                    str(stat.get("property_id") or "")
                )
                value = stat.get("value", 0)
                if percent and isinstance(value, (int, float)):
                    value = f"{float(value) * 100:g}%"
                values.append((label, value))
            return tuple(values)

        state_known = source == "nte_core"
        return OwnedEquipment(
            item_id=str(row.get("item_id") or ""),
            kind=str(row.get("kind") or ""),
            quality=_quality_key(row.get("quality")),
            suit_id=str(row.get("suit_id") or ""),
            shape_id=str(row.get("geometry") or row.get("shape_id") or ""),
            level=int(row.get("level") or 0),
            level_known=bool(row.get("level_known", source == "nte_core")),
            main_stats=stats("main_stats"),
            sub_stats=stats("sub_stats"),
            locked=state_known and bool(row.get("locked")),
            discarded=state_known and bool(row.get("discarded")),
            equipped_name=(
                str(row.get("equipped_character_name") or "")
                if state_known and row.get("equipped")
                else ""
            ),
            state_known=state_known,
            instance_key=(
                f"owned-{row.get('uid_slot', '')}-"
                f"{row.get('uid_serial', '')}-{index}"
            ),
        )

    def owned_for(self, record: EquipmentRecord) -> tuple[OwnedEquipment, ...]:
        if self.inventory is None:
            return ()
        result: list[OwnedEquipment] = []
        for item in self.inventory.items:
            exact = item.item_id == record.item_id
            fallback = item.item_id not in self._official_item_ids and (
                item.kind == record.kind
                and item.quality == record.quality
                and (
                    (record.kind == "core" and item.suit_id == record.suit_id)
                    or (
                        record.kind == "module"
                        and item.shape_id == record.shape_id
                    )
                )
            )
            if exact or fallback:
                result.append(item)
        return tuple(result)

    def owned_count(
        self,
        *,
        suit_id: str = "",
        shape_id: str = "",
    ) -> int | None:
        if self.inventory is None:
            return None
        return sum(
            1
            for item in self.inventory.items
            if (suit_id and item.suit_id == suit_id)
            or (shape_id and item.shape_id == shape_id)
        )


__all__ = [
    "AttributeCurve",
    "EquipmentCatalogPageController",
    "EquipmentRecord",
    "ReleaseEquipmentCatalogSource",
    "ShapeRecord",
    "StrengthExperience",
    "SuitRecord",
    "official_suit_number",
]
