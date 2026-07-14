# nanoka.cc 静态数据客户端：请求、版本探测与通用解析。
"""Shared helpers for reading nanoka.cc versioned static JSON.

Not HTML scraping of page content: version discovery reads the site's embedded
static data URLs (the live dataset it currently serves), then fetches JSON from
https://static.nanoka.cc/nte/{version}/...
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from src.app.constants import APP_VERSION


NANOKA_SITE_URL = "https://nte.nanoka.cc/"
NANOKA_STATIC_BASE = "https://static.nanoka.cc/nte"
NANOKA_DEFAULT_LOCALE = "zh"
NANOKA_API_TIMEOUT_SECONDS = 30

DEFAULT_LEVELS = (1, 20, 30, 40, 50, 60, 70, 80)

_VERSION_RE = re.compile(r"(?:static\.nanoka\.cc)?/nte/(\d+(?:\.\d+)*)/")


def parse_version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for part in str(version).strip().split("."):
        if not part.isdigit():
            raise ValueError(f"Invalid nanoka version: {version}")
        parts.append(int(part))
    if not parts:
        raise ValueError(f"Invalid nanoka version: {version}")
    return tuple(parts)


def _request_bytes(url: str, *, timeout: int = NANOKA_API_TIMEOUT_SECONDS) -> bytes:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "*/*",
            "User-Agent": f"NTE-Drive-Calc/{APP_VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"nanoka 请求失败，HTTP {exc.code}: {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"nanoka 请求异常: {exc}") from exc


def request_text(url: str, *, timeout: int = NANOKA_API_TIMEOUT_SECONDS) -> str:
    try:
        return _request_bytes(url, timeout=timeout).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"nanoka 响应不是有效文本: {url}") from exc


def request_json(url: str, *, timeout: int = NANOKA_API_TIMEOUT_SECONDS) -> Any:
    try:
        return json.loads(request_text(url, timeout=timeout))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"nanoka 响应不是有效 JSON: {url}") from exc


def detect_live_version(
    *,
    site_url: str = NANOKA_SITE_URL,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> str:
    """Detect the dataset version currently served by nte.nanoka.cc.

    The homepage SSR embeds live static URLs like /nte/1.2/character.json.
    When the site moves to 1.3/2.0, those paths update accordingly.
    """
    html = request_text(site_url, timeout=timeout)
    versions = _VERSION_RE.findall(html)
    if not versions:
        raise RuntimeError("无法从 nanoka 首页解析数据版本，请改用 --version 指定。")

    # Prefer the most frequently referenced version on the live page.
    counts: dict[str, int] = {}
    for version in versions:
        counts[version] = counts.get(version, 0) + 1
    ranked = sorted(
        counts.items(),
        key=lambda item: (item[1], parse_version_tuple(item[0])),
        reverse=True,
    )
    return ranked[0][0]


def resolve_version(
    version: str | None,
    *,
    site_url: str = NANOKA_SITE_URL,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> str:
    text = str(version or "latest").strip()
    if not text or text.lower() in {"latest", "auto", "current", "live"}:
        return detect_live_version(site_url=site_url, timeout=timeout)
    parse_version_tuple(text)  # validate
    return text


def static_url(
    version: str,
    *parts: str,
    base_url: str = NANOKA_STATIC_BASE,
) -> str:
    suffix = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    return f"{base_url.rstrip('/')}/{version}/{suffix}"


def extract_level_stats_from_nanoka_stats(
    stats: Any,
    *,
    stat_id_to_key: dict[str, str],
    levels: tuple[int, ...] = DEFAULT_LEVELS,
    required_ids: tuple[str, ...] | None = None,
    subject: str = "条目",
) -> dict[str, dict[str, float]]:
    """Build level -> {local_stat: value} from nanoka stats arrays (1-indexed levels)."""
    by_id: dict[str, list[Any]] = {}
    for item in stats or []:
        if not isinstance(item, dict):
            continue
        stat_id = str(item.get("id_stats") or "")
        values = item.get("values")
        if stat_id in stat_id_to_key and isinstance(values, list):
            by_id[stat_id] = values

    required = required_ids if required_ids is not None else tuple(stat_id_to_key)
    missing = [stat_id for stat_id in required if stat_id not in by_id]
    if missing:
        raise RuntimeError(f"{subject} 缺少属性: {', '.join(missing)}")

    level_stats: dict[str, dict[str, float]] = {}
    for level in levels:
        index = level - 1
        row: dict[str, float] = {}
        for stat_id, values in by_id.items():
            if index < 0 or index >= len(values):
                raise RuntimeError(f"{subject} 的 {stat_id} 缺少等级 {level} 数据。")
            row[stat_id_to_key[stat_id]] = float(values[index])
        level_stats[str(level)] = row
    return level_stats


def normalize_display_name(name: str) -> str:
    text = str(name or "").strip()
    for token in ("「", "」", "『", "』", '"', "'"):
        text = text.replace(token, "")
    return text.strip()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_stats(target: dict[str, Any] | None, incoming: dict[str, float]) -> dict[str, Any]:
    merged = dict(target) if isinstance(target, dict) else {}
    for key, value in incoming.items():
        merged[key] = float(value)
    return merged


def stats_equal(
    left: dict[str, Any] | None,
    right: dict[str, float],
    *,
    keys: tuple[str, ...] | None = None,
) -> bool:
    left = left if isinstance(left, dict) else {}
    compare_keys = keys if keys is not None else tuple(right)
    for key in compare_keys:
        if abs(as_float(left.get(key)) - as_float(right.get(key))) > 0.01:
            return False
    return True


def diff_stats(
    local_stats: dict[str, Any] | None,
    remote_stats: dict[str, float],
    *,
    level_key: str,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    local_dict = local_stats if isinstance(local_stats, dict) else None
    for key, new_value in remote_stats.items():
        if local_dict is None:
            diffs.append({"level": level_key, "stat": key, "local": None, "remote": float(new_value)})
            continue
        old_value = as_float(local_dict.get(key))
        if abs(old_value - float(new_value)) > 0.01:
            diffs.append(
                {
                    "level": level_key,
                    "stat": key,
                    "local": old_value,
                    "remote": float(new_value),
                }
            )
    return diffs


def merge_level_sub_stats(
    entity: dict[str, Any],
    remote_levels: dict[str, dict[str, float]],
    *,
    equal_keys: tuple[str, ...] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Update entity level_sub_stats/sub_stats from remote level rows.

    Returns (changed, diffs).
    """
    local_levels = entity.get("level_sub_stats")
    if not isinstance(local_levels, dict):
        local_levels = {}
        entity["level_sub_stats"] = local_levels

    changed = False
    diffs: list[dict[str, Any]] = []
    for level_key, remote_stats in remote_levels.items():
        local_stats = local_levels.get(level_key)
        local_dict = local_stats if isinstance(local_stats, dict) else None
        compare_keys = equal_keys if equal_keys is not None else tuple(remote_stats)
        if stats_equal(local_dict, remote_stats, keys=compare_keys):
            continue
        diffs.extend(diff_stats(local_dict, remote_stats, level_key=level_key))
        local_levels[level_key] = apply_stats(local_dict, remote_stats)
        changed = True

    current_level = str(entity.get("level", 80))
    if current_level in local_levels:
        current_stats = local_levels[current_level]
        sub_stats = entity.get("sub_stats")
        before = dict(sub_stats) if isinstance(sub_stats, dict) else {}
        entity["sub_stats"] = apply_stats(sub_stats if isinstance(sub_stats, dict) else {}, current_stats)
        if before != entity["sub_stats"]:
            changed = True
    return changed, diffs


def fetch_id_index(
    *,
    version: str,
    resource: str,
    base_url: str = NANOKA_STATIC_BASE,
    timeout: int = NANOKA_API_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    payload = request_json(static_url(version, f"{resource}.json", base_url=base_url), timeout=timeout)
    if not isinstance(payload, dict):
        raise RuntimeError(f"nanoka {resource}.json 格式异常。")
    return {
        str(item_id): item
        for item_id, item in payload.items()
        if isinstance(item, dict)
    }
