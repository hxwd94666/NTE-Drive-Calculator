# 从 nanoka.cc 静态 JSON 同步角色各等级基础白值。
"""Fetch character base white stats from nanoka static data and merge into configs.

This is not HTML scraping: nanoka.cc serves game data as versioned JSON under
https://static.nanoka.cc/nte/{version}/...
"""

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


NANOKA_DEFAULT_VERSION = "latest"

BASE_STAT_KEYS = ("生命白值", "攻击力白值", "防御力白值", "暴击率%", "暴击伤害%")

STAT_ID_TO_KEY = {
    "HPMaxBase": "生命白值",
    "AtkBase": "攻击力白值",
    "DefBase": "防御力白值",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
}

ELEMENT_TO_ATK_TYPE = {
    "Cosmos": "光",
    "Chaos": "暗",
    "Nature": "灵",
    "Psyche": "魂",
    "Incantation": "咒",
    "Lakshana": "相",
}

# nanoka display name -> local role key
CHARACTER_NAME_ALIASES = {
    "「零」": "主角",
    "零": "主角",
    "法帝娅": "法蒂娅",
}


def fetch_character_index(
    *,
    version: str,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    return fetch_id_index(
        version=version,
        resource="character",
        base_url=base_url,
        timeout=timeout,
    )


def fetch_character_detail(
    character_id: str,
    *,
    version: str,
    locale: str = NANOKA_DEFAULT_LOCALE,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    payload = request_json(
        static_url(version, locale, "character", f"{character_id}.json", base_url=base_url),
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise RuntimeError(f"nanoka 角色详情格式异常: {character_id}")
    return payload


def extract_level_base_stats(
    character: dict[str, Any],
    *,
    levels: tuple[int, ...] = DEFAULT_LEVELS,
) -> dict[str, dict[str, float]]:
    subject = f"角色 {character.get('id') or character.get('name') or '?'}"
    return extract_level_stats_from_nanoka_stats(
        character.get("stats"),
        stat_id_to_key=STAT_ID_TO_KEY,
        levels=levels,
        required_ids=tuple(STAT_ID_TO_KEY),
        subject=subject,
    )


def _workshop_ids_for_role(role_meta: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    value = role_meta.get("workshop_item_id")
    if value is not None and str(value).strip():
        ids.append(str(value).strip())
    raw_ids = role_meta.get("workshop_item_ids")
    if isinstance(raw_ids, list):
        for item in raw_ids:
            text = str(item or "").strip()
            if text and text not in ids:
                ids.append(text)
    return ids


def local_role_name_for_remote(zh_name: str) -> str:
    text = str(zh_name or "").strip()
    if text in CHARACTER_NAME_ALIASES:
        return CHARACTER_NAME_ALIASES[text]
    normalized = normalize_display_name(text)
    return CHARACTER_NAME_ALIASES.get(normalized, normalized or text)


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
        if local_role_name_for_remote(zh_name) == role_name or zh_name == role_name:
            return character_id
    return None


def board_matrix_from_equip_slots(equip_slots: Any) -> list[list[int]]:
    """Crop nanoka 7x7 equip slots to local 5x5 board_matrix."""
    slots = equip_slots.get("slots") if isinstance(equip_slots, dict) else None
    if not isinstance(slots, list) or len(slots) < 6:
        return [[0] * 5 for _ in range(5)]
    matrix: list[list[int]] = []
    for row_index in range(1, 6):
        row = slots[row_index] if row_index < len(slots) else []
        if not isinstance(row, list):
            row = []
        matrix.append([int(row[col]) if col < len(row) else 0 for col in range(1, 6)])
    return matrix


def build_role_model_stub(
    *,
    role_name: str,
    detail: dict[str, Any],
    level_sub_stats: dict[str, dict[str, float]],
) -> dict[str, Any]:
    element = str(detail.get("element") or detail.get("element_name") or "")
    current_level = "80" if "80" in level_sub_stats else next(iter(level_sub_stats), "80")
    return {
        "role_name": role_name,
        "atk_type": ELEMENT_TO_ATK_TYPE.get(element, ""),
        "weapon_type": "",
        "level": int(current_level) if str(current_level).isdigit() else 80,
        "desc": str(detail.get("desc") or ""),
        "level_sub_stats": level_sub_stats,
        "mix_level_sub_stats": {},
        "sub_stats": dict(level_sub_stats.get(current_level, {})),
        "drive": {"blueprint_layout": [], "drives": [], "sub_stats": {}},
        "tape": {},
        "weapon": {},
        "set_bonus": {"display_name": "", "skill": {}, "skill_2": {}, "skill_cover": 0.8},
    }


def build_role_meta_stub(
    *,
    role_name: str,
    character_id: str,
    detail: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role_name": role_name,
        "default_set": "",
        "extra_shape_label": "",
        "extra_shape_buffs": {},
        "board_matrix": board_matrix_from_equip_slots(detail.get("equip_slots")),
        "weights": {},
        "main_weights": {},
        "workshop_item_id": str(character_id),
        "workshop_item_ids": [str(character_id)],
    }


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
        changed, diffs = merge_level_sub_stats(
            role_data,
            remote_levels,
            equal_keys=BASE_STAT_KEYS,
        )
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
    add_missing: bool = False,
    base_url: str = NANOKA_STATIC_BASE,
    site_url: str = NANOKA_SITE_URL,
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

    resolved_version = resolve_version(version, site_url=site_url, timeout=timeout)
    character_index = fetch_character_index(
        version=resolved_version,
        base_url=base_url,
        timeout=timeout,
    )

    remote_by_role: dict[str, dict[str, dict[str, float]]] = {}
    skipped: list[str] = []
    fetch_errors: list[str] = []
    added_roles: list[str] = []
    missing_remote: list[str] = []

    for role_name in list(model):
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
                version=resolved_version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            remote_by_role[role_name] = extract_level_base_stats(detail, levels=levels)
        except Exception as exc:  # noqa: BLE001 - collect per-role failures for summary
            fetch_errors.append(f"{role_name}({character_id}): {exc}")

    seen_local = set(model)
    for character_id, meta in character_index.items():
        role_name = local_role_name_for_remote(str(meta.get("zh") or ""))
        if not role_name or role_name in seen_local:
            continue
        missing_remote.append(role_name)
        if not add_missing:
            continue
        try:
            detail = fetch_character_detail(
                character_id,
                version=resolved_version,
                locale=locale,
                base_url=base_url,
                timeout=timeout,
            )
            level_sub_stats = extract_level_base_stats(detail, levels=levels)
            model[role_name] = build_role_model_stub(
                role_name=role_name,
                detail=detail,
                level_sub_stats=level_sub_stats,
            )
            if role_name not in roles_meta or not isinstance(roles_meta.get(role_name), dict):
                roles_meta[role_name] = build_role_meta_stub(
                    role_name=role_name,
                    character_id=character_id,
                    detail=detail,
                )
            seen_local.add(role_name)
            added_roles.append(role_name)
            remote_by_role[role_name] = level_sub_stats
        except Exception as exc:  # noqa: BLE001
            fetch_errors.append(f"{role_name}({character_id}): {exc}")

    merged, summary = merge_nanoka_base_stats_into_model(model, remote_by_role)
    summary.update(
        {
            "api_role_count": len(character_index),
            "matched_count": len(remote_by_role),
            "skipped_count": len(skipped),
            "skipped_roles": skipped,
            "added_count": len(added_roles),
            "added_roles": added_roles,
            "missing_remote_count": len(missing_remote),
            "missing_remote_roles": missing_remote,
            "fetch_error_count": len(fetch_errors),
            "fetch_errors": fetch_errors,
            "version": resolved_version,
            "locale": locale,
            "dry_run": dry_run,
            "wrote": False,
            "wrote_roles_json": False,
        }
    )

    should_write_model = bool(summary["updated_count"] or added_roles)
    should_write_roles = bool(added_roles)
    if dry_run or not (should_write_model or should_write_roles):
        return summary

    if should_write_model:
        write_json_atomic(model_path, merged, indent=2)
        summary["wrote"] = True
    if should_write_roles:
        write_json_atomic(roles_path, roles_meta, indent=2)
        summary["wrote_roles_json"] = True
    return summary
