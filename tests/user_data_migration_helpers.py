# 用户数据库降级迁移 fixture 的结构回退工具。
from __future__ import annotations

import sqlite3


def drop_battle_axis_v23(connection: sqlite3.Connection) -> None:
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
