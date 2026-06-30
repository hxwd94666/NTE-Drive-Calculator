# 统一计算角色直伤评分和边际收益。
"""Direct-damage model shared by role marginal, replacement, and future theory scoring."""

from __future__ import annotations


ABILITY_DAMAGE_STAT = "异能伤害%"
LEGACY_ABILITY_DAMAGE_STAT = "元素" + "伤害%"


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _crit_rate_cap_fraction(crit_rate_cap: float | None) -> float:
    if crit_rate_cap is None:
        return 1.0
    return max(0.0, min(_as_float(crit_rate_cap, 100.0), 100.0)) / 100.0


def direct_damage_value(
    attack_base: float,
    attack_pct: float,
    attack_flat: float,
    ability_damage: float,
    damage_bonus: float,
    crit_rate_raw: float,
    crit_damage_raw: float,
    crit_rate_cap: float | None = 100.0,
) -> float:
    """Calculate the current coarse direct-damage score from normalized stat values."""
    crit_rate = min(max(crit_rate_raw, 0.0) / 100.0, _crit_rate_cap_fraction(crit_rate_cap))
    crit_damage = crit_damage_raw / 100.0
    attack = attack_base * (1 + attack_pct / 100.0) + attack_flat
    bonus = 1 + (ability_damage + damage_bonus) / 100.0
    crit = 1 + crit_rate * crit_damage
    return attack * bonus * crit


def direct_damage_inputs(total_stats: dict) -> dict[str, float]:
    """Extract the normalized stat inputs used by the direct-damage formula."""
    return {
        "attack_base": _as_float(total_stats.get("攻击力白值")),
        "attack_pct": _as_float(total_stats.get("攻击力%")),
        "attack_flat": _as_float(total_stats.get("攻击力")),
        "ability_damage": _as_float(
            total_stats.get(ABILITY_DAMAGE_STAT, total_stats.get(LEGACY_ABILITY_DAMAGE_STAT, 0.0))
        ),
        "damage_bonus": _as_float(total_stats.get("伤害增加%")),
        "crit_rate_raw": _as_float(total_stats.get("暴击率%")),
        "crit_damage_raw": _as_float(total_stats.get("暴击伤害%")),
    }


def calc_direct_damage(total_stats: dict, crit_rate_cap: float | None = 100.0) -> float:
    """Calculate direct-damage score from a total stat dictionary."""
    return direct_damage_value(**direct_damage_inputs(total_stats), crit_rate_cap=crit_rate_cap)


def _unit(benefit_one: dict, key: str, default: float) -> float:
    value = _as_float(benefit_one.get(key), default)
    return value or default


def calc_direct_marginal_benefits(
    total_stats: dict,
    benefit_one: dict,
    crit_rate_cap: float | None = 100.0,
) -> tuple[float, list[tuple[str, str, str, float]]]:
    """Return current direct score and per-unit marginal gains."""
    units = {
        "攻击力白值": _unit(benefit_one, "攻击力白值", 1.0),
        "攻击力%": _unit(benefit_one, "攻击力%", 1.25),
        "攻击力": _unit(benefit_one, "攻击力", 1.0),
        ABILITY_DAMAGE_STAT: (
            _as_float(benefit_one.get(ABILITY_DAMAGE_STAT))
            or _as_float(benefit_one.get(LEGACY_ABILITY_DAMAGE_STAT))
            or 1.25
        ),
        "伤害增加%": _unit(benefit_one, "伤害增加%", 1.0),
        "暴击率%": _unit(benefit_one, "暴击率%", 1.0),
        "暴击伤害%": _unit(benefit_one, "暴击伤害%", 2.0),
    }
    values = direct_damage_inputs(total_stats)
    base_damage = direct_damage_value(**values, crit_rate_cap=crit_rate_cap)
    if base_damage == 0:
        return 0.0, []

    def gain_with(**updates) -> float:
        next_values = dict(values)
        next_values.update(updates)
        next_damage = direct_damage_value(**next_values, crit_rate_cap=crit_rate_cap)
        return (next_damage / base_damage - 1) * 100

    items = [
        (
            "攻击力白值",
            f"{values['attack_base']:.0f}",
            f"{units['攻击力白值']:.0f}",
            gain_with(attack_base=values["attack_base"] + units["攻击力白值"]),
        ),
        (
            "攻击力%",
            f"{values['attack_pct']:.2f}%",
            f"{units['攻击力%']:.2f}%",
            gain_with(attack_pct=values["attack_pct"] + units["攻击力%"]),
        ),
        (
            "攻击力",
            f"{values['attack_flat']:.0f}",
            f"{units['攻击力']:.0f}",
            gain_with(attack_flat=values["attack_flat"] + units["攻击力"]),
        ),
        (
            ABILITY_DAMAGE_STAT,
            f"{values['ability_damage']:.2f}%",
            f"{units[ABILITY_DAMAGE_STAT]:.2f}%",
            gain_with(ability_damage=values["ability_damage"] + units[ABILITY_DAMAGE_STAT]),
        ),
        (
            "伤害增加%",
            f"{values['damage_bonus']:.2f}%",
            f"{units['伤害增加%']:.2f}%",
            gain_with(damage_bonus=values["damage_bonus"] + units["伤害增加%"]),
        ),
        (
            "暴击率%",
            f"{values['crit_rate_raw']:.2f}%",
            f"{units['暴击率%']:.2f}%",
            gain_with(crit_rate_raw=values["crit_rate_raw"] + units["暴击率%"]),
        ),
        (
            "暴击伤害%",
            f"{values['crit_damage_raw']:.2f}%",
            f"{units['暴击伤害%']:.2f}%",
            gain_with(crit_damage_raw=values["crit_damage_raw"] + units["暴击伤害%"]),
        ),
    ]
    items.sort(key=lambda item: item[3], reverse=True)
    return base_damage, items
