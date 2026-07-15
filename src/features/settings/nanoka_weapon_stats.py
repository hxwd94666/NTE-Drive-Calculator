# 从 nanoka.cc 静态数据同步武器基础属性。

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.features.settings.nanoka_client import (
    DEFAULT_LEVELS,
    NANOKA_API_TIMEOUT_SECONDS,
    NANOKA_DEFAULT_LOCALE,
    NANOKA_DEFAULT_VERSION,
    NANOKA_SITE_URL,
    NANOKA_STATIC_BASE,
    complete_sync_summary,
    extract_level_stats_from_nanoka_stats,
    fetch_id_index,
    fetch_resource_detail,
    merge_level_sub_stats,
    normalize_display_name,
    resolve_version,
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
WEAPON_STAT_KEYS = tuple(WEAPON_STAT_ID_TO_KEY.values())


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
    return fetch_resource_detail(
        "weapon",
        weapon_id,
        version=version,
        locale=locale,
        base_url=base_url,
        timeout=timeout,
    )


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
            if not normalized:
                continue
            existing = lookup.get(normalized)
            if existing is not None and existing != str(key):
                raise RuntimeError(f"本地武器名称冲突: {existing}, {key}")
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

        changed, weapon_diffs = merge_level_sub_stats(
            weapon_data,
            remote_levels,
            managed_keys=WEAPON_STAT_KEYS,
        )
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


def _fetch_existing_weapon_stats(
    weapons: dict[str, Any],
    weapon_index: dict[str, dict[str, Any]],
    lookup: dict[str, str],
    *,
    version: str,
    locale: str,
    levels: tuple[int, ...],
    base_url: str,
    timeout: int,
) -> tuple[
    dict[str, dict[str, dict[str, float]]],
    dict[str, str],
    set[str],
    list[str],
    list[str],
]:
    matches: dict[str, list[str]] = {}
    matched_ids: set[str] = set()
    for weapon_id, meta in weapon_index.items():
        remote_name = str(meta.get("zh") or meta.get("name") or "").strip()
        local_key = resolve_local_weapon_key(remote_name, weapons=weapons, lookup=lookup)
        if local_key:
            matches.setdefault(local_key, []).append(weapon_id)
            matched_ids.add(weapon_id)

    remote_by_weapon: dict[str, dict[str, dict[str, float]]] = {}
    type_by_weapon: dict[str, str] = {}
    errors: list[str] = []
    for local_key, weapon_ids in matches.items():
        if len(weapon_ids) > 1:
            errors.append(f"{local_key}: 匹配到多个远端武器 ID: {', '.join(weapon_ids)}")
            continue
        weapon_id = weapon_ids[0]
        try:
            detail = fetch_weapon_detail(
                weapon_id,
                version=version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            remote_by_weapon[local_key] = extract_weapon_level_stats(detail, levels=levels)
            type_by_weapon[local_key] = str(detail.get("type_name") or "")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{local_key}({weapon_id}): {exc}")

    skipped = [
        local_key
        for local_key, data in weapons.items()
        if isinstance(data, dict) and local_key not in remote_by_weapon
    ]
    return remote_by_weapon, type_by_weapon, matched_ids, skipped, errors


def _add_missing_weapons(
    weapons: dict[str, Any],
    weapon_index: dict[str, dict[str, Any]],
    matched_ids: set[str],
    remote_by_weapon: dict[str, dict[str, dict[str, float]]],
    type_by_weapon: dict[str, str],
    *,
    version: str,
    locale: str,
    levels: tuple[int, ...],
    add_missing: bool,
    base_url: str,
    timeout: int,
) -> tuple[list[str], list[str], list[str]]:
    missing_by_name: dict[str, list[str]] = {}
    for weapon_id, meta in weapon_index.items():
        if weapon_id in matched_ids:
            continue
        remote_name = normalize_display_name(str(meta.get("zh") or meta.get("name") or weapon_id))
        if remote_name and remote_name not in weapons:
            missing_by_name.setdefault(remote_name, []).append(weapon_id)

    missing = list(missing_by_name)
    added: list[str] = []
    errors: list[str] = []
    if not add_missing:
        return missing, added, errors

    for remote_name, weapon_ids in missing_by_name.items():
        if len(weapon_ids) > 1:
            errors.append(f"{remote_name}: 匹配到多个远端武器 ID: {', '.join(weapon_ids)}")
            continue
        weapon_id = weapon_ids[0]
        try:
            detail = fetch_weapon_detail(
                weapon_id,
                version=version,
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
            added.append(remote_name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{remote_name}({weapon_id}): {exc}")
    return missing, added, errors


def sync_nanoka_weapon_stats(
    config_dir: Path,
    *,
    version: str = NANOKA_DEFAULT_VERSION,
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
    (
        remote_by_weapon,
        type_by_weapon,
        matched_ids,
        skipped_local,
        fetch_errors,
    ) = _fetch_existing_weapon_stats(
        weapons,
        weapon_index,
        lookup,
        version=resolved_version,
        locale=locale,
        levels=levels,
        base_url=base_url,
        timeout=timeout,
    )
    missing_remote, added_weapons, add_errors = _add_missing_weapons(
        weapons,
        weapon_index,
        matched_ids,
        remote_by_weapon,
        type_by_weapon,
        version=resolved_version,
        locale=locale,
        levels=levels,
        add_missing=add_missing,
        base_url=base_url,
        timeout=timeout,
    )
    fetch_errors.extend(add_errors)

    merged, summary = merge_nanoka_weapon_stats(
        weapons,
        remote_by_weapon,
        type_by_weapon=type_by_weapon,
    )
    complete_sync_summary(
        summary,
        item_key="weapon",
        api_count=len(weapon_index),
        matched_count=len(remote_by_weapon),
        skipped=skipped_local,
        added=added_weapons,
        missing=missing_remote,
        errors=fetch_errors,
        version=resolved_version,
        locale=locale,
        dry_run=dry_run,
    )

    if dry_run or fetch_errors or not (summary["updated_count"] or added_weapons):
        return summary

    write_json_atomic(weapons_path, merged, indent=2)
    summary["wrote"] = True
    return summary
