# 冻结分配所需的身份和数值，不解析最新快照或创建服务。
"""唯一分配输入类型；既有评分DTO的定义导入不等于业务文件或DAO读取。"""

from __future__ import annotations

from dataclasses import dataclass
from src.domain.allocation.scoring_rules import AllocationScoringRules


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
    generation: int | None  # 采集快照字段，不等同于账号AppContext代次。
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
    substat_blacklist: tuple[str, ...] = ()
    blacklist_zero_weight: bool = False
    equal_substat_priority: bool = False
    ignore_grade_limit: bool = False
    min_grade_limit: str = "A"
    crit_threshold: float | None = None
    # 有效权重已由账号profile或同一静态默认冻结；求解侧不再读取配置补齐。
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
    """同一次正式分配、候选评分与预览消费的不可变输入。"""

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
    scoring_rules: AllocationScoringRules
    attributes: tuple[OfficialAttribute, ...] = ()

    def __post_init__(self):
        if not isinstance(self.scoring_rules, AllocationScoringRules):
            raise TypeError("AllocationContext必须显式固定评分规则")

    @property
    def scoring_rule_sha256(self) -> str:
        return self.scoring_rules.rule_sha256
