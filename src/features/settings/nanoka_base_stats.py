# 从 nanoka.cc 静态 JSON 同步角色各等级基础白值。
"""Fetch character base white stats from nanoka static data and merge into my_roles_model.json.

This is not HTML scraping: nanoka.cc serves game data as versioned JSON under
https://static.nanoka.cc/nte/{version}/...
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.app.constants import APP_VERSION
from src.storage.json_store import read_json, write_json_atomic


NANOKA_STATIC_BASE = "https://static.nanoka.cc/nte"
NANOKA_DEFAULT_VERSION = "1.2"
NANOKA_DEFAULT_LOCALE = "zh"
NANOKA_API_TIMEOUT_SECONDS = 30

DEFAULT_LEVELS = (1, 20, 30, 40, 50, 60, 70, 80)

BASE_STAT_KEYS = ("生命白值", "攻击力白值", "防御力白值", "暴击率%", "暴击伤害%")

STAT_ID_TO_KEY = {
    "HPMaxBase": "生命白值",
    "AtkBase": "攻击力白值",
    "DefBase": "防御力白值",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
}


def _request_json(url: str, *, timeout: int = NANOKA_API_TIMEOUT_SECONDS) -> Any:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"nanoka 静态数据请求失败，HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"nanoka 静态数据请求异常: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"nanoka 静态数据不是有效 JSON: {url}") from exc


def fetch_character_index(
    *,
    version: str = NANOKA_DEFAULT_VERSION,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    payload = _request_json(f"{base_url.rstrip('/')}/{version}/character.json", timeout=timeout)
    if not isinstance(payload, dict):
        raise RuntimeError("nanoka character.json 格式异常。")
    return {
        str(character_id): item
        for character_id, item in payload.items()
        if isinstance(item, dict)
    }


def fetch_character_detail(
    character_id: str,
    *,
    version: str = NANOKA_DEFAULT_VERSION,
    locale: str = NANOKA_DEFAULT_LOCALE,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/{version}/{locale}/character/{character_id}.json"
    payload = _request_json(url, timeout=timeout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"nanoka 角色详情格式异常: {character_id}")
    return payload


def extract_level_base_stats(
    character: dict[str, Any],
    *,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
) -> dict[str, dict[str, float]]:
    """Build level_sub_stats from nanoka character.stats arrays (1-indexed levels)."""
    by_id: dict[str, list[Any]] = {}
    for item in character.get("stats") or []:
        if not isinstance(item, dict):
            continue
        stat_id = str(item.get("id_stats") or "")
        values = item.get("values")
        if stat_id in STAT_ID_TO_KEY and isinstance(values, list):
            by_id[stat_id] = values

    missing = [stat_id for stat_id in STAT_ID_TO_KEY if stat_id not in by_id]
    if missing:
        raise RuntimeError(
            f"角色 {character.get('id') or character.get('name') or '?'} 缺少属性: {', '.join(missing)}"
        )

    level_sub_stats: dict[str, dict[str, float]] = {}
    for level in levels:
        index = level - 1
        row: dict[str, float] = {}
        for stat_id, key in STAT_ID_TO_KEY.items():
            values = by_id[stat_id]
            if index < 0 or index >= len(values):
                raise RuntimeError(
                    f"角色 {character.get('id')} 的 {stat_id} 缺少等级 {level} 数据。"
                )
            row[key] = float(values[index])
        level_sub_stats[str(level)] = row
    return level_sub_stats


def _workshop_ids_for_role(role_meta: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("workshop_item_id",):
        value = role_meta.get(key)
        if value is not None and str(value).strip():
            ids.append(str(value).strip())
    raw_ids = role_meta.get("workshop_item_ids")
    if isinstance(raw_ids, list):
        for value in raw_ids:
            text = str(value or "").strip()
            if text and text not in ids:
                ids.append(text)
    return ids


def resolve_character_id(
    role_name: str,
    *,
    roles_meta: dict[str, Any],
    character_index: dict[str, dict[str, Any]],
) -> str | None:
    role_meta = roles_meta.get(role_name)
    if isinstance(role_meta, dict):
        for character_id in _workshop_ids_for_role(role_meta):
            if character_id in character_index:
                return character_id

    for character_id, item in character_index.items():
        zh_name = str(item.get("zh") or "").strip()
        if zh_name == role_name:
            return character_id
    return None


def _stats_equal(left: dict[str, Any] | None, right: dict[str, float]) -> bool:
    left = left if isinstance(left, dict) else {}
    for key in BASE_STAT_KEYS:
        try:
            local_value = float(left.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            local_value = 0.0
        if abs(local_value - float(right[key])) > 0.01:
            return False
    return True


def _apply_base_stats(target: dict[str, Any], incoming: dict[str, float]) -> dict[str, Any]:
    merged = dict(target) if isinstance(target, dict) else {}
    for key, value in incoming.items():
        merged[key] = float(value)
    return merged


def merge_nanoka_base_stats_into_model(
    model: dict[str, Any],
    remote_by_role: dict[str, dict[str, dict[str, float]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = json.loads(json.dumps(model, ensure_ascii=False))
    updated: list[str] = []
    unchanged: list[str] = []
    role_diffs: dict[str, list[dict[str, Any]]] = {}

    for role_name, remote_levels in remote_by_role.items():
        role_data = merged.get(role_name)
        if not isinstance(role_data, dict):
            continue

        local_levels = role_data.get("level_sub_stats")
        if not isinstance(local_levels, dict):
            local_levels = {}
            role_data["level_sub_stats"] = local_levels

        changed = False
        diffs: list[dict[str, Any]] = []
        for level_key, remote_stats in remote_levels.items():
            local_stats = local_levels.get(level_key)
            if _stats_equal(local_stats if isinstance(local_stats, dict) else None, remote_stats):
                continue
            if isinstance(local_stats, dict):
                for key in BASE_STAT_KEYS:
                    try:
                        old_value = float(local_stats.get(key, 0.0) or 0.0)
                    except (TypeError, ValueError):
                        old_value = 0.0
                    new_value = float(remote_stats[key])
                    if abs(old_value - new_value) > 0.01:
                        diffs.append(
                            {
                                "level": level_key,
                                "stat": key,
                                "local": old_value,
                                "remote": new_value,
                            }
                        )
            else:
                for key in BASE_STAT_KEYS:
                    diffs.append(
                        {
                            "level": level_key,
                            "stat": key,
                            "local": None,
                            "remote": float(remote_stats[key]),
                        }
                    )
            local_levels[level_key] = _apply_base_stats(
                local_stats if isinstance(local_stats, dict) else {},
                remote_stats,
            )
            changed = True

        current_level = str(role_data.get("level", 80))
        if current_level in local_levels:
            current_stats = local_levels[current_level]
            sub_stats = role_data.get("sub_stats")
            if not isinstance(sub_stats, dict):
                sub_stats = {}
                role_data["sub_stats"] = sub_stats
            before = {key: sub_stats.get(key) for key in BASE_STAT_KEYS}
            role_data["sub_stats"] = _apply_base_stats(sub_stats, current_stats)
            after = {key: role_data["sub_stats"].get(key) for key in BASE_STAT_KEYS}
            if before != after:
                changed = True

        if changed:
            updated.append(role_name)
            if diffs:
                role_diffs[role_name] = diffs
        else:
            unchanged.append(role_name)

    return merged, {
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "updated_roles": updated,
        "unchanged_roles": unchanged,
        "diffs": role_diffs,
    }


def sync_nanoka_base_stats(
    config_dir: Path,
    *,
    version: str = NANOKA_DEFAULT_VERSION,
    locale: str = NANOKA_DEFAULT_LOCALE,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    dry_run: bool = False,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    config_dir = Path(config_dir)
    model_path = config_dir / "my_roles_model.json"
    roles_path = config_dir / "roles.json"

    model = read_json(model_path, default={}) or {}
    roles_meta = read_json(roles_path, default={}) or {}
    if not isinstance(model, dict):
        raise RuntimeError("my_roles_model.json 格式异常，无法同步白值。")
    if not isinstance(roles_meta, dict):
        roles_meta = {}

    character_index = fetch_character_index(version=version, base_url=base_url, timeout=timeout)

    remote_by_role: dict[str, dict[str, dict[str, float]]] = {}
    skipped: list[str] = []
    fetch_errors: list[str] = []

    for role_name in model:
        if not isinstance(model.get(role_name), dict):
            continue
        character_id = resolve_character_id(
            role_name,
            roles_meta=roles_meta,
            character_index=character_index,
        )
        if not character_id:
            skipped.append(role_name)
            continue
        try:
            detail = fetch_character_detail(
                character_id,
                version=version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            remote_by_role[role_name] = extract_level_base_stats(detail, levels=levels)
        except Exception as exc:  # noqa: BLE001 - collect per-role failures for summary
            fetch_errors.append(f"{role_name}({character_id}): {exc}")

    merged, summary = merge_nanoka_base_stats_into_model(model, remote_by_role)
    summary.update(
        {
            "api_role_count": len(character_index),
            "matched_count": len(remote_by_role),
            "skipped_count": len(skipped),
            "skipped_roles": skipped,
            "fetch_error_count": len(fetch_errors),
            "fetch_errors": fetch_errors,
            "version": version,
            "locale": locale,
            "dry_run": dry_run,
            "wrote": False,
        }
    )

    if dry_run or not summary["updated_count"]:
        return summary

    write_json_atomic(model_path, merged, indent=2)
    summary["wrote"] = True
    return summary
