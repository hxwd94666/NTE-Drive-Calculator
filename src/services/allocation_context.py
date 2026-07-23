# 为未来词条权重配装求解器构造不可变输入上下文。
"""Build immutable inputs for a future weighted-stat allocation solver.

The context is deliberately independent of the current allocation runner.  It
pins all database-derived inputs before a solver starts, so background inventory
syncs or later preference edits cannot change a calculation in flight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.domain.stat_catalog import StatCatalog
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


ALLOCATION_CONTEXT_SOLVER_VERSION = "allocation-context-v1"
_LEGACY_SHAPE_LABELS = {
    "H_2": "Type-2", "V_2": "Type-2",
    "H_3": "Type-3", "V_3": "Type-3",
    "L_3_BL": "Type-3", "L_3_TL": "Type-3", "L_3_TR": "Type-3", "L_3_BR": "Type-3",
    "H_4": "Type-4", "V_4": "Type-4", "Trap_4_H": "Type-4", "Trap_4_V": "Type-4",
}


def _legacy_shape_labels() -> Mapping[str, str]:
    """Return compatibility labels derived from official geometry mappings."""

    return dict(_LEGACY_SHAPE_LABELS)


class AllocationContextError(RuntimeError):
    """A requested immutable allocation input cannot be constructed."""


@dataclass(frozen=True, slots=True)
class StaticDatasetReference:
    """The read-only static dataset identity used to interpret official IDs."""

    schema_version: int
    dataset_id: str
    importer_version: int
    built_at_utc: str


@dataclass(frozen=True, slots=True)
class InventorySnapshotReference:
    """The immutable inventory snapshot selected for one calculation."""

    snapshot_id: int
    source: str
    generation: int | None
    sequence: int | None
    observed_at_unix_ms: int | None
    captured_at_utc: str
    declared_item_count: int
    stored_item_count: int


@dataclass(frozen=True, slots=True)
class OfficialStat:
    """One official property ID and its snapshot value."""

    property_id: str
    value: float
    percent: bool


@dataclass(frozen=True, slots=True)
class AllocationCandidate:
    """A fully copied official inventory item from the pinned snapshot."""

    uid_slot: int
    uid_serial: int
    kind: str
    item_id: str
    suit_id: str | None
    geometry: str | None
    grid_count: int | None
    quality: str | None
    level: int
    max_level: int
    locked: bool
    discarded: bool
    equipped: bool
    equipped_character_id: int | None
    is_duplicate_drive: bool
    duplicate_group_id: str | None
    duplicate_index: int | None
    duplicate_count: int | None
    main_stats: tuple[OfficialStat, ...]
    sub_stats: tuple[OfficialStat, ...]

    @property
    def uid(self) -> tuple[int, int]:
        """Return the native game UID as ``(slot, serial)``."""

        return self.uid_slot, self.uid_serial


@dataclass(frozen=True, slots=True)
class PropertyLimit:
    """Optional lower and upper limits for one official property ID."""

    property_id: str
    minimum: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class BlueprintCell:
    """One playable coordinate in the official role chassis."""

    row: int
    column: int


@dataclass(frozen=True, slots=True)
class SuitConstraint:
    """An official suit and the geometry IDs required to activate it."""

    suit_id: str
    required_shape_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OfficialShapeCell:
    """One official relative grid coordinate used by the local puzzle solver."""

    x: int
    y: int


@dataclass(frozen=True, slots=True)
class OfficialShape:
    """An official geometry definition frozen for pure puzzle generation."""

    shape_id: str
    cell_count: int
    cells: tuple[OfficialShapeCell, ...]
    legacy_shape_id: str | None = None
    legacy_label: str | None = None


@dataclass(frozen=True, slots=True)
class RoleEquipmentConstraints:
    """The only role-specific official layout constraint: its playable chassis."""

    character_id: int
    cells: tuple[BlueprintCell, ...]


@dataclass(frozen=True, slots=True)
class AllocationRolePreference:
    """One pinned role preference from a versioned optimization profile."""

    character_id: int
    ordinal: int
    priority_group: int
    target_suit_id: str | None
    suit_requirement_mode: str
    core_main_property_id: str | None
    property_weights: tuple[tuple[str, float], ...]
    substat_priorities: tuple[str, ...]
    property_limits: tuple[PropertyLimit, ...]
    equipment: RoleEquipmentConstraints
    # These values are copied from the currently synchronized workshop profile
    # when Context is built.  User v5 weights override the matching workshop
    # entries; the solver never follows roles.json after this boundary.
    effective_property_weights: tuple[tuple[str, float], ...] = ()
    effective_main_property_weights: tuple[tuple[str, float], ...] = ()
    extra_shape_label: str = ""
    extra_shape_buffs: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class OfficialAttribute:
    """Frozen compatibility label for the existing ScoringEngine."""

    property_id: str
    scoring_name: str


@dataclass(frozen=True, slots=True)
class AllocationContext:
    """All immutable inputs consumed by one future allocation solver call."""

    account_id: str
    static_dataset: StaticDatasetReference
    snapshot: InventorySnapshotReference
    profile_id: int
    profile_version: int
    allocation_strategy: str
    solver_version: str
    roles: tuple[AllocationRolePreference, ...]
    candidates: tuple[AllocationCandidate, ...]
    shapes: tuple[OfficialShape, ...]
    suits: tuple[SuitConstraint, ...]
    attributes: tuple[OfficialAttribute, ...] = ()


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AllocationContextError(f"{label} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def _geometry_key(value: Any, label: str) -> str:
    """Normalize inventory shorthand and static ``EquipmentGeometry_`` IDs."""

    geometry_id = _required_text(value, label)
    prefix = "EquipmentGeometry_"
    if geometry_id.startswith(prefix):
        geometry_id = geometry_id[len(prefix):]
    return geometry_id


def _official_attribute_id(value: Any, known_attribute_ids: set[str], label: str) -> str:
    attribute_id = _required_text(value, label)
    if attribute_id not in known_attribute_ids:
        raise AllocationContextError(f"静态数据库没有官方属性 ID：{attribute_id}")
    return attribute_id


def _stats(
    rows: Sequence[Mapping[str, Any]], known_attribute_ids: set[str]
) -> tuple[OfficialStat, ...]:
    return tuple(
        OfficialStat(
            property_id=_official_attribute_id(
                row.get("property_id"), known_attribute_ids, "背包词条 property_id"
            ),
            value=float(row.get("value")),
            percent=bool(row.get("percent")),
        )
        for row in rows
    )


def _candidate(
    row: Mapping[str, Any], *, known_attribute_ids: set[str],
    equipment_by_id: Mapping[str, Mapping[str, Any]], known_suit_ids: set[str],
    known_geometry_keys: set[str], known_character_ids: set[int],
) -> AllocationCandidate:
    kind = _required_text(row.get("kind"), "背包物品 kind")
    item_id = _required_text(row.get("item_id"), "背包物品 item_id")
    template = equipment_by_id.get(item_id)
    if template is None:
        raise AllocationContextError(f"静态数据库没有官方装备模板：{item_id}")
    if kind != _required_text(template.get("kind"), "官方装备模板 kind"):
        raise AllocationContextError(f"背包物品 {item_id} 的 kind 与官方模板不一致")
    suit_id = _optional_text(row.get("suit_id"))
    if suit_id is not None and suit_id not in known_suit_ids:
        raise AllocationContextError(f"静态数据库没有官方套装 ID：{suit_id}")
    geometry = _optional_text(row.get("geometry"))
    frozen_geometry = geometry
    if kind == "module":
        candidate_geometry = _geometry_key(geometry, "背包模块 geometry")
        template_geometry_id = _required_text(template.get("geometry_id"), "官方模块 geometry_id")
        template_geometry = _geometry_key(template_geometry_id, "官方模块 geometry_id")
        if candidate_geometry not in known_geometry_keys:
            raise AllocationContextError(f"静态数据库没有官方形状 ID：{geometry}")
        if candidate_geometry != template_geometry:
            raise AllocationContextError(f"背包模块 {item_id} 的 geometry 与官方模板不一致")
        frozen_geometry = template_geometry_id
    equipped_character_id = _optional_int(row.get("equipped_character_id"))
    if equipped_character_id is not None and equipped_character_id not in known_character_ids:
        raise AllocationContextError(f"静态数据库没有已装备角色：{equipped_character_id}")
    return AllocationCandidate(
        uid_slot=int(row["uid_slot"]),
        uid_serial=int(row["uid_serial"]),
        kind=kind,
        item_id=item_id,
        suit_id=suit_id,
        geometry=frozen_geometry,
        grid_count=_optional_int(row.get("grid_count")),
        quality=_optional_text(row.get("quality")),
        level=int(row.get("level") or 0),
        max_level=int(row.get("max_level") or 0),
        locked=bool(row.get("locked")),
        discarded=bool(row.get("discarded")),
        equipped=bool(row.get("equipped")),
        equipped_character_id=_optional_int(row.get("equipped_character_id")),
        is_duplicate_drive=bool(row.get("is_duplicate_drive")),
        duplicate_group_id=_optional_text(row.get("duplicate_group_id")),
        duplicate_index=_optional_int(row.get("duplicate_index")),
        duplicate_count=_optional_int(row.get("duplicate_count")),
        main_stats=_stats(row.get("main_stats") or (), known_attribute_ids),
        sub_stats=_stats(row.get("sub_stats") or (), known_attribute_ids),
    )


def _suit_constraint(
    suit_id: str, *, suits_by_id: Mapping[str, Mapping[str, Any]],
    known_geometry_keys: set[str],
) -> SuitConstraint:
    suit = suits_by_id.get(suit_id)
    if suit is None:
        raise AllocationContextError(f"静态数据库没有官方套装 ID：{suit_id}")
    required_shape_ids = tuple(
        _required_text(shape_id, "套装 required_shape_id")
        for shape_id in suit.get("required_shape_ids") or ()
    )
    for shape_id in required_shape_ids:
        if _geometry_key(shape_id, "套装 required_shape_id") not in known_geometry_keys:
            raise AllocationContextError(f"静态数据库没有官方形状 ID：{shape_id}")
    return SuitConstraint(
        suit_id=suit_id,
        required_shape_ids=required_shape_ids,
    )


def _role_equipment_constraints(
    static_dao: StaticGameDataDao,
    role: Mapping[str, Any],
    *, known_character_ids: set[int],
) -> RoleEquipmentConstraints:
    """Freeze only the 20 playable cells from an official equipment plan.

    The remaining plan fields are recommendations, not game rules.  Validating
    or retaining their templates here made a Context construction depend on data
    the weighted allocation solver must deliberately ignore.
    """

    character_id = int(role["character_id"])
    if character_id not in known_character_ids:
        raise AllocationContextError(f"静态数据库没有角色 {character_id}")
    blueprint = static_dao.get_equipment_plan(character_id)
    if blueprint is None:
        raise AllocationContextError(f"角色 {character_id} 没有官方角色底盘")
    cells = tuple(
        BlueprintCell(row=int(cell["row"]), column=int(cell["column"]))
        for cell in blueprint.get("cells") or ()
    )
    positions = {(cell.row, cell.column) for cell in cells}
    if len(cells) != 20 or len(positions) != 20 or any(
        row < 1 or row > 5 or column < 1 or column > 5
        for row, column in positions
    ):
        raise AllocationContextError(f"角色 {character_id} 的官方底盘必须包含 20 个唯一合法格位")
    return RoleEquipmentConstraints(character_id=character_id, cells=cells)


def _workshop_roles(config_path: str | Path) -> Mapping[str, Any]:
    """Read the current workshop-synchronized compatibility cache once.

    ``roles.json`` is not queried by the solver.  It remains the existing
    workshop-sync storage until a later product migration gives that source a
    dedicated versioned store; this boundary copies only the scoring metadata
    that old allocation behavior already consumes.
    """

    try:
        with Path(config_path).open("r", encoding="utf-8") as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise AllocationContextError("无法读取异环工坊同步的角色权重") from exc
    if not isinstance(payload, Mapping):
        raise AllocationContextError("异环工坊角色权重缓存格式无效")
    return payload


def _workshop_weight_ids(
    raw_weights: Any, *, catalog: StatCatalog, attribute_id_by_name: Mapping[str, str],
) -> dict[str, float]:
    if not isinstance(raw_weights, Mapping):
        return {}
    result: dict[str, float] = {}
    for raw_name, raw_weight in raw_weights.items():
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        name = str(raw_name or "").strip()
        normalized = catalog.normalize_stat_name(name, is_percent="%" in name)
        candidates = (name, normalized or "", catalog.flexible_weight_name(name) or "")
        property_id = next((attribute_id_by_name.get(item) for item in candidates if item), None)
        if property_id is None:
            # The workshop cache and static database use a few equivalent
            # display labels (for example “伤害增加%” / “伤害提升%”).  Resolve
            # those through the same StatCatalog alias policy used by the
            # established ScoringEngine rather than silently dropping a
            # default recommendation weight at the Context boundary.
            flexible = catalog.flexible_weight_name(name) or normalized or name
            property_id = next(
                (
                    official_id
                    for attribute_name, official_id in attribute_id_by_name.items()
                    if (catalog.flexible_weight_name(attribute_name) or attribute_name) == flexible
                ),
                None,
            )
        if property_id is not None:
            result[property_id] = weight
    return result


def _workshop_role_values(
    character_id: int,
    character_name: str,
    workshop_roles: Mapping[str, Any], *, catalog: StatCatalog,
    attribute_id_by_name: Mapping[str, str],
) -> tuple[dict[str, float], dict[str, float]]:
    character_token = str(character_id)
    raw = next(
        (
            role_data
            for role_data in workshop_roles.values()
            if isinstance(role_data, Mapping)
            and character_token in {
                str(value).strip()
                for value in (
                    [role_data.get("workshop_item_id")]
                    + list(role_data.get("workshop_item_ids") or ())
                )
                if value is not None
            }
        ),
        workshop_roles.get(character_name),
    )
    if not isinstance(raw, Mapping):
        # A missing synced role is a supported state.  It must not silently use
        # the official equipment-plan recommendation as a scoring substitute.
        return {}, {}
    return (
        _workshop_weight_ids(raw.get("weights"), catalog=catalog, attribute_id_by_name=attribute_id_by_name),
        _workshop_weight_ids(raw.get("main_weights"), catalog=catalog, attribute_id_by_name=attribute_id_by_name),
    )


def _allocation_role_values(
    user_dao: UserDataDao,
    static_dao: StaticGameDataDao,
    character_id: int,
    character_name: str,
    workshop_roles: Mapping[str, Any],
    *,
    catalog: StatCatalog | None,
    attribute_id_by_name: Mapping[str, str],
) -> tuple[dict[str, float], dict[str, float], str, dict[str, float]]:
    account_weights = user_dao.get_character_weight_preferences(character_id)
    recommended_weights = static_dao.get_character_recommended_weights(character_id)
    weight_record = account_weights or recommended_weights
    if weight_record is not None:
        weights = {
            str(property_id): float(weight)
            for property_id, weight in (
                weight_record.get("property_weights") or {}
            ).items()
        }
        main_weights = {
            str(property_id): float(weight)
            for property_id, weight in (
                weight_record.get("main_property_weights") or {}
            ).items()
        }
    elif catalog is None:
        weights = {}
        main_weights = {}
    else:
        weights, main_weights = _workshop_role_values(
            character_id,
            character_name,
            workshop_roles,
            catalog=catalog,
            attribute_id_by_name=attribute_id_by_name,
        )
    shape_bonus = static_dao.get_character_shape_bonus(character_id) or {}
    return (
        weights,
        main_weights,
        str(shape_bonus.get("shape_label") or ""),
        {
            str(row["property_id"]): float(row["display_value"])
            for row in shape_bonus.get("properties") or ()
        },
    )


def _role_preference(
    row: Mapping[str, Any],
    *,
    known_attribute_ids: set[str],
    equipment: RoleEquipmentConstraints,
    workshop_values: tuple[dict[str, float], dict[str, float], str, dict[str, float]],
    known_suit_ids: set[str],
) -> AllocationRolePreference:
    weights = row.get("property_weights") or {}
    limits = row.get("property_limits") or {}
    if not isinstance(weights, Mapping) or not isinstance(limits, Mapping):
        raise AllocationContextError("优化偏好版本包含无效的属性配置")
    profile_weights = {
        _official_attribute_id(property_id, known_attribute_ids, "属性权重 property_id"): float(weight)
        for property_id, weight in weights.items()
    }
    default_weights, default_main_weights, extra_shape_label, extra_shape_buffs = workshop_values
    effective_weights = dict(default_weights)
    effective_weights.update(profile_weights)
    effective_main_weights = dict(default_main_weights)
    # 副词条权重不能扩充卡带主词条候选；主词条仅来自角色配置的 main_weights
    # 或用户明确选择的 core_main_property_id。
    target_suit_id = _optional_text(row.get("target_suit_id"))
    suit_requirement_mode = _required_text(row.get("suit_requirement_mode"), "suit_requirement_mode")
    if target_suit_id is not None and target_suit_id not in known_suit_ids:
        raise AllocationContextError(f"静态数据库没有官方套装 ID：{target_suit_id}")
    return AllocationRolePreference(
        character_id=int(row["character_id"]),
        ordinal=int(row["ordinal"]),
        priority_group=int(row["priority_group"]),
        target_suit_id=target_suit_id,
        suit_requirement_mode=suit_requirement_mode,
        core_main_property_id=(
            _official_attribute_id(
                row["core_main_property_id"], known_attribute_ids, "核心主词条 property_id"
            )
            if row.get("core_main_property_id") is not None
            else None
        ),
        property_weights=tuple(sorted(profile_weights.items())),
        substat_priorities=tuple(
            _official_attribute_id(property_id, known_attribute_ids, "副词条优先级 property_id")
            for property_id in row.get("substat_priorities") or ()
        ),
        property_limits=tuple(
            PropertyLimit(
                property_id=_official_attribute_id(
                    property_id, known_attribute_ids, "属性限制 property_id"
                ),
                minimum=(float(bounds["minimum"]) if bounds.get("minimum") is not None else None),
                maximum=(float(bounds["maximum"]) if bounds.get("maximum") is not None else None),
            )
            for property_id, bounds in sorted(limits.items())
        ),
        equipment=equipment,
        effective_property_weights=tuple(sorted(effective_weights.items())),
        effective_main_property_weights=tuple(sorted(effective_main_weights.items())),
        extra_shape_label=extra_shape_label,
        extra_shape_buffs=tuple(sorted(extra_shape_buffs.items())),
    )


def build_allocation_context(
    user_dao: UserDataDao,
    static_dao: StaticGameDataDao,
    *,
    snapshot_id: int,
    profile_id: int,
    profile_version: int,
    solver_version: str = ALLOCATION_CONTEXT_SOLVER_VERSION,
    workshop_roles_path: str | Path | None = None,
) -> AllocationContext:
    """Copy one exact account, dataset, snapshot and preference version into memory.

    ``snapshot_id`` and ``profile_version`` are intentionally mandatory.  Callers
    must make the selection before this boundary; this function never follows a
    moving “current snapshot” or “latest preference” pointer.
    """

    pinned_snapshot_id = int(snapshot_id)
    pinned_profile_id = int(profile_id)
    pinned_profile_version = int(profile_version)
    pinned_solver_version = _required_text(solver_version, "solver_version")
    if min(pinned_snapshot_id, pinned_profile_id, pinned_profile_version) < 1:
        raise AllocationContextError("snapshot_id、profile_id 和 profile_version 必须大于 0")

    account = user_dao.profile()
    try:
        snapshot, inventory_rows = user_dao.export_inventory_snapshot(pinned_snapshot_id)
    except Exception as exc:
        raise AllocationContextError(f"无法固定背包快照：{pinned_snapshot_id}") from exc
    profile = user_dao.get_optimization_profile(
        pinned_profile_id, version_number=pinned_profile_version
    )
    if profile is None or profile.get("version") is None:
        raise AllocationContextError(
            f"优化偏好版本不存在：profile_id={pinned_profile_id}, version={pinned_profile_version}"
        )
    version = profile["version"]
    static_summary = static_dao.summary()
    dataset = static_summary.get("dataset") or {}
    raw_attributes = static_dao.list_equipment_attributes()
    known_attribute_ids = {
        _required_text(attribute.get("attribute_id"), "静态装备属性 ID")
        for attribute in raw_attributes
    }
    attribute_id_by_name: dict[str, str] = {}
    official_attributes: list[OfficialAttribute] = []
    for attribute in raw_attributes:
        attribute_id = _required_text(attribute.get("attribute_id"), "静态装备属性 ID")
        display_name = _required_text(attribute.get("display_name_zh"), "静态装备属性显示名")
        scoring_name = display_name + ("%" if bool(attribute.get("show_percent")) else "")
        official_attributes.append(OfficialAttribute(attribute_id, scoring_name))
        for name in (display_name, scoring_name, attribute.get("filter_name_zh")):
            if name:
                attribute_id_by_name.setdefault(str(name), attribute_id)
    character_rows = static_dao.list_characters()
    character_name_by_id = {int(character["character_id"]): str(character.get("name_zh") or "") for character in character_rows}
    known_character_ids = set(character_name_by_id)
    workshop_roles: Mapping[str, Any] = {}
    catalog: StatCatalog | None = None
    if workshop_roles_path is not None:
        workshop_roles = _workshop_roles(workshop_roles_path)
        config_directory = Path(workshop_roles_path).parent
        catalog = StatCatalog.from_config_dir(config_directory)
    suits_by_id = {
        _required_text(suit.get("suit_id"), "官方套装 ID"): suit
        for suit in static_dao.list_suits()
    }
    raw_shapes = static_dao.list_shapes()
    known_geometry_keys = {
        _geometry_key(shape.get("shape_id"), "官方形状 ID")
        for shape in raw_shapes
    }
    official_suits = tuple(
        _suit_constraint(
            suit_id, suits_by_id=suits_by_id,
            known_geometry_keys=known_geometry_keys,
        )
        for suit_id in sorted(suits_by_id)
    )
    official_shapes = tuple(
        OfficialShape(
            shape_id=_required_text(shape.get("shape_id"), "官方形状 ID"),
            cell_count=int(shape.get("cell_count") or 0),
            cells=tuple(
                OfficialShapeCell(x=int(cell["x"]), y=int(cell["y"]))
                for cell in shape.get("cells") or ()
            ),
            legacy_shape_id=legacy_shape_id(shape.get("shape_id")),
            legacy_label=_LEGACY_SHAPE_LABELS.get(legacy_shape_id(shape.get("shape_id"))),
        )
        for shape in raw_shapes
    )
    equipment_by_id = {
        _required_text(template.get("item_id"), "官方装备模板 item_id"): template
        for template in static_dao.list_equipment_items()
    }
    role_rows = sorted(
        version.get("characters") or (), key=lambda value: int(value["ordinal"])
    )
    roles = tuple(
        _role_preference(
            row,
            known_attribute_ids=known_attribute_ids,
            equipment=_role_equipment_constraints(
                static_dao, row, known_character_ids=known_character_ids,
            ),
            workshop_values=(
                _allocation_role_values(
                    user_dao,
                    static_dao,
                    int(row["character_id"]),
                    character_name_by_id.get(int(row["character_id"]), ""), workshop_roles,
                    catalog=catalog, attribute_id_by_name=attribute_id_by_name,
                )
            ),
            known_suit_ids=set(suits_by_id),
        )
        for row in role_rows
    )
    candidates = tuple(
        _candidate(
            row, known_attribute_ids=known_attribute_ids,
            equipment_by_id=equipment_by_id, known_suit_ids=set(suits_by_id),
            known_geometry_keys=known_geometry_keys,
            known_character_ids=known_character_ids,
        )
        for row in inventory_rows
    )
    return AllocationContext(
        account_id=_required_text(account.get("account_id"), "account_id"),
        static_dataset=StaticDatasetReference(
            schema_version=int(static_summary["schema_version"]),
            dataset_id=_required_text(dataset.get("dataset_id"), "静态数据集 dataset_id"),
            importer_version=int(dataset["importer_version"]),
            built_at_utc=_required_text(dataset.get("built_at_utc"), "静态数据集 built_at_utc"),
        ),
        snapshot=InventorySnapshotReference(
            snapshot_id=int(snapshot["snapshot_id"]),
            source=_required_text(snapshot.get("source"), "背包快照 source"),
            generation=_optional_int(snapshot.get("generation")),
            sequence=_optional_int(snapshot.get("sequence")),
            observed_at_unix_ms=_optional_int(snapshot.get("observed_at_unix_ms")),
            captured_at_utc=_required_text(snapshot.get("captured_at_utc"), "背包快照 captured_at_utc"),
            declared_item_count=int(snapshot["declared_item_count"]),
            stored_item_count=int(snapshot["stored_item_count"]),
        ),
        profile_id=pinned_profile_id,
        profile_version=pinned_profile_version,
        allocation_strategy=_required_text(version.get("allocation_strategy"), "allocation_strategy"),
        solver_version=pinned_solver_version,
        roles=roles,
        candidates=candidates,
        shapes=official_shapes,
        suits=official_suits,
        attributes=tuple(sorted(official_attributes, key=lambda attribute: attribute.property_id)),
    )
