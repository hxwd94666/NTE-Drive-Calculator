# 将官方角色和弧盘静态数据投影为可共享的配置模板文件。
"""Generate the shared official role/fork template cache used by role features."""

from __future__ import annotations

from typing import Any

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


def load_official_role_fork_templates() -> dict[str, Any]:
    """直接读取发行静态库中的角色与弧盘官方模板。

    账号抓包只更新账号背包快照；游戏公共定义由官方文件构建器更新静态库，
    不再复制或写入 config JSON。
    """
    with StaticGameDataDao() as static_dao:
        return {
            "source": "game_static.sqlite3",
            "static_dataset": static_dao.summary()["dataset"],
            "roles": static_dao.list_role_template_characters(),
            "forks": static_dao.list_fork_templates(),
        }


def _numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _add_modifiers(target: dict[str, float], modifiers: Any) -> None:
    for modifier in modifiers or []:
        if not isinstance(modifier, dict):
            continue
        mapped = _FORK_PROPERTY_DISPLAY.get(str(modifier.get("property_id") or ""))
        if mapped is None:
            continue
        name, scale = mapped
        target[name] = round(target.get(name, 0.0) + _numeric(modifier.get("value")) * scale, 4)


def _fork_stats_at_level(template: dict[str, Any], level: int) -> dict[str, float]:
    """Combine the official level row with the unlocked breakthrough row."""
    levels = [row for row in template.get("upgrade_levels", []) if isinstance(row, dict)]
    if not levels:
        return {}
    chosen = min(levels, key=lambda row: abs(int(row.get("level") or 0) - level))
    stats: dict[str, float] = {}
    _add_modifiers(stats, chosen.get("modifiers"))
    breakthroughs = [row for row in template.get("breakthroughs", []) if isinstance(row, dict)]
    available = [row for row in breakthroughs if int(row.get("max_fork_level") or 0) <= level]
    if available:
        _add_modifiers(stats, max(available, key=lambda row: int(row.get("stage") or 0)).get("modifiers"))
    return stats


def fork_templates_as_weapon_models(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Adapt official fork templates for current role and priority consumers."""
    models: dict[str, dict[str, Any]] = {}
    for template in payload.get("forks", []) if isinstance(payload, dict) else []:
        if not isinstance(template, dict):
            continue
        name = str(template.get("name_zh") or "").strip()
        fork_id = str(template.get("fork_id") or "").strip()
        if not name or not fork_id:
            continue
        levels = sorted({
            int(row.get("level"))
            for row in template.get("upgrade_levels", [])
            if isinstance(row, dict) and row.get("level") is not None
        })
        level_stats = {str(level): _fork_stats_at_level(template, level) for level in levels}
        maximum_level = levels[-1] if levels else 1
        models[name] = {
            "fork_id": fork_id,
            "name": name,
            "type": str(template.get("fork_type_name_zh") or ""),
            "level": maximum_level,
            "mix_level": 1,
            "max_breakthrough": int(template.get("max_breakthrough") or 0),
            "max_star": int(template.get("max_star") or 0),
            "level_sub_stats": level_stats,
            "sub_stats": dict(level_stats.get(str(maximum_level), {})),
            "star_levels": list(template.get("star_levels") or []),
        }
    return models

