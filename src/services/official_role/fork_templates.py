# 将官方角色和弧盘静态数据投影为可共享的配置模板文件。
"""Generate the shared official role/fork template cache used by role features."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.domain.official_role.advancement import fork_active_panel_stats, fork_breakthrough_choices
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_FORK_PROPERTY_DISPLAY = {
    "AtkBase": ("攻击力白值", 1.0),
    "AtkUp": ("攻击力%", 100.0),
    "ChargeGetEfficiencyBase": ("充能效率%", 100.0),
    "CritBase": ("暴击率%", 100.0),
    "CritDamageBase": ("暴击伤害%", 100.0),
    "DamageUpChaosBase": ("暗属性异能伤害增强%", 100.0),
    "DamageUpCosmosBase": ("光属性异能伤害增强%", 100.0),
    "DamageUpGeneralBase": ("伤害增加%", 100.0),
    "DamageUpIncantationBase": ("咒属性异能伤害增强%", 100.0),
    "DamageUpLakshanaBase": ("相属性异能伤害增强%", 100.0),
    "DamageUpNatureBase": ("灵属性异能伤害增强%", 100.0),
    "DamageUpPsycheBase": ("魂属性异能伤害增强%", 100.0),
    "DamageUpPsychicallyBase": ("心灵伤害增强%", 100.0),
    "DefUp": ("防御力%", 100.0),
    "HPMaxUp": ("生命值%", 100.0),
    "MagBase": ("环合强度", 1.0),
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
            "static_dataset": static_dao.summary()["dataset"],
            "roles": static_dao.list_role_template_characters(),
            "forks": static_dao.list_fork_templates(),
        }


def _fork_stats_at_level(
    template: dict[str, Any], level: int, refinement_level: int | None,
) -> dict[str, float]:
    """Project the legacy template model through the shared stage resolver."""
    stats: dict[str, float] = {}
    choices = fork_breakthrough_choices(template.get("breakthroughs") or (), level)
    highest_stage = max(
        (int(row.get("stage") or 0) for row in choices), default=None,
    )
    for property_id, value in fork_active_panel_stats(
        template,
        level,
        breakthrough_stage=highest_stage,
        refinement_level=refinement_level,
    ).items():
        mapped = _FORK_PROPERTY_DISPLAY.get(property_id)
        if mapped is None:
            continue
        name, scale = mapped
        stats[name] = round(stats.get(name, 0.0) + value * scale, 4)
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
            # No permanent refinement property is a valid, audited result;
            # the normal level/breakthrough panel can still prove a CritBase.
            "permanent_properties_known": template.get("permanent_review_status") in (
                "resolved_permanent", "confirmed_no_permanent", "conditional_only"
            ) or any("暴击率%" in stats for stats in level_stats.values()),
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
