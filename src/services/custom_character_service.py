# 编排账号私有自建角色与基础权重的初始记录。
"""Create account-private roles configured from the basic-weight page.

Custom roles, their chassis matrices, and their extra-shape values stay in the
current account database. They never participate in the release-level public
extra-shape override installed under ``data/app_shared.sqlite3``.
"""

from __future__ import annotations

from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


_INITIAL_WEIGHTS = (
    ("CritBase", 1.0, 0.0),
    ("CritDamageBase", 1.0, 0.0),
    ("DamageUpGeneralBase", 1.0, 0.0),
    ("AtkUp", 0.8, 0.0),
    ("AtkAdd", 0.25, 0.0),
)


def create_custom_character(user_database_path: str | Path, name_zh: str) -> dict:
    """Create a role and its account-owned editable weight seed together."""

    with UserDataDao(user_database_path) as user_dao:
        role = user_dao.create_custom_character(name_zh)
        user_dao.seed_character_weight_preferences(
            int(role["character_id"]),
            properties=[
                {"property_id": property_id, "weight": weight, "main_weight": main_weight}
                for property_id, weight, main_weight in _INITIAL_WEIGHTS
            ],
            source_dataset_id="account-custom-v1",
            source_kind="custom",
        )
        return role


def save_custom_character_board(
    user_database_path: str | Path, character_id: int, cells: list[dict],
) -> None:
    with UserDataDao(user_database_path) as user_dao:
        user_dao.save_custom_character_board_cells(character_id, cells)


def save_custom_character_target_suit(
    user_database_path: str | Path, character_id: int, target_suit_id: str | None,
) -> None:
    with UserDataDao(user_database_path) as user_dao:
        user_dao.save_custom_character_target_suit_id(character_id, target_suit_id)


def save_custom_character_shape_bonus(
    user_database_path: str | Path,
    character_id: int,
    *,
    shape_label: str,
    property_values: dict[str, float],
) -> None:
    with UserDataDao(user_database_path) as user_dao:
        user_dao.save_custom_character_shape_bonus(
            character_id,
            shape_label=shape_label,
            property_values=property_values,
        )


def delete_custom_character(user_database_path: str | Path, character_id: int) -> None:
    with UserDataDao(user_database_path) as user_dao:
        user_dao.delete_custom_character(character_id)
