# 兼容门面：公开的 UserDataDao 继续提供完整账号数据接口。
"""SQLite user-data facade composed from focused DAO mixins."""

from __future__ import annotations

from .account_data_dao import AccountDataDaoMixin
from .equipment_apply_job_dao import EquipmentApplyJobDaoMixin
from .inventory_snapshot_dao import InventorySnapshotDaoMixin
from .loadout_plan_dao import LoadoutPlanDaoMixin
from .optimization_profile_dao import OptimizationProfileDaoMixin
from .user_data_base import UserDataDaoCore
from .user_data_support import (
    ALLOCATION_STRATEGIES,
    BASE_SCHEMA_VERSION,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SNAPSHOT_RETENTION_COUNT,
    SCHEMA_VERSION,
    SNAPSHOT_SOURCES,
    SUIT_REQUIREMENT_MODES,
    SYNC_METHODS,
    USER_MIGRATIONS,
    UserDataError,
    UserDataValidationError,
)


class UserDataDao(
    AccountDataDaoMixin,
    OptimizationProfileDaoMixin,
    InventorySnapshotDaoMixin,
    EquipmentApplyJobDaoMixin,
    LoadoutPlanDaoMixin,
    UserDataDaoCore,
):
    """单个应用账号的数据访问门面；所有 mixin 共享同一 SQLite 连接。"""


__all__ = [
    "ALLOCATION_STRATEGIES", "BASE_SCHEMA_VERSION", "DEFAULT_SCHEMA_PATH",
    "DEFAULT_SNAPSHOT_RETENTION_COUNT", "SCHEMA_VERSION", "SNAPSHOT_SOURCES",
    "SUIT_REQUIREMENT_MODES", "SYNC_METHODS", "USER_MIGRATIONS", "UserDataDao",
    "UserDataError", "UserDataValidationError",
]
