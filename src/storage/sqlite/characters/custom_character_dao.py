# 管理当前账号创建的、没有发行模板的可计算角色。
"""Account-private custom calculation character persistence."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.storage.sqlite.user_data_support import UserDataError, UserDataValidationError, _finite_number, _integer, _utc_now


CUSTOM_CHARACTER_ID_START = 1_500_000_000


class CustomCharacterDaoMixin:
    """维护自建角色、底盘和加成；每项写入参与调用方的同一连接事务。"""

    @staticmethod
    def validate_custom_character_shape_bonus(
        shape_label: str, property_values: dict[str, float],
    ) -> tuple[str, str | None, float]:
        """只校验不写库，供整批准备与单项 DAO 写入共用，防止规则漂移。"""
        label = str(shape_label or "").strip() or "Type-3"
        if len(label) > 40:
            raise UserDataValidationError("额外形状标签长度须为 1 至 40 个字符")
        values = [(str(key).strip(), _finite_number(value, "额外形状加成", minimum=0))
                  for key, value in property_values.items()]
        if len(values) > 1 or any(not key for key, _value in values):
            raise UserDataValidationError("自建角色额外形状仅支持一项有效属性加成")
        property_id, display_value = values[0] if values else (None, 0.0)
        return label, property_id, display_value

    @staticmethod
    def validate_custom_character_board_cells(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """底盘为 5×5 正式坐标且恰好启用 20 格；校验在任何 SQL 写入前完成。"""
        normalized = []
        for cell in cells:
            row = _integer(cell.get("row"), "底盘 row", minimum=1)
            column = _integer(cell.get("column"), "底盘 column", minimum=1)
            if row > 5 or column > 5:
                raise UserDataValidationError("底盘坐标须位于 5×5 格位内")
            normalized.append({"row": row, "column": column,
                               "is_enabled": bool(cell.get("is_enabled")),
                               "is_locked": bool(cell.get("is_locked"))})
        coordinates = {(cell["row"], cell["column"]) for cell in normalized}
        if len(normalized) != 25 or len(coordinates) != 25 or sum(cell["is_enabled"] for cell in normalized) != 20:
            raise UserDataValidationError("自定义底盘必须包含 25 个格位，其中恰好启用 20 格")
        return normalized

    def list_custom_characters(self) -> list[dict[str, Any]]:
        roles = self._rows(
            """SELECT character_id, name_zh, game_name, created_at_utc, updated_at_utc
               FROM custom_character ORDER BY created_at_utc, character_id"""
        )
        for role in roles:
            role["board_cells"] = self.list_custom_character_board_cells(role["character_id"])
            role["shape_bonus"] = self.get_custom_character_shape_bonus(role["character_id"])
            role["target_suit_id"] = self.get_custom_character_target_suit_id(
                role["character_id"]
            )
        return roles

    def get_custom_character_target_suit_id(self, character_id: int) -> str | None:
        row = self._one(
            """SELECT target_suit_id
               FROM custom_character_calculation_setting
               WHERE character_id = ?""",
            (int(character_id),),
        )
        return str(row["target_suit_id"]) if row and row.get("target_suit_id") else None

    def save_custom_character_target_suit_id(
        self, character_id: int, target_suit_id: str | None,
    ) -> None:
        suit_id = str(target_suit_id or "").strip() or None
        try:
            with self.write_transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM custom_character WHERE character_id = ?",
                    (int(character_id),),
                ).fetchone() is None:
                    raise UserDataValidationError("自建角色不存在")
                now = _utc_now()
                connection.execute(
                    """INSERT INTO custom_character_calculation_setting(
                           character_id, target_suit_id, updated_at_utc
                       ) VALUES (?, ?, ?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           target_suit_id = excluded.target_suit_id,
                           updated_at_utc = excluded.updated_at_utc""",
                    (int(character_id), suit_id, now),
                )
                connection.execute(
                    "UPDATE custom_character SET updated_at_utc = ? WHERE character_id = ?",
                    (now, int(character_id)),
                )
        except sqlite3.Error as exc:
            raise UserDataError("保存自建角色默认套装失败") from exc

    def list_custom_character_board_cells(self, character_id: int) -> list[dict[str, Any]]:
        return self._rows(
            """SELECT row_number, column_number, is_enabled, is_locked
               FROM custom_character_board_cell WHERE character_id = ?
               ORDER BY row_number, column_number""",
            (int(character_id),),
        )

    def get_custom_character_shape_bonus(self, character_id: int) -> dict[str, Any]:
        row = self._one(
            """SELECT shape_label, property_id, display_value
               FROM custom_character_shape_bonus WHERE character_id = ?""",
            (int(character_id),),
        )
        if row is None:
            return {"shape_label": "Type-3", "properties": []}
        properties = (
            [{"property_id": str(row["property_id"]), "display_value": float(row["display_value"])}]
            if row.get("property_id")
            else []
        )
        return {"shape_label": str(row["shape_label"]), "properties": properties}

    def save_custom_character_shape_bonus(
        self,
        character_id: int,
        *,
        shape_label: str,
        property_values: dict[str, float],
    ) -> None:
        label, property_id, display_value = self.validate_custom_character_shape_bonus(shape_label, property_values)
        try:
            with self.write_transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM custom_character WHERE character_id = ?", (int(character_id),)
                ).fetchone() is None:
                    raise UserDataValidationError("自建角色不存在")
                now = _utc_now()
                connection.execute(
                    """INSERT INTO custom_character_shape_bonus(
                           character_id, shape_label, property_id, display_value, updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(character_id) DO UPDATE SET
                           shape_label = excluded.shape_label,
                           property_id = excluded.property_id,
                           display_value = excluded.display_value,
                           updated_at_utc = excluded.updated_at_utc""",
                    (int(character_id), label, property_id, display_value, now),
                )
                connection.execute(
                    "UPDATE custom_character SET updated_at_utc = ? WHERE character_id = ?",
                    (now, int(character_id)),
                )
        except sqlite3.Error as exc:
            raise UserDataError("保存自建角色额外形状失败") from exc

    def save_custom_character_board_cells(self, character_id: int, cells: list[dict[str, Any]]) -> None:
        cells = self.validate_custom_character_board_cells(cells)
        try:
            with self.write_transaction() as connection:
                connection.execute("DELETE FROM custom_character_board_cell WHERE character_id = ?", (int(character_id),))
                connection.executemany(
                    """INSERT INTO custom_character_board_cell(
                           character_id, row_number, column_number, is_enabled, is_locked
                       ) VALUES (?, ?, ?, ?, ?)""",
                    [(int(character_id), int(row["row"]), int(row["column"]), int(bool(row.get("is_enabled"))), int(bool(row.get("is_locked")))) for row in cells],
                )
                connection.execute(
                    "UPDATE custom_character SET updated_at_utc = ? WHERE character_id = ?",
                    (_utc_now(), int(character_id)),
                )
        except sqlite3.Error as exc:
            raise UserDataError("无法保存自建角色底盘") from exc

    def create_custom_character(
        self, name_zh: str, *, game_name: str | None = None,
    ) -> dict[str, Any]:
        name = str(name_zh or "").strip()
        alias = str(game_name or name).strip()
        if not name or len(name) > 40 or not alias or len(alias) > 40:
            raise UserDataValidationError("角色名称和游戏内名称须为 1 至 40 个字符")
        try:
            with self.write_transaction() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM custom_character WHERE name_zh = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if existing is not None:
                    raise UserDataValidationError(f"自建角色名称已存在：{name}")
                row = connection.execute(
                    "SELECT MAX(character_id) AS maximum FROM custom_character"
                ).fetchone()
                character_id = max(CUSTOM_CHARACTER_ID_START - 1, int(row["maximum"] or 0)) + 1
                now = _utc_now()
                connection.execute(
                    """INSERT INTO custom_character(
                           character_id, name_zh, game_name, created_at_utc, updated_at_utc
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (character_id, name, alias, now, now),
                )
                connection.executemany(
                    """INSERT INTO custom_character_board_cell(
                           character_id, row_number, column_number, is_enabled, is_locked
                       ) VALUES (?, ?, ?, ?, 0)""",
                    [(character_id, row, column, int(row <= 4)) for row in range(1, 6) for column in range(1, 6)],
                )
        except sqlite3.Error as exc:
            raise UserDataError("无法创建自建角色") from exc
        return self._one(
            """SELECT character_id, name_zh, game_name, created_at_utc, updated_at_utc
               FROM custom_character WHERE character_id = ?""",
            (character_id,),
        ) or {}

    def delete_custom_character(self, character_id: int) -> None:
        """原子删除自建角色与账号配置；有外部任务历史时保留原事实。"""

        raw_character_id = int(character_id)
        try:
            with self.write_transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM custom_character WHERE character_id = ?",
                    (raw_character_id,),
                ).fetchone() is None:
                    raise UserDataValidationError("自建角色不存在")
                job_reference = connection.execute(
                    """SELECT 1 FROM equipment_apply_job_item AS item
                       JOIN loadout_plan AS plan ON plan.plan_id = item.plan_id
                       WHERE plan.character_id = ? LIMIT 1""",
                    (raw_character_id,),
                ).fetchone()
                if job_reference is not None:
                    raise UserDataValidationError("该角色已有极速装配任务记录，先保留其方案与任务历史")
                connection.execute(
                    "DELETE FROM optimization_preference_character WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM loadout_plan WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM role_loadout_slot WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM character_weight_preference_seed WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM character_profile_observation WHERE character_id = ?", (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM character_profile WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM character_instance_mapping WHERE character_id = ?",
                    (raw_character_id,),
                )
                connection.execute(
                    "DELETE FROM custom_character WHERE character_id = ?",
                    (raw_character_id,),
                )
        except sqlite3.Error as exc:
            raise UserDataError("删除自建角色失败") from exc
