# 按正式逻辑角色身份投影旧变体配置；未知身份保留，不猜名称或静默删除。
from __future__ import annotations

from src.services.identification.matching_settings import normalize_matching_settings
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def project_matching_settings(roles_db, settings, static_database_path=None):
    settings = normalize_matching_settings(settings)
    known = {str(row["character_id"]) for row in roles_db.values()
             if type(row.get("character_id")) is int and row["character_id"] > 0}
    fields = ("extra_suits", "suit_overrides") if settings["version"] == 2 else ("extra_suits",)
    missing = set().union(*(set(settings[field]) for field in fields)) - known
    aliases = {}
    if missing and static_database_path is not None:
        with StaticGameDataDao(static_database_path) as dao:
            candidates = {}
            for key in known:
                logical = dao.get_logical_character_key(int(key))
                if logical:
                    candidates.setdefault(logical, set()).add(key)
            for key in missing:
                targets = candidates.get(dao.get_logical_character_key(int(key)), set())
                if len(targets) == 1:
                    aliases[key] = next(iter(targets))
    projected = {"version": settings["version"]}
    for field in fields:
        projected[field] = {}
        for key, suits in settings[field].items():
            projected[field].setdefault(aliases.get(key, key), []).extend(suits)
    # 唯一别名合并保留所有显式选择；存在替换配置时不重新添加官方默认。
    for key, suits in projected.get("suit_overrides", {}).items():
        suits.extend(projected["extra_suits"].pop(key, []))
    draft = normalize_matching_settings(projected)
    # 返回读取投影而不写库；编辑器只有显式保存才将变体合并写入当前正式 ID。
    return draft, tuple(sorted(int(key) for key in missing - set(aliases)))
