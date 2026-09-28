# 将官方角色和弧盘静态数据投影为可共享的配置模板文件。
"""Generate the shared official role/fork template cache used by role features."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.services.advancement_stage_service import fork_active_panel_stats
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_FORK_PROPERTY_DISPLAY = {
    "AtkBase": ("攻击力白值", 1.0),
    "AtkUp": ("攻击力%", 100.0),
    "ChargeGetEfficiencyBase": ("攻击力%", 100.0),
    "CritBase": ("暴击率%", 100.0),
    "CritDamageBase": ("暴击伤害%", 100.0),
    "DefUp": ("防御力%", 100.0),
    "HPMaxUp": ("生命值%", 100.0),
    "UnbalIntensityBase": ("倾陷强度", 1.0),
}


def load_official_role_fork_templates(
    static_database_path: str | Path | None = None,
) -> dict[str, Any]:
    """直接读取发行静态库中的角色与弧盘官方模板。

    账号抓包只更新账号背包快照；游戏公共定义由官方文件构建器更新静态库，
    不再复制或写入 config JSON。
    """
    with StaticGameDataDao(static_database_path) as static_dao:
        return {
            "source": "game_static.sqlite3",
            "static_dataset": static_dao.dataset_info(),
            "roles": static_dao.list_role_template_characters(),
            "forks": static_dao.list_fork_templates(),
        }


def _fork_stats_at_level(
    template: dict[str, Any], level: int, refinement_level: int | None,
) -> dict[str, float]:
    """Project the legacy template model through the shared stage resolver."""
    stats: dict[str, float] = {}
    for property_id, value in fork_active_panel_stats(
        template,
        level,
        refinement_level=refinement_level,
    ).items():
        mapped = _FORK_PROPERTY_DISPLAY.get(property_id)
        if mapped is None:
            continue
        name, scale = mapped
        stats[name] = round(stats.get(name, 0.0) + value * scale, 4)
    return stats


# 弧盘模板投影是纯 CPU 计算（51 个弧盘逐级的面板统计，实测一次约 0.95 s），
# 且只随静态数据集发布变化。按数据集身份缓存后，后续重建不再重复这笔开销。
_WEAPON_MODEL_CACHE: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}


def clear_weapon_model_cache() -> None:
    """丢弃缓存的弧盘投影（测试与静态数据集晋升后使用）。"""

    _WEAPON_MODEL_CACHE.clear()


def _weapon_model_cache_key(
    payload: Mapping[str, Any],
) -> tuple[str, str, str] | None:
    dataset = payload.get("static_dataset")
    if not isinstance(dataset, Mapping):
        return None
    dataset_id = str(dataset.get("dataset_id") or "")
    if not dataset_id:
        return None
    return (
        dataset_id,
        str(dataset.get("importer_version") or ""),
        str(dataset.get("built_at_utc") or ""),
    )


def fork_templates_as_weapon_models(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Adapt official fork templates for current role and priority consumers.

    结果按静态数据集身份缓存；调用方只读取，不应修改返回的映射。
    """

    key = _weapon_model_cache_key(payload)
    if key is not None and key in _WEAPON_MODEL_CACHE:
        return _WEAPON_MODEL_CACHE[key]
    models = _build_weapon_models(payload)
    if key is not None:
        _WEAPON_MODEL_CACHE[key] = models
    return models


def _build_weapon_models(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for template in payload.get("forks", []) if isinstance(payload, dict) else []:
        if not isinstance(template, dict):
            continue
        name = str(template.get("name_zh") or "").strip()
        fork_id = str(template.get("fork_id") or "").strip()
        if not name or not fork_id:
            continue
        levels = sorted({
            int(str(row["level"]))
            for row in template.get("upgrade_levels", [])
            if isinstance(row, dict) and row.get("level") is not None
        })
        # Role management has no per-role refinement selector.  Its automatic
        # cap therefore uses the live default model (精炼 1), not a fictitious
        # max-refinement fork.
        refinement_level = 1
        level_stats = {
            str(level): _fork_stats_at_level(template, level, refinement_level)
            for level in levels
        }
        maximum_level = levels[-1] if levels else 1
        models[name] = {
            "fork_id": fork_id,
            "name": name,
            "type": str(template.get("fork_type_name_zh") or ""),
            "level": maximum_level,
            "mix_level": refinement_level,
            "max_breakthrough": int(template.get("max_breakthrough") or 0),
            "max_star": int(template.get("max_star") or 0),
            "level_sub_stats": level_stats,
            "sub_stats": dict(level_stats.get(str(maximum_level), {})),
            "star_levels": list(template.get("star_levels") or []),
        }
    return models
