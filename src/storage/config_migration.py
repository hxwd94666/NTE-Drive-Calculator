# 迁移基础 JSON 配置，补齐新版缺失字段但保留用户已有值。
"""Core configuration migration helpers."""

from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.storage.json_store import read_json, write_json_atomic


def _list_item_key(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("shape_id", "set_name", "role_name", "id", "name"):
        value = item.get(key)
        if value:
            return f"{key}:{value}"
    return None


def _merge_lists(current: list, bundled: list) -> tuple[list, bool]:
    result = deepcopy(current)
    changed = False
    keyed_indexes = {}
    for index, item in enumerate(result):
        key = _list_item_key(item)
        if key:
            keyed_indexes[key] = index

    for bundled_item in bundled:
        key = _list_item_key(bundled_item)
        if key and key in keyed_indexes:
            merged, item_changed = merge_missing_config_data(result[keyed_indexes[key]], bundled_item)
            if item_changed:
                result[keyed_indexes[key]] = merged
                changed = True
            continue
        if bundled_item not in result:
            result.append(deepcopy(bundled_item))
            changed = True
    return result, changed


def merge_missing_config_data(current: Any, bundled: Any) -> tuple[Any, bool]:
    """Return current data plus missing structure from bundled data.

    Existing values always win. Dict keys are merged recursively. Lists append
    missing scalar items, and keyed dict-list items are matched by stable ids.
    """

    if isinstance(current, dict) and isinstance(bundled, dict):
        result = deepcopy(current)
        changed = False
        for key, bundled_value in bundled.items():
            if key not in result:
                result[key] = deepcopy(bundled_value)
                changed = True
                continue
            merged, child_changed = merge_missing_config_data(result[key], bundled_value)
            if child_changed:
                result[key] = merged
                changed = True
        return result, changed

    if isinstance(current, list) and isinstance(bundled, list):
        return _merge_lists(current, bundled)

    return deepcopy(current), False


def _backup_path(path: Path) -> Path:
    candidate = path.with_name(f"{path.name}.bak")
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = path.with_name(f"{path.name}.bak.{index}")
        if not candidate.exists():
            return candidate
        index += 1


def migrate_config_file(user_path: str | Path, bundled_path: str | Path) -> bool:
    user_path = Path(user_path)
    bundled_path = Path(bundled_path)
    if not bundled_path.exists():
        return False
    if bundled_path.resolve() == user_path.resolve():
        return False
    if not user_path.exists():
        user_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(bundled_path), str(user_path))
        return True

    try:
        current = read_json(user_path, default=None)
        bundled = read_json(bundled_path, default=None)
    except Exception:
        user_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(user_path), str(_backup_path(user_path)))
        shutil.copy2(str(bundled_path), str(user_path))
        return True

    merged, changed = merge_missing_config_data(current, bundled)
    if not changed:
        return False
    shutil.copy2(str(user_path), str(_backup_path(user_path)))
    write_json_atomic(user_path, merged, indent=2)
    return True


def migrate_core_config_dir(
    user_config_dir: str | Path,
    bundled_config_dir: str | Path,
    core_config_files: tuple[str, ...],
) -> int:
    user_config_dir = Path(user_config_dir)
    bundled_config_dir = Path(bundled_config_dir)
    migrated = 0
    for filename in core_config_files:
        if migrate_config_file(user_config_dir / filename, bundled_config_dir / filename):
            migrated += 1
    return migrated
