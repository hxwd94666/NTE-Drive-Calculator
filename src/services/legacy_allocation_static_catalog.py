# 将旧求解器所需的兼容结构完全投影自官方 SQLite。
"""Static SQLite adapter for the legacy allocation solver.

The solver still uses display names and puzzle matrices internally, but these
structures are derived only from official static data and account-scoped SQLite
 weight preferences.  No legacy JSON configuration is read here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.models.equipment import DriveShape
from src.optimizer.scoring import ScoringEngine
from src.services.sqlite_allocation_inventory import legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


_LEGACY_SHAPE_LABELS = {
    "H_2": "Type-2", "V_2": "Type-2",
    "H_3": "Type-3", "V_3": "Type-3",
    "L_3_BL": "Type-3", "L_3_TL": "Type-3", "L_3_TR": "Type-3", "L_3_BR": "Type-3",
    "H_4": "Type-4", "V_4": "Type-4", "Trap_4_H": "Type-4", "Trap_4_V": "Type-4",
}


@dataclass(frozen=True)
class LegacyAllocationStaticCatalog:
    roles_db: dict[str, dict[str, Any]]
    sets_db: dict[str, dict[str, Any]]
    shapes_db: dict[str, DriveShape]
    board_matrices: dict[str, list[list[int]]]


def _shape_matrix(shape: dict[str, Any]) -> list[list[int]]:
    cells = list(shape.get("cells") or [])
    xs = [int(cell["x"]) for cell in cells]
    ys = [int(cell["y"]) for cell in cells]
    if not xs or not ys:
        raise ValueError(f"官方形状 {shape.get('shape_id')} 没有格位定义")
    matrix = [[0] * (max(ys) - min(ys) + 1) for _ in range(max(xs) - min(xs) + 1)]
    for cell in cells:
        matrix[int(cell["x"]) - min(xs)][int(cell["y"]) - min(ys)] = 1
    return matrix


def build_legacy_allocation_static_catalog(
    *, config_dir: str | Path, user_database_path: str | Path | None = None,
) -> LegacyAllocationStaticCatalog:
    """Build all old-solver inputs from the static and account SQLite databases."""

    scoring = ScoringEngine(
        config_dir=str(config_dir), user_database_path=user_database_path,
    )
    roles_db: dict[str, dict[str, Any]] = {}
    sets_db: dict[str, dict[str, Any]] = {}
    shapes_db: dict[str, DriveShape] = {}
    board_matrices: dict[str, list[list[int]]] = {}
    database_path = Path(user_database_path) if user_database_path is not None else None

    with StaticGameDataDao() as static_dao:
        characters = static_dao.list_role_template_characters()
        shape_bonus_overrides: dict[int, dict[str, Any]] = {}
        if database_path is not None and database_path.is_file():
            with UserDataDao(database_path) as user_dao:
                shape_bonus_overrides = {
                    character_id: override
                    for character in characters
                    if (override := user_dao.get_character_shape_bonus_preferences(
                        character_id := int(character["character_id"])
                    )) is not None
                }
        for suit in static_dao.list_suits():
            name = str(suit.get("name_zh") or suit["suit_id"])
            sets_db[name] = {
                "suit_id": str(suit["suit_id"]),
                "shapes": [legacy_shape_id(shape_id) for shape_id in suit.get("required_shape_ids") or ()],
            }
        for shape in static_dao.list_shapes():
            legacy_id = legacy_shape_id(shape["shape_id"])
            shapes_db[legacy_id] = DriveShape(
                shape_id=legacy_id,
                label=_LEGACY_SHAPE_LABELS.get(legacy_id, f"Type-{int(shape['cell_count'])}"),
                matrix=_shape_matrix(shape),
                area=int(shape["cell_count"]),
                description=str(shape["shape_id"]),
            )
        attributes = {
            str(attribute["attribute_id"]): ScoringEngine._scoring_property_name(attribute)
            for attribute in static_dao.list_equipment_attributes()
        }
        for character in characters:
            character_id = int(character["character_id"])
            role_name = str(character.get("name_zh") or character_id)
            plan = static_dao.get_equipment_plan(character_id)
            default_suit = static_dao.get_character_default_suit(character_id)
            if plan is None or default_suit is None:
                continue
            suit_name = str(default_suit["suit_name_zh"])
            if suit_name not in sets_db:
                raise ValueError(f"角色 [{role_name}] 的官方默认套装不存在：{suit_name}")
            shape_bonus = static_dao.get_character_shape_bonus(character_id) or {}
            shape_override = shape_bonus_overrides.get(character_id)
            extra_shape_label = (
                str(shape_override.get("shape_label") or "")
                if shape_override is not None
                else str(shape_bonus.get("shape_label") or "")
            )
            extra_shape_buffs = (
                {
                    attributes[property_id]: float(value)
                    for property_id, value in (
                        shape_override.get("property_values") or {}
                    ).items()
                    if attributes.get(str(property_id))
                }
                if shape_override is not None
                else {
                    attributes[str(row["property_id"])]: float(row["display_value"])
                    for row in shape_bonus.get("properties") or ()
                    if attributes.get(str(row["property_id"]))
                }
            )
            scoring_role = scoring.roles_db.get(role_name, {})
            roles_db[role_name] = {
                "character_id": character_id,
                "default_set": suit_name,
                "extra_shape_label": extra_shape_label,
                "extra_shape_buffs": extra_shape_buffs,
                "weights": dict(scoring_role.get("weights") or {}),
                "main_weights": dict(scoring_role.get("main_weights") or {}),
            }
            board = [[-1] * 5 for _ in range(5)]
            for cell in plan.get("cells") or ():
                board[int(cell["row"]) - 1][int(cell["column"]) - 1] = 0
            board_matrices[role_name] = board
    return LegacyAllocationStaticCatalog(roles_db, sets_db, shapes_db, board_matrices)
