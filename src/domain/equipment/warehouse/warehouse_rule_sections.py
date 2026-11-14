# 仓库两区规则草稿的纯值操作；排序不改变类型，普通副本只追加手动区末尾。
from copy import deepcopy
from enum import StrEnum
from uuid import uuid4

from src.domain.equipment.warehouse.intelligent_rules import attach_origin, preserve_origin


class RuleSection(StrEnum):
    MANUAL = "manual"
    INTELLIGENT = "intelligent"


def rule_section(rule):
    # 动态能力与来源分开判断，缺来源的旧动态副本也不能被当作手动普通规则。
    return RuleSection.INTELLIGENT if rule.get("intelligent") or rule.get("smart_kind") else RuleSection.MANUAL


def partition_rules(rules):
    manual, intelligent = [], []
    for rule in rules:
        (manual if rule_section(rule) == RuleSection.MANUAL else intelligent).append(deepcopy(rule))
    return manual, intelligent


def _find(rules, rule_id):
    found = [rule for rule in rules if rule.get("rule_id") == rule_id]
    if len(found) != 1:
        raise ValueError("规则身份缺失或重复，请重新打开管理设置")
    return found[0]


def _new_identity(rules):
    known = {rule.get("rule_id") for rule in rules}
    identity = "rule:" + uuid4().hex
    while identity in known:
        identity = "rule:" + uuid4().hex
    return identity


def reorder_section(rules, section, ordered_ids):
    """只接受本区完整排列；拖放、键盘及其他调用均不能借重排跨区移动。"""
    section = RuleSection(section)
    manual, intelligent = partition_rules(rules)
    selected = manual if section == RuleSection.MANUAL else intelligent
    identities = [rule.get("rule_id") for rule in selected]
    requested = list(ordered_ids)
    if (
        any(not isinstance(identity, str) or not identity for identity in requested + identities)
        or len(set(identities)) != len(identities)
        or len(requested) != len(identities)
        or set(requested) != set(identities)
    ):
        raise ValueError("只能调整同一区域内的规则顺序")
    by_id = {rule["rule_id"]: rule for rule in selected}
    ordered = [by_id[identity] for identity in requested]
    return ordered + intelligent if section == RuleSection.MANUAL else manual + ordered


def append_manual_rule(rules, rule):
    if rule_section(rule) != RuleSection.MANUAL:
        raise ValueError("手动区只接受普通手动规则")
    manual, intelligent = partition_rules(rules)
    manual.append({**deepcopy(rule), "rule_id": _new_identity(rules)})
    return manual + intelligent


def edit_rule(rules, rule_id, edited):
    original = _find(rules, rule_id)
    # 编辑可修改条件和动作，但不得把来源或动态能力变成另一区的执行类型。
    if edited.get("smart_kind") != original.get("smart_kind"):
        raise ValueError("编辑不能改变规则执行类型")
    if not original.get("intelligent") and edited.get("intelligent"):
        raise ValueError("编辑不能改变规则来源")
    updated = preserve_origin(original, edited)
    updated["rule_id"] = rule_id
    if rule_section(updated) != rule_section(original):
        raise ValueError("编辑不能改变规则所属区域")
    return [updated if row.get("rule_id") == rule_id else deepcopy(row) for row in rules]


def copy_rule(rules, rule_id, *, name=None):
    original = _find(rules, rule_id)
    duplicate = deepcopy(original)
    duplicate["rule_id"] = _new_identity(rules)
    if name is not None:
        duplicate["name"] = name
    manual, intelligent = partition_rules(rules)
    if duplicate.get("smart_kind"):
        # 动态库存策略不能降格为逐件条件，副本默认关闭，避免同类策略多条启用。
        duplicate["enabled"] = False
        duplicate = (
            preserve_origin(original, duplicate)
            if duplicate.get("intelligent")
            else attach_origin(duplicate, "copied_dynamic")
        )
        intelligent.append(duplicate)
    else:
        duplicate.pop("intelligent", None)
        manual.append(duplicate)
    return manual + intelligent


def set_rule_enabled(rules, rule_id, enabled):
    original = _find(rules, rule_id)
    kind = original.get("smart_kind")
    if (
        enabled
        and kind
        and any(
            row.get("rule_id") != rule_id and row.get("smart_kind") == kind and row.get("enabled", True)
            for row in rules
        )
    ):
        raise ValueError("同类型智能策略只能启用一条")
    return edit_rule(rules, rule_id, {**original, "enabled": bool(enabled)})


def replace_intelligent_rules(rules, generated):
    """清空或重新生成均移除全部智能条目；手动区及其顺序完全保留。"""
    manual, _ = partition_rules(rules)
    result = manual
    for source in generated:
        if rule_section(source) != RuleSection.INTELLIGENT:
            raise ValueError("生成结果必须保留智能来源或动态类型")
        result.append({**deepcopy(source), "rule_id": _new_identity(result)})
    return result
