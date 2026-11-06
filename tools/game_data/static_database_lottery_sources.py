# 规范化卡池角色身份字段的已证实改名，不容忍冲突、缺失或空列表。
from tools.game_data.static_database_build_support import StaticDatabaseError


def lottery_character_ids(properties: dict, source_name: str) -> list[int]:
    """兼容已观测到的 UE 字段改名；身份列表缺失不等于卡池没有角色。"""
    old, new = properties.get("CharacterIDs"), properties.get("CharacterIds")
    if old is not None and new is not None and old != new:
        raise StaticDatabaseError(f"角色卡池身份字段冲突：{source_name}")
    values = old if old is not None else new
    if not isinstance(values, list) or not values:
        raise StaticDatabaseError(f"角色卡池缺少正式角色列表：{source_name}")
    if any(isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0 for value in values):
        raise StaticDatabaseError(f"角色卡池含无效角色身份：{source_name}")
    return [int(value) for value in values]
