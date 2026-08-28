# 提供标准化静态游戏数据库的只读访问层。
"""标准化静态游戏数据库的只读访问层。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable

from .static_game_data_metadata import SCHEMA_VERSION, SUMMARY_TABLES

STATIC_DATABASE_ENV = "NTE_GAME_STATIC_DB"
_DEFAULT_LOGICAL_CHARACTER_IDS = {"protagonist": 1051}
_ROLE_TEMPLATE_CLASSIFICATIONS = {
    "available_character",
    "scheduled_character",
    "playable",
}

class StaticGameDataError(RuntimeError):
    """静态数据库缺失或版本不兼容。"""


def static_database_candidates() -> list[Path]:
    """按优先级返回开发环境和打包环境中的静态数据库候选路径。"""

    candidates: list[Path] = []
    configured = os.environ.get(STATIC_DATABASE_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())

    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "data" / "game_static.sqlite3")

    executable_dir = Path(sys.executable).resolve().parent
    project_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        (
            executable_dir / "data" / "game_static.sqlite3",
            project_root / "data" / "game_static.sqlite3",
            project_root / "build_resources" / "game_static.sqlite3",
        )
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def resolve_static_database(database_path: str | Path | None = None) -> Path:
    """解析显式路径、环境变量或随程序提供的静态数据库。"""

    if database_path is not None:
        resolved = Path(database_path).expanduser().resolve()
        if resolved.is_file():
            return resolved
        raise StaticGameDataError(f"静态数据库不存在：{resolved}")
    for candidate in static_database_candidates():
        if candidate.is_file():
            return candidate
    checked = "、".join(str(path) for path in static_database_candidates())
    raise StaticGameDataError(f"找不到静态数据库；已检查：{checked}")


from src.storage.sqlite.static_game_data_combat_blueprint_queries import (
    StaticGameDataCombatBlueprintQueriesMixin,
)
from src.storage.sqlite.static_game_data_buff_queries import (
    StaticGameDataBuffQueriesMixin,
)
from src.storage.sqlite.static_game_data_extended_queries import StaticGameDataExtendedQueriesMixin
from src.storage.sqlite.static_game_data_encounter_queries import (
    StaticGameDataEncounterQueriesMixin,
)


class StaticGameDataDao(
    StaticGameDataEncounterQueriesMixin,
    StaticGameDataBuffQueriesMixin,
    StaticGameDataCombatBlueprintQueriesMixin,
    StaticGameDataExtendedQueriesMixin,
):
    """面向 schema v29 静态数据库的轻量查询边界。

    连接始终使用 SQLite 只读模式，避免界面或计算代码意外修改开发者生成的数据包。
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = resolve_static_database(database_path)
        uri = f"{self.database_path.as_uri()}?mode=ro"
        try:
            self._connection: sqlite3.Connection | None = sqlite3.connect(
                uri,
                uri=True,
            )
        except sqlite3.Error as exc:
            raise StaticGameDataError(
                f"无法打开静态数据库：{self.database_path}"
            ) from exc
        self._connection.row_factory = sqlite3.Row
        try:
            version_row = self._connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration"
            ).fetchone()
        except sqlite3.Error as exc:
            self.close()
            raise StaticGameDataError("文件不是 NTE 静态游戏数据库") from exc
        version = version_row["version"] if version_row is not None else None
        if version != SCHEMA_VERSION:
            self.close()
            raise StaticGameDataError(
                f"不支持的静态数据库结构版本：{version!r}；需要 {SCHEMA_VERSION}"
            )

    def __enter__(self) -> "StaticGameDataDao":
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
            self._connection = None

    def _rows(self, sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        if self._connection is None:
            raise StaticGameDataError("静态数据库 DAO 已关闭")
        return [dict(row) for row in self._connection.execute(sql, tuple(parameters))]

    def _one(self, sql: str, parameters: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, parameters)
        return rows[0] if rows else None

    def summary(self) -> dict[str, Any]:
        dataset = self._one(
            "SELECT dataset_id, importer_version, built_at_utc FROM dataset"
        )
        if dataset is None:
            raise StaticGameDataError("静态数据库缺少数据集元信息")
        counts = {}
        for table in SUMMARY_TABLES:
            row = self._one(f"SELECT COUNT(*) AS count FROM {table}")
            counts[table] = int((row or {}).get("count", 0))
        return {
            "schema_version": SCHEMA_VERSION,
            "database_path": str(self.database_path),
            "dataset": dataset,
            "counts": counts,
        }

    def application_setting_defaults(self) -> dict[str, dict[str, Any]]:
        defaults: dict[str, dict[str, Any]] = {}
        for row in self._rows(
            "SELECT setting_key, value_json FROM application_setting_default ORDER BY setting_key"
        ):
            try:
                value = json.loads(row["value_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StaticGameDataError(
                    f"静态设置默认值 {row['setting_key']!r} 不是有效 JSON"
                ) from exc
            if not isinstance(value, dict):
                raise StaticGameDataError(
                    f"静态设置默认值 {row['setting_key']!r} 不是 JSON 对象"
                )
            defaults[str(row["setting_key"])] = value
        return defaults

    def get_character_shape_bonus(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        row = self._one(
            """
            SELECT a.character_id, a.logical_character_key,
                   b.representative_character_id, b.shape_label,
                   b.shape_grid_count, b.source_kind
            FROM character_annotation AS a
            JOIN logical_character_shape_bonus AS b
              ON b.logical_character_key = a.logical_character_key
            WHERE a.character_id = ?
            """,
            (int(character_id),),
        )
        if row is None:
            return None
        row["properties"] = self._rows(
            """
            SELECT p.property_id, p.display_value, p.ordinal,
                   a.display_name_zh, a.filter_name_zh, a.show_percent
            FROM logical_character_shape_bonus_property AS p
            JOIN equipment_attribute AS a
              ON a.attribute_id = p.property_id
            WHERE p.logical_character_key = ?
            ORDER BY p.ordinal
            """,
            (str(row["logical_character_key"]),),
        )
        return row

    def list_characters(self) -> list[dict[str, Any]]:
        return self._rows(
            """
            SELECT c.character_id, c.name_zh, c.name_text_table, c.name_text_key,
                   c.element_type, c.group_type, c.actor_path, c.mainland_show_time,
                   c.source_row_id, a.logical_character_key,
                   a.canonical_character_id, a.classification, a.annotation_source
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            ORDER BY c.character_id
            """
        )

    def get_character(self, character_id: int) -> dict[str, Any] | None:
        return self._one(
            """
            SELECT c.character_id, c.name_zh, c.name_text_table, c.name_text_key,
                   c.element_type, c.group_type, c.actor_path, c.mainland_show_time,
                   c.source_row_id, a.logical_character_key,
                   a.canonical_character_id, a.classification, a.annotation_source
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            WHERE c.character_id = ?
            """,
            (character_id,),
        )

    def get_logical_character_key(self, character_id: int) -> str | None:
        """Resolve an actual character or transformation ID to its shared rule key."""

        row = self._one(
            """
            SELECT logical_character_key
            FROM character_annotation
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        return str(row["logical_character_key"]) if row is not None else None

    def list_role_template_characters(
        self,
        preferred_character_ids: Iterable[int] = (),
    ) -> list[dict[str, Any]]:
        """Return one actual character row for every logical role.

        Account-observed avatar IDs win for variant-only roles.  With no
        account evidence the protagonist uses the female official ID 1051.
        Combat transformations resolve through their logical key but never
        create an additional role-menu entry.
        """

        characters = self.list_characters()
        by_id = {
            int(character["character_id"]): character
            for character in characters
        }
        selected: dict[str, dict[str, Any]] = {}
        for character in characters:
            if character.get("classification") not in _ROLE_TEMPLATE_CLASSIFICATIONS:
                continue
            logical_key = str(
                character.get("logical_character_key")
                or f"character:{character['character_id']}"
            )
            selected.setdefault(logical_key, character)

        preferred_logical_keys: set[str] = set()
        for raw_character_id in preferred_character_ids:
            try:
                character_id = int(raw_character_id)
            except (TypeError, ValueError):
                continue
            preferred_character = by_id.get(character_id)
            if preferred_character is None:
                continue
            preferred_logical_key = self.get_logical_character_key(
                character_id
            )
            if (
                not preferred_logical_key
                or preferred_logical_key in preferred_logical_keys
            ):
                continue
            if (
                preferred_character.get("classification")
                == "available_avatar_variant"
            ):
                selected[preferred_logical_key] = preferred_character
                preferred_logical_keys.add(preferred_logical_key)

        for logical_key, default_character_id in _DEFAULT_LOGICAL_CHARACTER_IDS.items():
            if logical_key in selected:
                continue
            default_character = by_id.get(default_character_id)
            if (
                default_character is not None
                and self.get_logical_character_key(default_character_id) == logical_key
            ):
                selected[logical_key] = default_character

        return sorted(
            selected.values(),
            key=lambda character: int(character["character_id"]),
        )

    def get_character_recommended_weights(self, character_id: int) -> dict[str, Any] | None:
        """读取开发期写入静态库的推荐权重；运行时不会调用外部 API。"""

        recommendation = self._one(
            """SELECT character_id, source_kind, source_item_id, source_name,
                      source_updated_at_utc
               FROM character_weight_recommendation WHERE character_id = ?""",
            (int(character_id),),
        )
        if recommendation is None:
            return None
        properties = self._rows(
            """SELECT property_id, weight, main_weight, ordinal
               FROM character_weight_recommendation_property
               WHERE character_id = ? ORDER BY ordinal""",
            (int(character_id),),
        )
        recommendation["properties"] = properties
        recommendation["property_weights"] = {
            row["property_id"]: float(row["weight"])
            for row in properties if float(row["weight"]) > 0
        }
        recommendation["main_property_weights"] = {
            row["property_id"]: float(row["main_weight"])
            for row in properties if float(row["main_weight"]) > 0
        }
        return recommendation

    def list_character_recommended_weights(self) -> list[dict[str, Any]]:
        return [
            recommendation
            for row in self._rows(
                "SELECT character_id FROM character_weight_recommendation ORDER BY character_id"
            )
            if (recommendation := self.get_character_recommended_weights(int(row["character_id"])))
            is not None
        ]

    def get_character_graduation_template(
        self, character_id: int,
    ) -> dict[str, Any] | None:
        """读取构建期生成的固定毕业模板；运行时不再搜索词条组合。"""

        template = self._one(
            """
            SELECT character_id, source_kind, fork_id, fork_level,
                   fork_refinement_level, core_suit_id,
                   core_main_property_id, drive_area, extra_shape_count,
                   benchmark_damage, profile_json, equipment_json,
                   generated_at_utc
            FROM character_graduation_template
            WHERE character_id = ?
            """,
            (int(character_id),),
        )
        if template is None:
            return None
        template["profile"] = json.loads(template.pop("profile_json"))
        template["equipment"] = json.loads(template.pop("equipment_json"))
        return template

    def list_character_graduation_templates(self) -> list[dict[str, Any]]:
        return [
            template
            for row in self._rows(
                "SELECT character_id FROM character_graduation_template ORDER BY character_id"
            )
            if (
                template := self.get_character_graduation_template(
                    int(row["character_id"])
                )
            ) is not None
        ]

    def list_character_awaken_effects(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色六觉与三/六觉共鸣，含可直接应用的技能等级加成。"""

        effects = self._rows(
            """
            SELECT character_id, effect_id, ordinal, awaken_type, title_zh,
                   title_text_table, title_text_key, description_zh,
                   description_text_table, description_text_key, icon_path,
                   modify_data_json, gameplay_effect_ids_json, source_row_id
            FROM character_awaken_effect
            WHERE character_id = ?
            ORDER BY ordinal
            """,
            (character_id,),
        )
        bonuses_by_effect: dict[str, list[dict[str, Any]]] = {}
        for bonus in self._rows(
            """
            SELECT effect_id, ordinal, skill_id, level_delta
            FROM character_awaken_skill_level_bonus
            WHERE character_id = ?
            ORDER BY effect_id, ordinal
            """,
            (character_id,),
        ):
            effect_id = bonus.pop("effect_id")
            bonuses_by_effect.setdefault(effect_id, []).append(bonus)
        for effect in effects:
            effect["modify_data"] = json.loads(effect.pop("modify_data_json"))
            effect["gameplay_effect_ids"] = json.loads(effect.pop("gameplay_effect_ids_json"))
            effect["skill_level_bonuses"] = bonuses_by_effect.get(effect["effect_id"], [])
            effect["description_damage_entries"] = [
                damage
                for damage_id in effect["gameplay_effect_ids"]
                if (damage := self.get_skill_damage(str(damage_id))) is not None
            ]
        return effects

    def get_character_panel_growth(
        self, character_id: int, level: int, breakthrough_stage: int
    ) -> dict[str, Any] | None:
        """按角色、等级和已突破阶段返回官方基础生命、攻击和防御。"""

        return self._one(
            """
            SELECT character_id, level, breakthrough_stage, state,
                   hp_base, atk_base, def_base,
                   player_pack_source_row_id, level_modify_source_row_id,
                   breakthrough_modify_source_row_id
            FROM character_panel_growth
            WHERE character_id = ? AND level = ? AND breakthrough_stage = ?
            """,
            (character_id, level, breakthrough_stage),
        )

    def list_character_panel_growth(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色全部官方等级/突破面板，供角色页选择而非复制数值。"""

        return self._rows(
            """
            SELECT character_id, level, breakthrough_stage, state,
                   hp_base, atk_base, def_base,
                   player_pack_source_row_id, level_modify_source_row_id,
                   breakthrough_modify_source_row_id
            FROM character_panel_growth
            WHERE character_id = ?
            ORDER BY level, breakthrough_stage
            """,
            (character_id,),
        )

    def list_character_skills(self, character_id: int) -> list[dict[str, Any]]:
        """返回角色技能目录及每一级对应的突破、觉醒和材料要求。"""

        skills = self._rows(
            """
            SELECT character_id, skill_id, ability_type, ability_index,
                   show_detail_info, gameplay_tag, gameplay_effect_path,
                   reapply_after_revive, ability_source_row_id, effect_source_row_id
            FROM character_skill
            WHERE character_id = ?
            ORDER BY ability_index, skill_id
            """,
            (character_id,),
        )
        levels_by_skill: dict[str, list[dict[str, Any]]] = {}
        for level in self._rows(
            """
            SELECT skill_id, level, required_breakthrough_stage,
                   required_awaken_level, cost_items_json
            FROM character_skill_level
            WHERE character_id = ?
            ORDER BY skill_id, level
            """,
            (character_id,),
        ):
            skill_id = level.pop("skill_id")
            level["cost_items"] = json.loads(level.pop("cost_items_json"))
            levels_by_skill.setdefault(skill_id, []).append(level)
        for skill in skills:
            skill["show_detail_info"] = bool(skill["show_detail_info"])
            skill["reapply_after_revive"] = bool(skill["reapply_after_revive"])
            skill["levels"] = levels_by_skill.get(skill["skill_id"], [])
            skill["damage_entries"] = self._rows(
                """
                SELECT d.damage_id, d.damage_type, d.charge_add, d.unbal_value,
                       d.heterochrome_add, d.damage_source_category, d.fixed_crit_rate,
                       d.atk_rate_base_json, d.def_rate_base_json, d.hp_rate_base_json,
                       d.story_balance_ge_rate, d.attack_break_level,
                       d.override_breakable_damage, d.breakable_damage,
                       d.override_breakable_impulse, d.breakable_impulse,
                       d.override_vehicle_breakable_impulse,
                       d.vehicle_breakable_impulse, d.source_row_id,
                       m.atk_rate_base_coefficient AS modifier_atk_rate_base_coefficient,
                       m.source_row_id AS modifier_source_row_id
                FROM skill_damage AS d
                LEFT JOIN skill_damage_modifier AS m USING (damage_id)
                WHERE d.ability_id = ?
                ORDER BY d.damage_id
                """,
                (skill["skill_id"],),
            )
            for damage in skill["damage_entries"]:
                for key in ("atk_rate_base", "def_rate_base", "hp_rate_base"):
                    damage[key] = json.loads(damage.pop(f"{key}_json"))
                for key in (
                    "override_breakable_damage",
                    "override_breakable_impulse",
                    "override_vehicle_breakable_impulse",
                ):
                    damage[key] = bool(damage[key])
        return skills

    def list_shapes(self) -> list[dict[str, Any]]:
        shapes = self._rows(
            """
            SELECT shape_id, cell_count, first_grid_delta_x, first_grid_delta_y,
                   source_row_id
            FROM equipment_shape
            ORDER BY shape_id
            """
        )
        cells = self._rows(
            """
            SELECT shape_id, ordinal, x, y
            FROM equipment_shape_cell
            ORDER BY shape_id, ordinal
            """
        )
        cells_by_shape: dict[str, list[dict[str, Any]]] = {}
        for cell in cells:
            cells_by_shape.setdefault(cell.pop("shape_id"), []).append(cell)
        for shape in shapes:
            shape["cells"] = cells_by_shape.get(shape["shape_id"], [])
        return shapes

    def list_suits(self) -> list[dict[str, Any]]:
        suits = self._rows(
            """
            SELECT suit_id, name_zh, name_text_table, name_text_key, icon_path,
                   source_row_id
            FROM equipment_suit
            ORDER BY suit_id
            """
        )
        required_shapes = self._rows(
            """
            SELECT suit_id, ordinal, shape_id
            FROM equipment_suit_required_shape
            ORDER BY suit_id, ordinal
            """
        )
        effects = self._rows(
            """
            SELECT suit_id, required_count, modify_pack_id, buff_object_path,
                   description_zh, description_text_table, description_text_key,
                   reapply_after_revive, source_row_id
            FROM equipment_suit_effect
            ORDER BY suit_id, required_count
            """
        )
        shapes_by_suit: dict[str, list[str]] = {}
        for row in required_shapes:
            shapes_by_suit.setdefault(row["suit_id"], []).append(row["shape_id"])
        effects_by_suit: dict[str, list[dict[str, Any]]] = {}
        for effect in effects:
            effect["reapply_after_revive"] = bool(effect["reapply_after_revive"])
            effects_by_suit.setdefault(effect.pop("suit_id"), []).append(effect)
        for suit in suits:
            suit["required_shape_ids"] = shapes_by_suit.get(suit["suit_id"], [])
            suit["effects"] = effects_by_suit.get(suit["suit_id"], [])
        return suits

    def get_suit(self, suit_id: str) -> dict[str, Any] | None:
        return next((suit for suit in self.list_suits() if suit["suit_id"] == suit_id), None)

    def list_equipment_attributes(self) -> list[dict[str, Any]]:
        """返回可用于装备词条、核心筛选和官方蓝图的属性 ID。"""

        rows = self._rows(
            """
            SELECT attribute_id, display_name_zh, filter_name_zh,
                   random_attribute_name_zh, attribute_type, show_percent,
                   show_outside, show_inside, score, icon_path, source_row_id
            FROM equipment_attribute
            ORDER BY attribute_id
            """
        )
        for row in rows:
            for field in ("show_percent", "show_outside", "show_inside"):
                row[field] = bool(row[field])
        return rows

    def get_equipment_attribute(self, attribute_id: str) -> dict[str, Any] | None:
        """按官方属性 ID 查询装备属性定义。"""

        raw_attribute_id = str(attribute_id).strip()
        if not raw_attribute_id:
            raise ValueError("attribute_id 不能为空")
        return next(
            (
                attribute
                for attribute in self.list_equipment_attributes()
                if attribute["attribute_id"] == raw_attribute_id
            ),
            None,
        )

    def list_equipment_items(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind not in (None, "module", "core"):
            raise ValueError("equipment kind must be 'module', 'core', or None")
        where = "" if kind is None else "WHERE kind = ?"
        parameters = () if kind is None else (kind,)
        rows = self._rows(
            f"""
            SELECT item_id, kind, quality, name_zh, name_text_table, name_text_key,
                   geometry_id, geometry_enum, grid_count, suit_id, suit_type_enum,
                   max_level, random_base_attribute_pool_id,
                   random_base_attribute_count, random_sub_attribute_pool_id,
                   random_sub_attribute_count, random_sub_attribute_max_count,
                   strength_pack_id, icon_path, plan_icon_path, is_guide_item,
                   source_row_id
            FROM equipment_item
            {where}
            ORDER BY item_id
            """,
            parameters,
        )
        for row in rows:
            row["is_guide_item"] = bool(row["is_guide_item"])
        return rows

    def get_equipment_item(self, item_id: str) -> dict[str, Any] | None:
        """按游戏官方物品 ID 返回一条装备模板。"""

        raw_item_id = str(item_id).strip()
        if not raw_item_id:
            raise ValueError("item_id 不能为空")
        return next(
            (
                item
                for item in self.list_equipment_items()
                if item["item_id"] == raw_item_id
            ),
            None,
        )

    def evaluate_equipment_base_attribute_curve(
        self,
        curve_id: str,
        level: float,
    ) -> float | None:
        """按官方插值模式读取装备主属性在指定等级的数值。"""

        curve = self._one(
            """
            SELECT interpolation_mode, default_value
            FROM equipment_base_attribute_curve
            WHERE curve_id = ?
            """,
            (str(curve_id),),
        )
        if curve is None:
            return None
        points = self._rows(
            """
            SELECT level, value
            FROM equipment_base_attribute_point
            WHERE curve_id = ?
            ORDER BY level
            """,
            (str(curve_id),),
        )
        if not points:
            default_value = curve.get("default_value")
            return None if default_value is None else float(default_value)

        target = float(level)
        if target <= float(points[0]["level"]):
            return float(points[0]["value"])
        if target >= float(points[-1]["level"]):
            return float(points[-1]["value"])

        previous = points[0]
        for current in points[1:]:
            current_level = float(current["level"])
            if target > current_level:
                previous = current
                continue
            if str(curve.get("interpolation_mode") or "") == "RCIM_Constant":
                return float(previous["value"])
            previous_level = float(previous["level"])
            span = current_level - previous_level
            if span <= 0:
                return float(current["value"])
            ratio = (target - previous_level) / span
            return float(previous["value"]) + (
                float(current["value"]) - float(previous["value"])
            ) * ratio
        return float(points[-1]["value"])
