# 提供游戏资料库 125 张发行静态表的固定只读登记。
"""Fixed-table coverage queries for the release static catalog overview."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.storage.sqlite.static_game_data_dao import StaticGameDataError, resolve_static_database
from src.storage.sqlite.static_game_data_metadata import (
    MINIMUM_SUPPORTED_SCHEMA_VERSION,
    SCHEMA_VERSION,
)


# 表名只来自本固定登记，绝不接受 UI 输入。状态是独立覆盖审计结论：
# A=完整正式目录，B=已公开但仍有高级字段，C=可展示且存在结构化缺口，
# D=只有 ID/有限证据，E=正式发行中为空或 payload 明确省略。
STATIC_TABLE_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("dataset", "元数据与来源", "B"),
    ("schema_migration", "元数据与来源", "B"),
    ("source_file", "元数据与来源", "C"),
    ("source_row", "元数据与来源", "E"),
    ("application_setting_default", "元数据与来源", "B"),
    ("localized_term", "元数据与来源", "B"),
    ("localized_term_name", "元数据与来源", "A"),
    ("character", "角色与养成", "A"),
    ("character_release_evidence", "角色与养成", "B"),
    ("character_release_annotation", "角色与养成", "B"),
    ("character_release_evidence_link", "角色与养成", "B"),
    ("character_acquisition_membership", "角色与养成", "A"),
    ("character_annotation", "角色与养成", "B"),
    ("character_awaken_effect", "角色与养成", "A"),
    ("character_awaken_skill_level_bonus", "角色与养成", "A"),
    ("character_likeability_bonus", "角色与养成", "B"),
    ("character_likeability_bonus_property", "角色与养成", "B"),
    ("character_panel_growth", "角色与养成", "A"),
    ("character_skill", "角色与养成", "A"),
    ("character_skill_level", "角色与养成", "B"),
    ("character_cultivation_guide", "角色与养成", "C"),
    ("character_cultivation_fork_recommendation", "角色与养成", "B"),
    ("character_cultivation_attribute_recommendation", "角色与养成", "C"),
    ("character_cultivation_stage", "角色与养成", "C"),
    ("character_cultivation_stage_skill", "角色与养成", "B"),
    ("character_weight_recommendation", "角色与养成", "B"),
    ("character_weight_recommendation_property", "角色与养成", "B"),
    ("character_graduation_template", "角色与养成", "B"),
    ("character_shape_bonus", "角色与养成", "E"),
    ("character_shape_bonus_property", "角色与养成", "E"),
    ("logical_character_shape_bonus", "角色与养成", "A"),
    ("logical_character_shape_bonus_property", "角色与养成", "B"),
    ("fork_type", "弧盘", "B"),
    ("fork_item", "弧盘", "A"),
    ("fork_modify_pack", "弧盘", "B"),
    ("fork_modify_value", "弧盘", "A"),
    ("fork_upgrade_level", "弧盘", "B"),
    ("fork_breakthrough", "弧盘", "A"),
    ("fork_refinement_parameter_value", "弧盘", "A"),
    ("fork_permanent_property", "弧盘", "A"),
    ("fork_star_level", "弧盘", "A"),
    ("fork_star_parameter", "弧盘", "A"),
    ("fork_lottery_campaign", "弧盘", "A"),
    ("progression_item", "角色与养成", "A"),
    ("progression_item_alias", "角色与养成", "B"),
    ("item_quality_term", "角色与养成", "A"),
    ("equipment_attribute", "装备与套装", "A"),
    ("equipment_base_attribute_curve", "装备与套装", "B"),
    ("equipment_base_attribute_point", "装备与套装", "B"),
    ("equipment_buff_curve", "装备与套装", "B"),
    ("equipment_buff_curve_point", "装备与套装", "B"),
    ("equipment_core_random_attribute", "装备与套装", "D"),
    ("equipment_item", "装备与套装", "A"),
    ("equipment_strength_level", "装备与套装", "D"),
    ("equipment_modify_pack", "装备与套装", "B"),
    ("equipment_modify_value", "装备与套装", "B"),
    ("equipment_plan", "装备与套装", "A"),
    ("equipment_plan_cell", "装备与套装", "A"),
    ("equipment_plan_core_attribute", "装备与套装", "B"),
    ("equipment_plan_module", "装备与套装", "A"),
    ("equipment_plan_recommended_attribute", "装备与套装", "B"),
    ("equipment_shape", "装备与套装", "A"),
    ("equipment_shape_cell", "装备与套装", "A"),
    ("equipment_suit", "装备与套装", "A"),
    ("equipment_suit_effect", "装备与套装", "A"),
    ("equipment_suit_required_shape", "装备与套装", "B"),
    ("gameplay_ability_catalog", "技能与正式标识", "B"),
    ("gameplay_ability_description", "技能与正式标识", "C"),
    ("gameplay_ability_level_hint", "技能与正式标识", "B"),
    ("gameplay_effect_catalog", "技能与正式标识", "B"),
    ("skill_damage", "技能与正式标识", "A"),
    ("skill_damage_modifier", "技能与正式标识", "B"),
    ("character_combat_ability_binding", "技能与正式标识", "B"),
    ("combat_ability_effect_binding", "技能与正式标识", "B"),
    ("combat_ability_montage_binding", "技能与正式标识", "B"),
    ("combat_level_curve", "公式与效果证据", "B"),
    ("combat_level_curve_point", "公式与效果证据", "B"),
    ("reaction_definition", "公式与效果证据", "C"),
    ("combat_effect_constant", "公式与效果证据", "C"),
    ("damage_resistance_term", "公式与效果证据", "A"),
    ("combat_effect_definition", "公式与效果证据", "B"),
    ("combat_effect_buff_link", "公式与效果证据", "B"),
    ("combat_curve", "公式与效果证据", "B"),
    ("combat_curve_point", "公式与效果证据", "B"),
    ("buff_definition", "公式与效果证据", "B"),
    ("buff_modifier", "公式与效果证据", "B"),
    ("buff_trigger_effect", "公式与效果证据", "B"),
    ("roguelike_modifier_profile", "公式与效果证据", "C"),
    ("roguelike_modifier_property", "公式与效果证据", "C"),
    ("combat_blueprint_asset", "蓝图与动画证据", "B"),
    ("combat_blueprint_reference", "蓝图与动画证据", "B"),
    ("combat_blueprint_semantic_property", "蓝图与动画证据", "B"),
    ("combat_blueprint_tag", "蓝图与动画证据", "B"),
    ("combat_montage", "蓝图与动画证据", "B"),
    ("combat_montage_section", "蓝图与动画证据", "B"),
    ("combat_montage_notify", "蓝图与动画证据", "B"),
    ("monster_catalog", "怪物与画像", "A"),
    ("monster_identifier_alias", "怪物与画像", "B"),
    ("monster_template_binding", "怪物与画像", "B"),
    ("monster_boss_support", "怪物与画像", "B"),
    ("monster_instance_profile", "怪物与画像", "B"),
    ("monster_instance_profile_variant", "怪物与画像", "B"),
    ("enemy_combat_profile", "怪物与画像", "B"),
    ("enemy_element_resistance", "怪物与画像", "B"),
    ("abyss_level", "玩法与遭遇", "A"),
    ("abyss_level_monster_spawn", "玩法与遭遇", "B"),
    ("abyss_monster_pool_entry", "玩法与遭遇", "B"),
    ("clone_activity_category", "玩法与遭遇", "A"),
    ("clone_activity", "玩法与遭遇", "B"),
    ("clone_activity_difficulty", "玩法与遭遇", "B"),
    ("clone_drop_projection", "玩法与遭遇", "B"),
    ("clone_drop_projection_item", "玩法与遭遇", "A"),
    ("clone_drop_projection_gap", "玩法与遭遇", "C"),
    ("clone_spawn_member", "玩法与遭遇", "B"),
    ("feast_stage", "玩法与遭遇", "A"),
    ("feast_stage_difficulty", "玩法与遭遇", "B"),
    ("feast_option", "玩法与遭遇", "A"),
    ("feast_stage_option", "玩法与遭遇", "A"),
    ("divination_buff", "玩法与遭遇", "A"),
    ("outer_realm_rotation", "玩法与遭遇", "A"),
    ("outer_realm_season_buff", "玩法与遭遇", "A"),
    ("outer_realm_season_buff_component", "玩法与遭遇", "B"),
    ("high_risk_commission", "玩法与遭遇", "B"),
    ("high_risk_commission_difficulty", "玩法与遭遇", "B"),
    ("high_risk_monster_pool_member", "玩法与遭遇", "B"),
)


@dataclass(frozen=True, slots=True)
class StaticTableOverview:
    name: str
    domain: str
    coverage_state: str
    rows: int


class StaticCatalogOverviewQueries:
    """Count every registered static table through a schema-checked RO handle."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = resolve_static_database(database_path)
        try:
            self._connection = sqlite3.connect(
                f"{self.database_path.as_uri()}?mode=ro", uri=True
            )
        except sqlite3.Error as exc:
            raise StaticGameDataError("无法只读打开资料库覆盖总览") from exc
        version = self._connection.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()[0]
        self._schema_version = int(version or 0)
        if not MINIMUM_SUPPORTED_SCHEMA_VERSION <= self._schema_version <= SCHEMA_VERSION:
            self.close()
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；支持 "
                f"{MINIMUM_SUPPORTED_SCHEMA_VERSION} 至 {SCHEMA_VERSION}"
            )

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def list_tables(self) -> tuple[StaticTableOverview, ...]:
        if self._connection is None:
            raise StaticGameDataError("资料库覆盖总览连接已关闭")
        available_tables = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        rows: list[StaticTableOverview] = []
        for name, domain, state in STATIC_TABLE_CATALOG:
            if name not in available_tables:
                continue
            count = self._connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            rows.append(StaticTableOverview(name, domain, state, int(count)))
        return tuple(rows)
