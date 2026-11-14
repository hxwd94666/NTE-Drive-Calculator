# 定义账号内三种匹配作用域及两套管理配置，迁移只转换冻结纯值。
from copy import deepcopy
from enum import StrEnum
from hashlib import sha256
import json

from src.domain.inventory.post_actions import merge_post_action_config
from src.domain.equipment.warehouse.intelligent_rules import attach_origin
from src.domain.equipment.warehouse.warehouse_rule_sections import partition_rules


class MatchingScope(StrEnum):
    IDENTIFICATION = "identification"
    SCAN = "scan_management"
    WAREHOUSE = "warehouse_management"

    @property
    def setting_key(self):
        return {
            self.IDENTIFICATION: "identification_role_suits",
            self.SCAN: "scan_management_role_suits",
            self.WAREHOUSE: "warehouse_management_role_suits",
        }[self]

    @property
    def management_key(self):
        return {self.SCAN: "full_scan_post_action_config", self.WAREHOUSE: "warehouse_management_config"}.get(self)


WAREHOUSE_CONFIG_VERSION = 1


def normalize_warehouse_config(raw=None):
    """公共字段只复用既有归一化，不继承扫描开关或扫描的规则仲裁。"""
    if raw is not None and not isinstance(raw, dict):
        raise ValueError("仓库管理配置必须是对象")
    raw = deepcopy(raw or {})
    if not isinstance(raw.get("preserve_rules", []), list):
        raise ValueError("仓库规则集必须是列表")
    version = raw.pop("version", WAREHOUSE_CONFIG_VERSION)
    if type(version) is not int or version != WAREHOUSE_CONFIG_VERSION:
        raise ValueError("仓库管理配置版本不受支持")
    result = merge_post_action_config(raw)
    result.pop("server_region", None)
    result["discard"].pop("discard_unmatched", None)
    result["version"] = WAREHOUSE_CONFIG_VERSION
    rules = []
    sources = (
        raw.get("preserve_rules", [])
        + merge_post_action_config({"custom_keep": raw.get("custom_keep")})["preserve_rules"]
    )
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError("仓库规则必须是对象")
        normalized = merge_post_action_config({"preserve_rules": [source]})["preserve_rules"]
        if len(normalized) != 1:
            raise ValueError("仓库规则条件无效")
        identity = source.get("rule_id")
        if identity is None:
            identity = (
                "rule:"
                + sha256(
                    json.dumps([index, source], sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
                ).hexdigest()[:32]
            )
        if not isinstance(identity, str) or not identity or len(identity) > 96:
            raise ValueError("仓库规则身份无效")
        rules.append({**normalized[0], "rule_id": identity})
    if len({rule["rule_id"] for rule in rules}) != len(rules):
        raise ValueError("仓库规则身份重复")
    # 规范化后的数据也分区，防止其他调用者以混排隐式改变执行类型。
    manual, intelligent = partition_rules(rules)
    result["preserve_rules"] = manual + intelligent
    # 没有智能规则就不跳过评分；有规则时才恢复显式选择，旧字段缺失默认开启。
    result["skip_scoring"] = bool(intelligent) and (
        raw["skip_scoring"] is True if "skip_scoring" in raw else True
    )
    return result


def normalize_scan_management_config(raw):
    """扫描只保留原可执行静态条件及其顺序，不携带仓库动态策略。"""
    scan = merge_post_action_config(raw)
    ordinary = []
    for rule in scan["preserve_rules"]:
        if not rule.get("smart_kind"):
            frozen = deepcopy(rule)
            frozen.pop("intelligent", None)
            ordinary.append(frozen)
    scan["preserve_rules"] = ordinary
    scan["recommendation"] = {}
    return scan


def split_legacy_management_config(raw):
    """扫描保留所有原可执行静态规则及顺序；动态规则只保留于仓库。"""
    scan = normalize_scan_management_config(raw)
    warehouse = normalize_warehouse_config(raw)
    for index, rule in enumerate(warehouse["preserve_rules"]):
        if rule.get("smart_kind") and not rule.get("intelligent"):
            # 旧动态副本虽曾失去来源标签，其能力并未变为逐件词条判断。
            warehouse["preserve_rules"][index] = attach_origin(rule, f"migrated_dynamic:{index}")
    return scan, warehouse
