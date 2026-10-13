# 公开账号数据门面：各领域 mixin 沿用同一连接及事务 owner。
"""SQLite user-data facade composed from focused DAO mixins."""

from __future__ import annotations

from .equipment.loadout_management_dao import LoadoutManagementDaoMixin

from src.storage.sqlite.accounts.account_data_dao import AccountDataDaoMixin
from src.storage.sqlite.inventory.all_item_snapshot_dao import AllItemSnapshotDaoMixin
from src.storage.sqlite.battle_report.battle_report_dao import BattleReportDaoMixin
from src.storage.sqlite.battle_report.battle_report_transfer_dao import BattleReportTransferDaoMixin
from src.storage.sqlite.battle_report.battle_axis_dao import BattleAxisDaoMixin
from src.storage.sqlite.battle_report.battle_axis_finalization_dao import BattleAxisFinalizationDaoMixin
from src.storage.sqlite.battle_report.battle_analysis_dao import BattleAnalysisDaoMixin
from src.storage.sqlite.battle_report.battle_inferred_target_dao import BattleInferredTargetDaoMixin
from src.storage.sqlite.battle_report.battle_build_edit_dao import BattleBuildEditDaoMixin
from src.storage.sqlite.characters.custom_character_dao import CustomCharacterDaoMixin
from src.storage.sqlite.progression.cultivation_history_dao import CultivationHistoryDaoMixin
from src.storage.sqlite.equipment.equipment_apply_job_dao import EquipmentApplyJobDaoMixin
from src.storage.sqlite.inventory.inventory_snapshot_dao import InventorySnapshotDaoMixin
from src.storage.sqlite.inventory.inventory_runtime_state_dao import InventoryRuntimeStateDaoMixin
from src.storage.sqlite.equipment.loadout_plan_lock_dao import LoadoutPlanLockDaoMixin
from src.storage.sqlite.equipment.loadout_plan_dao import LoadoutPlanDaoMixin
from src.storage.sqlite.equipment.loadout_slot_dao import LoadoutSlotDaoMixin
from src.storage.sqlite.equipment.optimization_profile_dao import OptimizationProfileDaoMixin
from src.storage.sqlite.inventory.packet_item_observation_dao import PacketItemObservationDaoMixin
from src.storage.sqlite.characters.native_character_profile_dao import NativeCharacterProfileDaoMixin
from src.storage.sqlite.sharing.practice_share_dao import PracticeShareDaoMixin
from src.storage.sqlite.user_data_base import UserDataDaoCore
from src.storage.sqlite.user_data_support import (
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
    LoadoutManagementDaoMixin,
    PracticeShareDaoMixin,
    CultivationHistoryDaoMixin,
    PacketItemObservationDaoMixin,
    AllItemSnapshotDaoMixin,
    NativeCharacterProfileDaoMixin,
    CustomCharacterDaoMixin,
    AccountDataDaoMixin,
    BattleInferredTargetDaoMixin,
    BattleAnalysisDaoMixin,
    BattleBuildEditDaoMixin,
    BattleAxisFinalizationDaoMixin,
    BattleAxisDaoMixin,
    BattleReportTransferDaoMixin,
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
