# 保存账号的战报增强搜索档位，并将显式选择转换为固定搜索预算。
from src.storage.sqlite.user_data_dao import UserDataDao

SEARCH_MODES = (("fast", "快搜（约 2 分钟）"), ("deep", "深搜（约 10 分钟）"))
_KEY = "battle_axis_search"


def search_budget(mode):
    if mode not in {key for key, _ in SEARCH_MODES}:
        raise ValueError("未知战报搜索档位，请重新选择")
    return {"max_millis": 600_000 if mode == "deep" else 120_000,
            "max_evaluations": 100_000 if mode == "deep" else 1_024}


def load_search_mode(database_path):
    with UserDataDao(database_path) as dao:
        raw = dao.list_application_setting_copies().get(_KEY) or {}
    mode = raw.get("mode", "fast") if isinstance(raw, dict) else "fast"
    return mode if mode in {key for key, _ in SEARCH_MODES} else "fast"


def save_search_mode(database_path, mode):
    search_budget(mode)
    with UserDataDao(database_path) as dao:
        dao.replace_application_setting_copy(_KEY, {"mode": mode})
