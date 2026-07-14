# 从 nanoka.cc 静态 JSON 同步武器各等级基础属性。
"""Fetch weapon base stats from nanoka static data and merge into weapons.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.features.settings.nanoka_client import (
    DEFAULT_LEVELS,
    NANOKA_API_TIMEOUT_SECONDS,
    NANOKA_DEFAULT_LOCALE,
    NANOKA_SITE_URL,
    NANOKA_STATIC_BASE,
    extract_level_stats_from_nanoka_stats,
    fetch_id_index,
    merge_level_sub_stats,
    normalize_display_name,
    request_json,
    resolve_version,
    static_url,
)
from src.storage.json_store import read_json, write_json_atomic


WEAPON_STAT_ID_TO_KEY = {
    "AtkBase": "攻击力白值",
    "AtkUp": "攻击力%",
    "HPMaxUp": "生命值%",
    "DefUp": "防御力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "UnbalIntensityBase": "倾陷强度",
    "ChargeGetEfficiencyBase": "充能效率%",
}


def fetch_weapon_index(
    *,
    version: str,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    return fetch_id_index(
        version=version,
        resource="weapon",
        base_url=base_url,
        timeout=timeout,
    )


def fetch_weapon_detail(
    weapon_id: str,
    *,
    version: str,
    locale: str = NANOKA_DEFAULT_LOCALE,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = request_json(
        static_url(version, locale, "weapon", f"{weapon_id}.json", base_url=base_url),
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"nanoka 武器详情格式异常: {weapon_id}")
    return payload


def extract_weapon_level_stats(
    weapon: dict[str, Any],
    *,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
) -> dict[str, dict[str, float]]:
    subject = f"武器 {weapon.get('id') or weapon.get('name') or '?'}"
    return extract_level_stats_from_nanoka_stats(
        weapon.get("stats"),
        stat_id_to_key=WEAPON_STAT_ID_TO_KEY,
        levels=levels,
        required_ids=("AtkBase",),
        subject=subject,
    )


def _local_weapon_lookup(weapons: dict[str, Any]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, data in weapons.items():
        candidates = {str(key)}
        if isinstance(data, dict) and data.get("name"):
            candidates.add(str(data.get("name")))
        for name in candidates:
            normalized = normalize_display_name(name)
            if normalized and normalized not in lookup:
                lookup[normalized] = str(key)
    return lookup


def resolve_local_weapon_key(
    remote_name: str,
    *,
    weapons: dict[str, Any],
    lookup: dict[str, str] | None = None,
) -> str | None:
    lookup = lookup if lookup is not None else _local_weapon_lookup(weapons)
    normalized = normalize_display_name(remote_name)
    if normalized in lookup:
        return lookup[normalized]
    if remote_name in weapons:
        return remote_name
    return None


def build_weapon_stub(
    *,
    weapon_name: str,
    detail: dict[str, Any],
    level_sub_stats: dict[str, dict[str, float]],
) -> dict[str, Any]:
    current_level = "80" if "80" in level_sub_stats else next(iter(level_sub_stats), "80")
    return {
        "name": weapon_name,
        "type": str(detail.get("type_name") or ""),
        "level": int(current_level) if str(current_level).isdigit() else 80,
        "mix_level": 1,
        "desc": str(detail.get("description") or detail.get("context") or ""),
        "sub_stats": dict(level_sub_stats.get(current_level, {})),
        "level_sub_stats": level_sub_stats,
        "ascension_materials": {},
        "recommended_characters": [],
        "mix_level_sub_stats": {},
        "skill_desc": [],
    }


def merge_nanoka_weapon_stats(
    weapons: dict[str, Any],
    remote_by_weapon: dict[str, dict[str, dict[str, float]]],
    *,
    type_by_weapon: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = json.loads(json.dumps(weapons, ensure_ascii=False))
    updated: list[str] = []
    unchanged: list[str] = []
    diffs: dict[str, list[dict[str, Any]]] = {}
    type_by_weapon = type_by_weapon or {}

    for weapon_key, remote_levels in remote_by_weapon.items():
        weapon_data = merged.get(weapon_key)
        if not isinstance(weapon_data, dict):
            continue

        changed, weapon_diffs = merge_level_sub_stats(weapon_data, remote_levels)
        type_name = str(type_by_weapon.get(weapon_key) or "").strip()
        if type_name and not str(weapon_data.get("type") or "").strip():
            weapon_data["type"] = type_name
            changed = True

        if changed:
            updated.append(weapon_key)
            if weapon_diffs:
                diffs[weapon_key] = weapon_diffs
        else:
            unchanged.append(weapon_key)

    return merged, {
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "updated_weapons": updated,
        "unchanged_weapons": unchanged,
        "diffs": diffs,
    }


def sync_nanoka_weapon_stats(
    config_dir: Path,
    *,
    version: str = "latest",
    locale: str = NANOKA_DEFAULT_LOCALE,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    dry_run: bool = False,
    add_missing: bool = False,
    base_url: str = NANOKA_STATIC_BASE,
    site_url: str = NANOKA_SITE_URL,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    config_dir = Path(config_dir)
    weapons_path = config_dir / "weapons.json"
    weapons = read_json(weapons_path, default={}) or {}
    if not isinstance(weapons, dict):
        raise RuntimeError("weapons.json 格式异常，无法同步武器属性。")

    resolved_version = resolve_version(version, site_url=site_url, timeout=timeout)
    weapon_index = fetch_weapon_index(version=resolved_version, base_url=base_url, timeout=timeout)
    lookup = _local_weapon_lookup(weapons)

    remote_by_weapon: dict[str, dict[str, dict[str, float]]] = {}
    type_by_weapon: dict[str, str] = {}
    matched_ids: set[str] = set()
    fetch_errors: list[str] = []
    added_weapons: list[str] = []
    missing_remote: list[str] = []
    skipped_local: list[str] = []

    for weapon_id, meta in weapon_index.items():
        remote_name = str(meta.get("zh") or meta.get("name") or "").strip()
        local_key = resolve_local_weapon_key(remote_name, weapons=weapons, lookup=lookup)
        if not local_key:
            continue
        matched_ids.add(str(weapon_id))
        try:
            detail = fetch_weapon_detail(
                weapon_id,
                version=resolved_version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            remote_by_weapon[local_key] = extract_weapon_level_stats(detail, levels=levels)
            type_by_weapon[local_key] = str(detail.get("type_name") or "")
        except Exception as exc:  # noqa: BLE001
            fetch_errors.append(f"{local_key}({weapon_id}): {exc}")

    for local_key, data in weapons.items():
        if isinstance(data, dict) and local_key not in remote_by_weapon:
            skipped_local.append(local_key)

    for weapon_id, meta in weapon_index.items():
        if str(weapon_id) in matched_ids:
            continue
        remote_name = normalize_display_name(str(meta.get("zh") or meta.get("name") or weapon_id))
        if not remote_name:
            continue
        missing_remote.append(remote_name)
        if not add_missing or remote_name in weapons:
            continue
        try:
            detail = fetch_weapon_detail(
                weapon_id,
                version=resolved_version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            level_sub_stats = extract_weapon_level_stats(detail, levels=levels)
            weapons[remote_name] = build_weapon_stub(
                weapon_name=remote_name,
                detail=detail,
                level_sub_stats=level_sub_stats,
            )
            remote_by_weapon[remote_name] = level_sub_stats
            type_by_weapon[remote_name] = str(detail.get("type_name") or "")
            added_weapons.append(remote_name)
            lookup[normalize_display_name(remote_name)] = remote_name
        except Exception as exc:  # noqa: BLE001
            fetch_errors.append(f"{remote_name}({weapon_id}): {exc}")

    merged, summary = merge_nanoka_weapon_stats(
        weapons,
        remote_by_weapon,
        type_by_weapon=type_by_weapon,
    )
    summary.update(
        {
            "api_weapon_count": len(weapon_index),
            "matched_count": len(remote_by_weapon),
            "skipped_count": len(skipped_local),
            "skipped_weapons": skipped_local,
            "added_count": len(added_weapons),
            "added_weapons": added_weapons,
            "missing_remote_count": len(missing_remote),
            "missing_remote_weapons": missing_remote,
            "fetch_error_count": len(fetch_errors),
            "fetch_errors": fetch_errors,
            "version": resolved_version,
            "locale": locale,
            "dry_run": dry_run,
            "wrote": False,
        }
    )

    if dry_run or not (summary["updated_count"] or added_weapons):
        return summary

    write_json_atomic(weapons_path, merged, indent=2)
    summary["wrote"] = True
    return summary
