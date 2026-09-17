# 用户数据库降级迁移 fixture 的结构回退工具。
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite import user_data_base
from src.storage.sqlite.user_data_dao import UserDataDao


def create_user_database_at_version(
    database: Path,
    version: int,
    *,
    account_id: str = "migration-account",
    account_name: str = "迁移测试账号",
) -> None:
    """从基础 schema 顺序迁移到指定版本，避免制造新旧结构混杂的假历史库。"""

    with patch.object(user_data_base, "SCHEMA_VERSION", version):
        with UserDataDao(
            database,
            account_id=account_id,
            account_name=account_name,
        ):
            pass


def migrate_user_database_to_version(database: Path, version: int) -> None:
    """把已有真实旧版夹具顺序迁移到另一个历史版本。"""

    with patch.object(user_data_base, "SCHEMA_VERSION", version):
        with UserDataDao(database):
            pass


def insert_optimization_profile(
    database: Path,
    *,
    name: str,
    character: dict[str, object] | None = None,
) -> None:
    """向真实旧版结构写入迁移所需的最小优化配置事实。"""

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            """INSERT INTO optimization_preference_profile(
                   name, is_active, created_at_utc, updated_at_utc
               ) VALUES (?, 1, 'now', 'now')""",
            (name,),
        )
        profile_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """INSERT INTO optimization_preference_version(
                   profile_id, version_number, allocation_strategy, created_at_utc
               ) VALUES (?, 1, 'role_priority', 'now')""",
            (profile_id,),
        )
        version_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        if character is not None:
            character_id = int(character["character_id"])
            connection.execute(
                """INSERT INTO optimization_preference_character(
                       profile_version_id, character_id, ordinal, priority_group,
                       target_suit_id, suit_requirement_mode, core_main_property_id
                   ) VALUES (?, ?, 0, 0, NULL, 'none', NULL)""",
                (version_id, character_id),
            )
            for ordinal, property_id in enumerate(character.get("substat_priorities", ())):
                connection.execute(
                    """INSERT INTO optimization_preference_substat_priority(
                           profile_version_id, character_id, property_id, ordinal
                       ) VALUES (?, ?, ?, ?)""",
                    (version_id, character_id, str(property_id), ordinal),
                )
            for ordinal, property_id in enumerate(character.get("substat_blacklist", ())):
                connection.execute(
                    """INSERT INTO optimization_preference_substat_blacklist(
                           profile_version_id, character_id, property_id, ordinal
                       ) VALUES (?, ?, ?, ?)""",
                    (version_id, character_id, str(property_id), ordinal),
                )
        connection.commit()
    finally:
        connection.close()


def drop_battle_axis_v23(connection: sqlite3.Connection) -> None:
    # Remove every table added after v23 before dropping its referenced axis
    # parents.  These fixtures start from the current schema and then replay
    # the append-only migration chain from an older version.
    for table in (
        "battle_inferred_target_snapshot",
        "battle_character_import_equipment_lock",
        "battle_report_import_origin",
    ):
        connection.execute(f"DROP TABLE IF EXISTS {table}")
    connection.execute("DROP TABLE IF EXISTS battle_target_condition")
    for table in (
        "battle_character_awaken_edit",
        "battle_character_skill_edit",
        "battle_character_build_edit",
        "battle_build_edit",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.execute("DROP TABLE battle_character_stat_snapshot")
    for column in (
        "analysis_character_id",
        "analysis_end_us",
        "analysis_start_us",
    ):
        connection.execute(
            f"ALTER TABLE battle_report_page_state DROP COLUMN {column}"
        )
    for column in (
        "name_mapping_version",
        "formula_model_version",
    ):
        connection.execute(f"ALTER TABLE battle_build_snapshot DROP COLUMN {column}")
    for column in (
        "follow_up_damage_attribute",
        "follow_up_attack_type",
        "follow_up_damage_component",
        "follow_up_damage_name",
        "target_context_json",
    ):
        connection.execute(f"ALTER TABLE battle_hit_evidence DROP COLUMN {column}")
    connection.execute("DROP TABLE character_profile_awaken_effect")
    connection.execute(
        "ALTER TABLE character_profile DROP COLUMN fork_breakthrough_stage"
    )
    for column in (
        "awakening_selection_initialized",
        "likeability_level_10_enabled",
    ):
        connection.execute(f"ALTER TABLE character_profile DROP COLUMN {column}")
    for table in (
        "battle_equipment_stat_snapshot",
        "battle_equipment_snapshot",
        "battle_character_skill_snapshot",
        "battle_character_build_snapshot",
        "battle_build_snapshot",
        "battle_time_stop_interval",
        "battle_hit_evidence",
        "battle_axis_capture",
    ):
        connection.execute(f"DROP TABLE {table}")
    for column in (
        "nte_core_executable_sha256",
        "nte_core_data_version",
        "nte_core_protocol_version",
        "nte_core_version",
        "axis_stored_hits",
        "axis_total_hits",
        "axis_first_sequence",
        "axis_complete",
        "nte_core_contract_version",
        "nte_core_record_id",
        "evidence_capability_level",
        "evidence_source_kind",
    ):
        connection.execute(f"ALTER TABLE battle_record DROP COLUMN {column}")
