# 计算角色属性汇总、边际收益和装备加成。
"""角色功能模块 - 边际收益与属性计算
纯函数，不依赖 Qt UI，可在其他地方复用。
"""

import re
from typing import List, Tuple, Dict, Any, Optional
from src.utils.logger import logger

from .dao import load_stats
from .damage_model import (
    ABILITY_DAMAGE_STAT,
    LEGACY_ABILITY_DAMAGE_STAT,
    calc_direct_damage,
    calc_direct_marginal_benefits,
)


def is_empty_drive(drive: dict) -> bool:
    """判断驱动是否为空驱动（无有效属性）"""
    if drive.get("uid", "").startswith("empty_"):
        return True
    if not drive.get("sub_stats") or len(drive.get("sub_stats", {})) == 0:
        return True
    if not drive.get("shape_id"):
        return True
    return False


def get_valid_drives(drives: list) -> list:
    """获取有效驱动列表（排除空驱动）"""
    filtered = [d for d in drives if not is_empty_drive(d)]
    return filtered


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
    return calc_direct_marginal_benefits(total_stats, benefit_one)


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
    role_data: 角色数据字典（来自当前账号 SQLite 快照）

    Returns:
        List[Tuple[str, float]]: 汇总属性列表
    """
    drive = role_data.get("drive", {})
    drives = get_valid_drives(drive.get("drives", []))

    enriched_drives = enrich_drives_with_shape_bonus(drives)
    result = aggregate_drive_stats(enriched_drives)
    extra_buffs = calc_extra_buffs_from_role_data(enriched_drives, role_data)
    for k, v in extra_buffs.items():
        result[k] = result.get(k, 0.0) + v

    return sorted(result.items(), key=lambda x: x[0])


def calc_tape_bonus_stats(role_data: dict) -> List[Tuple[str, float]]:
    """计算卡带本体的主词条和副词条加成。"""
    tape = role_data.get("tape", {})
    if not isinstance(tape, dict):
        return []
    result = {}
    for stats in (tape.get("main_stats", {}), tape.get("sub_stats", {})):
        if not isinstance(stats, dict):
            continue
        for k, v in stats.items():
            result[k] = result.get(k, 0.0) + float(v)
    return sorted(result.items(), key=lambda x: x[0])


def calc_equipment_bonus_stats(role_data: dict) -> List[Tuple[str, float]]:
    """计算驱动和卡带本体的汇总属性，不包含套装技能。"""
    result = {}
    for stat, value in calc_drive_bonus_stats(role_data) + calc_tape_bonus_stats(role_data):
        result[stat] = result.get(stat, 0.0) + float(value)
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
    role_data: 角色数据字典（来自当前账号 SQLite 快照）

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

    # 3. 卡带本体
    for k, v in calc_tape_bonus_stats(role_data):
        add_stat(k, v)

    # 4. 武器
    weapon = role_data.get("weapon", {})
    # 基础加成
    for k, v in weapon.get("sub_stats", {}).items():
        add_stat(k, v)

    # 技能效果（新格式：数组）
    skill_effects = weapon.get("skill", [])
    for effect in skill_effects:
        key = effect.get("key")
        if not key:
            continue
        value = float(effect.get("value", 0.0))
        cover = float(effect.get("cover", 0.8))
        num = float(effect.get("num", 1))
        effect_total = value * cover * num
        if effect_total != 0:
            add_stat(key, effect_total)

    # 5. 套装技能
    set_bonus = role_data.get("set_bonus", {})
    t_cover = float(set_bonus.get("skill_cover", 0.0))
    for k, v in set_bonus.get("skill", {}).items():
        add_stat(k, float(v))
    for k, v in set_bonus.get("skill_2", {}).items():
        add_stat(k, float(v) * t_cover)

    return total


def calc_base_damage(total_stats: dict) -> float:
    """根据汇总属性计算直伤评分（伤害值）"""
    return calc_direct_damage(total_stats)
