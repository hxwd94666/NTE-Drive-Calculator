# 兼容门面：公开的 UserDataDao 继续提供完整账号数据接口。
"""SQLite user-data facade composed from focused DAO mixins."""

from __future__ import annotations

from .account_data_dao import AccountDataDaoMixin
from .battle_report_dao import BattleReportDaoMixin
from .battle_axis_dao import BattleAxisDaoMixin
from .custom_character_dao import CustomCharacterDaoMixin
from .equipment_apply_job_dao import EquipmentApplyJobDaoMixin
from .inventory_snapshot_dao import InventorySnapshotDaoMixin
from .inventory_runtime_state_dao import InventoryRuntimeStateDaoMixin
from .loadout_plan_lock_dao import LoadoutPlanLockDaoMixin
from .loadout_plan_dao import LoadoutPlanDaoMixin
from .loadout_slot_dao import LoadoutSlotDaoMixin
from .optimization_profile_dao import OptimizationProfileDaoMixin
from .user_data_base import UserDataDaoCore
from .user_data_support import (
    ALLOCATION_STRATEGIES,
    BASE_SCHEMA_VERSION,
    BATTLE_REPORT_MAX_MANUAL_RECORDS,
    BATTLE_REPORT_MAX_RECORDS,
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
    CustomCharacterDaoMixin,
    AccountDataDaoMixin,
    BattleAxisDaoMixin,
    BattleReportDaoMixin,
    OptimizationProfileDaoMixin,
    InventoryRuntimeStateDaoMixin,
    InventorySnapshotDaoMixin,
    EquipmentApplyJobDaoMixin,
    LoadoutPlanLockDaoMixin,
    LoadoutSlotDaoMixin,
    LoadoutPlanDaoMixin,
    UserDataDaoCore,
):
    """单个应用账号的数据访问门面；所有 mixin 共享同一 SQLite 连接。"""


__all__ = [
    "ALLOCATION_STRATEGIES", "BASE_SCHEMA_VERSION",
    "BATTLE_REPORT_MAX_MANUAL_RECORDS", "BATTLE_REPORT_MAX_RECORDS",
    "DEFAULT_SCHEMA_PATH",
    "DEFAULT_SNAPSHOT_RETENTION_COUNT", "SCHEMA_VERSION", "SNAPSHOT_SOURCES",
    "SUIT_REQUIREMENT_MODES", "SYNC_METHODS", "USER_MIGRATIONS", "UserDataDao",
    "UserDataError", "UserDataValidationError",
]
