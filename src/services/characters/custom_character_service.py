# 编排账号私有自建角色与基础权重的初始记录。
"""角色、底盘和额外形状均归当前账号；创建统一事务，编辑由完整权重服务编排。"""

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
    """同一事务创建角色、默认底盘和可编辑权重；任一步失败均保留原账号事实。"""

    with UserDataDao(user_database_path) as user_dao, user_dao.write_transaction():
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


def delete_custom_character(user_database_path: str | Path, character_id: int) -> None:
    with UserDataDao(user_database_path) as user_dao:
        user_dao.delete_custom_character(character_id)
