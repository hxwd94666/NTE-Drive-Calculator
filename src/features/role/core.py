"""角色功能模块 - 边际收益与属性计算
纯函数，不依赖 Qt UI，可在其他地方复用。
"""

import re
from typing import List, Tuple, Dict, Any, Optional
from src.utils.logger import logger

from .dao import load_stats, load_role


# ---------------------  边际收益  ---------------------
def calc_marginal_benefits(total_stats: dict) -> tuple:
    """
    计算边际收益

    Args:
        total_stats: 汇总属性字典（由 get_character_total_stats 返回）

    Returns:
        tuple: (base_damage, items)
            base_damage: 直伤评分
            items: [(参数名, 当前值字符串, 单位价值字符串, 收益百分比数值), ...]
                   已按收益数值从大到小排序
    """
    stats = load_stats()
    benefit_one = stats.get("benefit_one", {})

    # 单位价值，缺失时默认值
    unit_a_base = benefit_one.get("攻击力白值", 1.0) or 1.0
    unit_a_pct = benefit_one.get("攻击力%", 1.25) or 1.25
    unit_a_flat = benefit_one.get("攻击力", 1.0) or 1.0
    unit_elem = benefit_one.get("元素伤害%", 1.25) or 1.25
    unit_dmg = benefit_one.get("伤害增加%", 1.0) or 1.0
    unit_cr = benefit_one.get("暴击率%", 1.0) or 1.0
    unit_cd = benefit_one.get("暴击伤害%", 2.0) or 2.0

    a_base = total_stats.get("攻击力白值", 0.0)
    a_pct = total_stats.get("攻击力%", 0.0)
    a_flat = total_stats.get("攻击力", 0.0)
    elem = total_stats.get("元素伤害%", 0.0)
    dmg = total_stats.get("伤害增加%", 0.0)
    cr_raw = total_stats.get("暴击率%", 0.0)
    cd_raw = total_stats.get("暴击伤害%", 0.0)

    cr = min(cr_raw / 100.0, 1.0)
    cd = cd_raw / 100.0

    def damage(base, pct, flat, elem_val, dmg_val, crit_rate, crit_dmg):
        return (base * (1 + pct / 100.0) + flat) * (1 + (elem_val + dmg_val) / 100.0) * (1 + crit_rate * crit_dmg)

    base_damage = damage(a_base, a_pct, a_flat, elem, dmg, cr, cd)
    if base_damage == 0:
        return 0.0, []

    items = []

    # 攻击力白值
    step = unit_a_base
    d = damage(a_base + step, a_pct, a_flat, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力白值", f"{a_base:.0f}", f"{step:.0f}", gain))

    # 攻击力%
    step = unit_a_pct
    d = damage(a_base, a_pct + step, a_flat, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力%", f"{a_pct:.2f}%", f"{step:.2f}%", gain))

    # 攻击力
    step = unit_a_flat
    d = damage(a_base, a_pct, a_flat + step, elem, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("攻击力", f"{a_flat:.0f}", f"{step:.0f}", gain))

    # 元素伤害%
    step = unit_elem
    d = damage(a_base, a_pct, a_flat, elem + step, dmg, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("元素伤害%", f"{elem:.2f}%", f"{step:.2f}%", gain))

    # 伤害增加%
    step = unit_dmg
    d = damage(a_base, a_pct, a_flat, elem, dmg + step, cr, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("伤害增加%", f"{dmg:.2f}%", f"{step:.2f}%", gain))

    # 暴击率%
    step = unit_cr
    cr_new = min((cr_raw + step) / 100.0, 1.0)
    d = damage(a_base, a_pct, a_flat, elem, dmg, cr_new, cd)
    gain = (d / base_damage - 1) * 100
    items.append(("暴击率%", f"{cr_raw:.2f}%", f"{step:.2f}%", gain))

    # 暴击伤害%
    step = unit_cd
    cd_new = (cd_raw + step) / 100.0
    d = damage(a_base, a_pct, a_flat, elem, dmg, cr, cd_new)
    gain = (d / base_damage - 1) * 100
    items.append(("暴击伤害%", f"{cd_raw:.2f}%", f"{step:.2f}%", gain))

    # 按收益降序排序
    items.sort(key=lambda x: x[3], reverse=True)
    return base_damage, items


def filter_margins_by_weights(margins: list, weights: dict) -> list:
    """
    根据权重词条过滤边际收益列表

    Args:
        margins: calc_marginal_benefits 返回的 items 列表
        weights: 角色权重字典

    Returns:
        list: 过滤后的边际收益列表
    """
    if not margins or not weights:
        return margins

    stats_config = load_stats()
    alias_map = stats_config.get("benefit_alias_mapping", {})

    allowed_categories = set()
    for weight_key in weights.keys():
        canonical = alias_map.get(weight_key, weight_key)
        allowed_categories.add(canonical)

    return [m for m in margins if m[0] in allowed_categories]


def apply_margins_to_weights(weights: dict, margins: list, alias_map: dict) -> int:
    """
    将边际收益的 gain 值覆盖到对应的权重词条

    Args:
        weights: 权重字典（会被原地修改）
        margins: 边际收益列表
        alias_map: 别名映射字典

    Returns:
        int: 更新的词条数量
    """
    # 建立反向映射：规范名 -> 所有权重键列表
    reverse_map = {}
    for wk in weights.keys():
        canonical = alias_map.get(wk, wk)
        reverse_map.setdefault(canonical, []).append(wk)

    updated = 0
    for name, cur_val, unit_val, gain in margins:
        if name in reverse_map:
            for wk in reverse_map[name]:
                weights[wk] = round(gain, 4)
                updated += 1

    return updated


# ---------------------  驱动  ---------------------
def calc_drive_bonus_stats(role_data: dict) -> List[Tuple[str, float]]:
    """
    计算角色驱动的汇总属性（包含形状基础加成）
    返回 [(词条名, 数值), ...]

    Args:
        role_data: 角色数据字典（来自 my_roles.json）

    Returns:
        List[Tuple[str, float]]: 汇总属性列表
    """
    drive = role_data.get("drive", {})
    drives = drive.get("drives", [])

    enriched_drives = enrich_drives_with_shape_bonus(drives)
    result = aggregate_drive_stats(enriched_drives)
    extra_buffs = calc_extra_buffs_from_role_data(enriched_drives, role_data)
    for k, v in extra_buffs.items():
        result[k] = result.get(k, 0.0) + v

    return sorted(result.items(), key=lambda x: x[0])


def enrich_drives_with_shape_bonus(drives: list) -> list:
    """
    为每个驱动补充形状加成的攻击力和生命值到 sub_stats 中
    同时确保 main_stats 字段存在且有效

    Args:
        drives: 驱动列表（原始数据）

    Returns:
        list: 处理后的驱动列表（每个驱动都包含完整的 main_stats 和 sub_stats）
    """
    result = []
    for d in drives:
        d = dict(d)  # 不污染原数据

        # 1. main_stats给他覆盖了
        d["main_stats"] = {}

        # 2. 确保 sub_stats 存在
        if "sub_stats" not in d or not isinstance(d["sub_stats"], dict):
            d["sub_stats"] = {}

        # 3. 计算形状加成（攻击力、生命值）
        shape_id = str(d.get("shape_id", ""))
        nums = re.findall(r"\d+", shape_id)
        shape_num = int(nums[0]) if nums else 0
        shape_attack = shape_num * 21
        shape_hp = shape_num * 280

        # 4. 将形状加成添加到 main_stats 中
        d["main_stats"]["攻击力"] = d["main_stats"].get("攻击力", 0) + shape_attack
        d["main_stats"]["生命值"] = d["main_stats"].get("生命值", 0) + shape_hp

        result.append(d)
    return result


def aggregate_drive_stats(drives: list) -> dict:
    """
    汇总驱动列表中的所有属性（main_stats + sub_stats）
    调用前请确保 drives 已通过 enrich_drives_with_shape_bonus 处理
    """
    result = {}
    for d in drives:
        for stats in (d.get("main_stats", {}), d.get("sub_stats", {})):
            for k, v in stats.items():
                result[k] = result.get(k, 0.0) + float(v)
    return result


def calc_extra_buffs_from_role_data(drives: list, role_data: dict) -> dict:
    """
    从角色数据中计算额外形状加成

    Args:
        drives: 驱动列表
        role_name: 角色名

    Returns:
        dict: 额外形状加成字典，如 {"攻击力%": 20.0}，若无加成则返回空字典
    """
    extra_buffs = role_data.get("extra_shape_buffs", {})
    extra_shape_label = role_data.get("extra_shape_label", "")

    if not extra_buffs or not extra_shape_label:
        return {}

    # 提取目标数字
    m = re.search(r"(\d+)", extra_shape_label)
    if not m:
        return {}
    target_num = int(m.group(1))

    # 统计匹配的驱动数量
    matched_count = 0
    for drive in drives:
        shape_id = drive.get("shape_id", "")
        nums = re.findall(r"\d+", shape_id)
        if nums:
            drive_num = int(nums[0])
            if drive_num == target_num:
                matched_count += 1

    if matched_count == 0:
        return {}

    # 计算加成
    result = {}
    for stat, value in extra_buffs.items():
        result[stat] = float(value) * matched_count
    return result


# ---------------------  空幕  ---------------------


# ---------------------  武器  ---------------------


# ---------------------  人物其他  ---------------------
def get_character_total_stats(role_data: dict) -> dict:
    """
    获取角色所有来源的汇总属性（基础 + 驱动 + 武器 + 空幕）

    Args:
        role_data: 角色数据字典（来自 my_roles.json）

    Returns:
        dict: 规范化后的属性字典（键名已统一映射）
    """
    stats = load_stats()
    benefit_map = stats.get("benefit_alias_mapping", {})
    alias_map = stats.get("stat_alias_mapping", {})

    total = {}

    def add_stat(key, value):
        if value is None:
            return
        try:
            v = float(value)
        except (ValueError, TypeError):
            return
        canonical = benefit_map.get(key)
        if canonical is None:
            canonical = alias_map.get(key, key)
        total[canonical] = total.get(canonical, 0.0) + v

    # 1. 基础 sub_stats
    for k, v in role_data.get("sub_stats", {}).items():
        add_stat(k, v)

    # 2. 驱动汇总
    drive_rows = calc_drive_bonus_stats(role_data)
    for k, v in drive_rows:
        add_stat(k, v)

    # 3. 武器
    weapon = role_data.get("weapon", {})
    for k, v in weapon.get("sub_stats", {}).items():
        add_stat(k, v)
    w_skill = weapon.get("skill", {})
    for k, v in w_skill.get("sub_stats", {}).items():
        add_stat(k, v)
    w_cover = float(w_skill.get("skill_cover", 0.0))
    for k, v in w_skill.get("skill", {}).items():
        add_stat(k, float(v) * w_cover)

    # 4. 空幕
    tape = role_data.get("tape", {})
    t_cover = float(tape.get("skill_cover", 0.0))

    for k, v in tape.get("main_stats", {}).items():
        add_stat(k, float(v))
    for k, v in tape.get("sub_stats", {}).items():
        add_stat(k, float(v))
    for k, v in tape.get("skill", {}).items():
        add_stat(k, float(v))
    for k, v in tape.get("skill_2", {}).items():
        add_stat(k, float(v) * t_cover)

    return total
