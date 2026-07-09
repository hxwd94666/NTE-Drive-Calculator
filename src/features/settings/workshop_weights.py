# 同步异环工坊开放接口中的角色词条权重。
"""Fetch and merge workshop role weight configs into roles.json."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from src.app.constants import APP_VERSION, WORKSHOP_WEIGHT_CONFIGS_API
from src.domain.stat_catalog import StatCatalog
from src.storage.json_store import backup_json, read_json, write_json_atomic


WORKSHOP_API_TIMEOUT_SECONDS = 8
WORKSHOP_ROLE_NAME_ALIASES = {
    "异能者(男)": "主角",
    "异能者(女)": "主角",
}


def fetch_workshop_weight_configs(
    api_key: str,
    *,
    api_url: str = WORKSHOP_WEIGHT_CONFIGS_API,
    timeout: int = WORKSHOP_API_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    api_key = str(api_key or "").strip()
    if not api_key:
        raise ValueError("请先填写异环工坊 Open API Key。")
    request = urllib.request.Request(
        api_url,
        method="GET",
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError("异环工坊 API Key 无效或已过期。") from exc
        raise RuntimeError(f"异环工坊接口请求失败，HTTP {exc.code}。") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"异环工坊接口请求异常: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError("异环工坊接口返回内容不是有效 JSON。") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("异环工坊接口返回格式异常。")
    if int(payload.get("code", 0) or 0) != 200:
        message = str(payload.get("msg") or "未知错误")
        raise RuntimeError(f"异环工坊接口返回失败: {message}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("异环工坊接口 data 不是角色数组。")
    return [item for item in data if isinstance(item, dict)]


def _weight_items(weight_config: Any) -> list[dict[str, Any]]:
    if isinstance(weight_config, dict):
        raw_items = weight_config.get("weights")
    else:
        raw_items = weight_config
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def _canonical_stat_name(catalog: StatCatalog, raw_name: str) -> str:
    raw_name = str(raw_name or "").strip()
    if not raw_name:
        return ""
    normalized = catalog.normalize_stat_name(raw_name, is_percent="%" in raw_name)
    if normalized:
        return normalized
    mapped = catalog.flexible_weight_name(raw_name)
    return mapped or raw_name


def _positive_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number > 0 else 0.0


def parse_workshop_role_weights(
    records: list[dict[str, Any]],
    catalog: StatCatalog,
) -> dict[str, dict[str, Any]]:
    parsed: dict[str, dict[str, Any]] = {}
    for record in records:
        role_name = str(record.get("name") or "").strip()
        if not role_name:
            continue
        weights: dict[str, float] = {}
        main_weights: dict[str, float] = {}
        for item in _weight_items(record.get("weightConfig")):
            stat_name = _canonical_stat_name(catalog, item.get("name") or item.get("key") or "")
            if not stat_name:
                continue
            sub_value = _positive_float(item.get("value"))
            main_value = _positive_float(item.get("main_value"))
            if sub_value:
                weights[stat_name] = sub_value
            if main_value:
                main_weights[stat_name] = main_value
        if weights or main_weights:
            parsed[role_name] = {
                "item_id": str(record.get("itemId") or ""),
                "weights": weights,
                "main_weights": main_weights,
            }
    return parsed


def _local_role_name(api_role_name: str) -> str:
    return WORKSHOP_ROLE_NAME_ALIASES.get(api_role_name, api_role_name)


def merge_workshop_weights_into_roles(
    roles: dict[str, Any],
    workshop_roles: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    merged = json.loads(json.dumps(roles, ensure_ascii=False))
    updated = []
    unchanged = []
    skipped = []
    seen_local_roles = set()
    updated_set = set()

    def mark_updated(name: str) -> None:
        if name not in updated_set:
            updated.append(name)
            updated_set.add(name)

    for api_role_name, payload in workshop_roles.items():
        role_name = _local_role_name(api_role_name)
        role_data = merged.get(role_name)
        if not isinstance(role_data, dict):
            skipped.append(api_role_name)
            continue
        if role_name in seen_local_roles:
            duplicate_changed = False
            if api_role_name != role_name:
                aliases = role_data.get("workshop_aliases")
                if not isinstance(aliases, list):
                    aliases = []
                if api_role_name not in aliases:
                    aliases.append(api_role_name)
                    role_data["workshop_aliases"] = aliases
                    duplicate_changed = True
            existing_ids = role_data.get("workshop_item_ids")
            if not isinstance(existing_ids, list):
                existing_ids = [role_data["workshop_item_id"]] if role_data.get("workshop_item_id") else []
            if payload.get("item_id") and payload["item_id"] not in existing_ids:
                existing_ids.append(payload["item_id"])
                role_data["workshop_item_ids"] = existing_ids
                duplicate_changed = True
            if duplicate_changed:
                mark_updated(role_name)
            else:
                unchanged.append(api_role_name)
            continue
        old_weights = role_data.get("weights", {}) or {}
        old_main_weights = role_data.get("main_weights", {}) if "main_weights" in role_data else None
        new_weights = payload.get("weights", {}) or {}
        new_main_weights = payload.get("main_weights", {}) or {}
        metadata_changed = False
        if api_role_name != role_name:
            aliases = role_data.get("workshop_aliases")
            if not isinstance(aliases, list):
                aliases = []
            if api_role_name not in aliases:
                aliases.append(api_role_name)
                role_data["workshop_aliases"] = aliases
                metadata_changed = True
        if old_weights == new_weights and old_main_weights == new_main_weights:
            if metadata_changed:
                mark_updated(role_name)
            else:
                unchanged.append(role_name)
            seen_local_roles.add(role_name)
            continue
        role_data["weights"] = new_weights
        role_data["main_weights"] = new_main_weights
        if payload.get("item_id"):
            role_data["workshop_item_id"] = payload["item_id"]
            role_data["workshop_item_ids"] = [payload["item_id"]]
        mark_updated(role_name)
        seen_local_roles.add(role_name)
    return merged, {
        "api_role_count": len(workshop_roles),
        "updated_count": len(updated),
        "unchanged_count": len(unchanged),
        "skipped_count": len(skipped),
        "updated_roles": updated,
        "skipped_roles": skipped,
    }


def sync_workshop_weights(config_dir: Path, api_key: str) -> dict[str, Any]:
    config_dir = Path(config_dir)
    roles_path = config_dir / "roles.json"
    roles = read_json(roles_path, default={}) or {}
    if not isinstance(roles, dict):
        raise RuntimeError("roles.json 格式异常，无法同步权重。")
    records = fetch_workshop_weight_configs(api_key)
    catalog = StatCatalog.from_config_dir(config_dir)
    workshop_roles = parse_workshop_role_weights(records, catalog)
    merged, summary = merge_workshop_weights_into_roles(roles, workshop_roles)
    if summary["updated_count"]:
        backup_json(roles_path, suffix=".workshop.bak")
        write_json_atomic(roles_path, merged, indent=4)
    return summary
