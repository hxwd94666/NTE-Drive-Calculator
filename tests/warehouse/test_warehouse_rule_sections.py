# 验证仓库手动区与智能区的来源、稳定身份、排序及复制边界。
from copy import deepcopy
from dataclasses import replace

import pytest

from src.domain.equipment.warehouse.intelligent_rules import attach_origin, automatic_main_supported, generated_rules, fingerprint
from src.domain.equipment.eligibility.management_settings import normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact
from src.domain.equipment.warehouse.warehouse_rule_sections import (
    RuleSection,
    rule_section,
    partition_rules,
    reorder_section,
    copy_rule,
    edit_rule,
    append_manual_rule,
    set_rule_enabled,
    replace_intelligent_rules,
)


def ordinary(identity, *, intelligent=False):
    rule = {
        "rule_id": identity,
        "name": "同名规则",
        "enabled": True,
        "item_type": "tape",
        "main_stats": ["生命值%"],
        "action": "keep",
    }
    return attach_origin(rule, "combo:test") if intelligent else rule


def rules():
    return normalize_warehouse_config(
        {
            "preserve_rules": [
                ordinary("smart", intelligent=True),
                ordinary("first"),
                ordinary("last"),
                {**generated_rules({}, strong_set_name=None)[0], "rule_id": "dynamic"},
            ]
        }
    )["preserve_rules"]


def test_normalized_data_separates_regions_without_changing_each_relative_order():
    value = rules()
    assert [row["rule_id"] for row in value] == ["first", "last", "smart", "dynamic"]
    assert [row["rule_id"] for row in partition_rules(value)[0]] == ["first", "last"]
    assert rule_section({"smart_kind": "future"}) == RuleSection.INTELLIGENT
    for invalid in (None, {}, "not-a-list"):
        with pytest.raises(ValueError, match="规则集必须是列表"):
            normalize_warehouse_config({"preserve_rules": invalid})


def test_reorder_is_section_local_for_pointer_keyboard_and_data_callers():
    value = rules()
    before = deepcopy(value)
    ordered = reorder_section(value, RuleSection.MANUAL, ["last", "first"])
    assert [row["rule_id"] for row in ordered] == ["last", "first", "smart", "dynamic"]
    ordered = reorder_section(value, RuleSection.INTELLIGENT, ["dynamic", "smart"])
    assert [row["rule_id"] for row in ordered] == ["first", "last", "dynamic", "smart"]
    for request in (["first", "smart"], ["first"], ["first", "first"], ["first", "last", "smart"]):
        with pytest.raises(ValueError, match="同一区域"):
            reorder_section(value, RuleSection.MANUAL, request)
    assert value == before


@pytest.mark.parametrize("source", ["first", "smart"])
def test_ordinary_copies_are_new_manual_rules_appended_at_manual_tail(source):
    value = rules()
    before = deepcopy(value)
    copied = copy_rule(value, source, name="副本")
    manual, intelligent = partition_rules(copied)
    assert [row["rule_id"] for row in manual[:2]] == ["first", "last"]
    assert manual[-1]["rule_id"] not in {row["rule_id"] for row in value}
    assert manual[-1]["name"] == "副本" and not manual[-1].get("intelligent")
    assert [row["rule_id"] for row in intelligent] == ["smart", "dynamic"]
    assert value == before


def test_dynamic_copy_remains_intelligent_and_requires_unique_enabled_type():
    value = rules()
    copied = copy_rule(value, "dynamic")
    manual, intelligent = partition_rules(copied)
    duplicate = intelligent[-1]
    assert len(manual) == 2 and duplicate["smart_kind"] == intelligent[-2]["smart_kind"]
    assert duplicate["intelligent"] and duplicate["enabled"] is False
    with pytest.raises(ValueError, match="同类型智能策略"):
        set_rule_enabled(copied, duplicate["rule_id"], True)
    disabled = set_rule_enabled(copied, "dynamic", False)
    enabled = set_rule_enabled(disabled, duplicate["rule_id"], True)
    assert enabled[-1]["enabled"] and not enabled[-2]["enabled"]
    assert [row["rule_id"] for row in replace_intelligent_rules(enabled, [])] == ["first", "last"]


def test_edit_preserves_region_identity_origin_and_clear_includes_edited_disabled_rules():
    value = rules()
    original = next(row for row in value if row["rule_id"] == "smart")
    edited = edit_rule(value, "smart", {**original, "rule_id": "forged", "name": "修改后", "enabled": False})
    smart = next(row for row in edited if row["rule_id"] == "smart")
    assert smart["intelligent"]["edited"] and not smart["enabled"]
    assert [row["rule_id"] for row in replace_intelligent_rules(edited, [])] == ["first", "last"]
    with pytest.raises(ValueError, match="执行类型"):
        edit_rule(value, "dynamic", ordinary("dynamic"))
    with pytest.raises(ValueError, match="来源"):
        edit_rule(value, "first", original)
    with pytest.raises(ValueError, match="手动区"):
        append_manual_rule(value, original)


def test_regeneration_keeps_manual_copies_but_replaces_all_intelligent_entries():
    value = copy_rule(rules(), "smart")
    manual, _ = partition_rules(value)
    generated = generated_rules({}, strong_set_name=None)
    updated = replace_intelligent_rules(value, generated)
    assert partition_rules(updated)[0] == manual
    assert {row["rule_id"] for row in partition_rules(updated)[1]}.isdisjoint(row["rule_id"] for row in value)
    assert len(partition_rules(updated)[1]) == len(generated)
    assert all(row.get("intelligent") for row in partition_rules(updated)[1])


def test_stable_id_does_not_invalidate_automatic_rule_content_fingerprint():
    raw = attach_origin(
        {
            "enabled": True,
            "name": "自动",
            "item_type": "tape",
            "action": "keep",
            "main_stats": [],
            "sub_stats": ["攻击力%"],
            "sub_match": 1,
        },
        "combo:test",
    )
    # 模拟迁移前已有的归一化规则，补入稳定身份不能被识别为编辑并放宽主属性用途。
    normalized = normalize_warehouse_config({"preserve_rules": [raw]})["preserve_rules"][0]
    normalized["intelligent"]["baseline"] = fingerprint({k: v for k, v in normalized.items() if k != "rule_id"})
    fact = WarehouseFact("a", "tape", "Suit1", "TAPE_15", "光属性异能伤害增强%", 15, "Gold", (), (), ())
    assert fingerprint(normalized) == normalized["intelligent"]["baseline"]
    assert not automatic_main_supported(normalized, fact)
    assert automatic_main_supported(normalized, replace(fact, main_stat="暗属性异能伤害增强%"))
