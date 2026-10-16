# 账号内的追加及替换匹配套装设置，只保存正式身份，不改角色或计算配置。
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import CancelledError
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError


SETTING_KEY = "identification_role_suits"


def normalize_matching_settings(raw: dict | None) -> dict:
    if raw is None:
        return {"version": 1, "extra_suits": {}}
    if (not isinstance(raw, dict) or type(raw.get("version")) is not int
            or raw["version"] not in (1, 2)):
        raise ValueError("角色匹配套装配置格式或版本不受支持")
    fields = {"extra_suits"} if raw["version"] == 1 else {"extra_suits", "suit_overrides"}
    if set(raw) != {"version", *fields} or any(not isinstance(raw[field], dict) for field in fields):
        raise ValueError("角色匹配套装配置字段无效")
    normalized = {"version": raw["version"]}
    for field in sorted(fields):
        normalized[field] = _normalize_suits(raw[field], allow_empty=field == "extra_suits")
    if set(normalized["extra_suits"]) & set(normalized.get("suit_overrides", {})):
        raise ValueError("同一角色不可同时使用追加与替换套装配置")
    return normalized


def _normalize_suits(mapping, *, allow_empty):
    result = {}
    for key, values in mapping.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdigit() or int(key) <= 0:
            raise ValueError("角色匹配套装缺少有效角色身份")
        canonical = str(int(key))
        if canonical != key or not isinstance(values, list):
            raise ValueError("角色匹配套装身份或套装列表无效")
        if any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in values):
            raise ValueError("角色匹配套装缺少有效套装身份")
        if values:
            result[canonical] = sorted(set(values))
        elif not allow_empty:
            raise ValueError("自定义匹配至少保留一个套装")
    return dict(sorted(result.items()))


def effective_matching_suits(settings, character_id, default_id):
    """旧配置保留默认加额外；显式替换只使用选定集合，不回退补回官方套装。"""
    key = str(character_id)
    override = settings.get("suit_overrides", {}).get(key)
    if override is not None:
        return tuple(override)
    return tuple(dict.fromkeys([default_id, *settings["extra_suits"].get(key, ())]))


def set_matching_suits(settings, character_id, default_id, values):
    """仅修改界面草稿；保留其他角色和未知条目，实际提交仍由账号服务完成。"""
    key, values = str(character_id), sorted(set(values))
    if not values:
        raise ValueError("自定义匹配至少保留一个套装")
    reset_matching_role(settings, character_id)
    if default_id in values:
        extras = [value for value in values if value != default_id]
        if extras:
            settings["extra_suits"][key] = extras
    else:
        settings["version"] = 2
        settings.setdefault("suit_overrides", {})[key] = values


def reset_matching_role(settings, character_id):
    key = str(character_id)
    settings["extra_suits"].pop(key, None)
    settings.get("suit_overrides", {}).pop(key, None)


def matching_settings_identity(value: dict) -> str:
    canonical = normalize_matching_settings(value)
    # 版本升级但没有替换项时，实际匹配集合未变，不使旧结果无谓失效。
    if canonical["version"] == 2 and not canonical["suit_overrides"]:
        canonical = {"version": 1, "extra_suits": canonical["extra_suits"]}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class MatchingSettingsService:
    def __init__(self, user_database_path: str | Path):
        self.database_path = Path(user_database_path)

    def load(self) -> dict:
        with UserDataDao(self.database_path) as dao:
            return normalize_matching_settings(dao.list_application_setting_copies().get(SETTING_KEY))

    def save(self, draft: dict, *, expected: dict, check_current=None, post_action_config=None) -> bool:
        """UI 草稿按打开时的账号与配置提交；冲突不覆盖，等价内容不推进修订。"""
        value = normalize_matching_settings(draft)
        expected = normalize_matching_settings(expected)
        if matching_settings_identity(value) == matching_settings_identity(expected):
            value = expected
        if post_action_config is not None:
            from src.domain.post_actions import merge_post_action_config, validate_post_action_config
            post_action_config = merge_post_action_config(post_action_config)
            error = validate_post_action_config(post_action_config)
            if error:
                raise ValueError(error)
        # 管理窗口的一次保存同时提交两份草稿；冲突、取消、写入失败整体回滚。
        with UserDataDao(self.database_path) as dao, dao.write_transaction():
            changed = dao.compare_and_replace_application_setting_copy(
                SETTING_KEY, value, expected=expected,
                missing_value=normalize_matching_settings(None), check=check_current)
            if post_action_config is not None:
                dao.patch_application_setting_copy("ui", {"full_scan_post_action_config": post_action_config})
            if check_current is not None:
                check_current()
            return changed

    def require_identity(self, expected: str) -> None:
        """在派发前复核冻结配置；迟到结果不沿用已失效的匹配范围。"""
        try:
            current = matching_settings_identity(self.load())
        except (ValueError, OSError, sqlite3.Error, UserDataError) as exc:
            # 复核缺证同样只撤销后续动作；已接受部分仍由原执行 owner 核对。
            raise CancelledError("角色匹配套装配置读取失败，本批后续处理已停止") from exc
        if current != expected:
            raise CancelledError("角色匹配套装已变化，请重新评估")
