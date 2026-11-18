# 验证套装常驻投影的身份、重复形状和虚拟占位边界。
import pytest

from src.domain.equipment.properties.suit_permanent import project_suit_permanent_properties
from src.domain.equipment.scoring.crit_threshold import minimum_crit_total
from src.services.official_role.panel.attributes import calculate_official_role_attribute_summaries


def test_suit_requires_core_and_unique_required_shapes():
    suits = {"S": {"required_shape_ids": ("A", "B", "C", "D")}}
    rows = {
        ("S", 2): ({"property_id": "AtkUp", "value": 0.1},),
        ("S", 4): ({"property_id": "CritBase", "value": 0.14},),
    }
    items = [
        {"kind": "core", "suit_id": "S", "geometry": "Core"},
        {"kind": "module", "shape_id": "A"},
        {"kind": "module", "suit_id": "S", "shape_id": "B"},
        {"kind": "module", "suit_id": "S", "shape_id": "C"},
        {"kind": "module", "suit_id": "S", "shape_id": "D"},
        {"kind": "module", "suit_id": "S", "shape_id": "D", "virtual": True},
    ]
    result = project_suit_permanent_properties(items, suits, rows)
    assert [(row["required_count"], row["property_id"]) for row in result] == [
        (2, "AtkUp"), (4, "CritBase")
    ]


def test_suit_without_core_does_not_activate():
    suits = {"S": {"required_shape_ids": ("A", "B")}}
    items = [
        {"kind": "module", "suit_id": "S", "shape_id": "A"},
        {"kind": "module", "suit_id": "S", "shape_id": "B"},
    ]
    assert project_suit_permanent_properties(items, suits, {("S", 2): ({"property_id": "AtkUp", "value": .1},)}) == ()


@pytest.mark.parametrize("count", range(5))
def test_crit_floor_uses_candidate_not_planned_four_piece(count):
    role = {"suit_crit_rate": 14.0, "suit_definitions": {"S": {"required_shape_ids": list("ABCD")}},
            "suit_permanent_properties": {"S": {"4": [{"property_id": "CritBase", "value": .14}]}}}
    tape = {"item_type": "tape", "suit_id": "S"}
    drives = [{"item_type": "drive", "shape_id": shape} for shape in "ABCD"[:count]]
    assert minimum_crit_total(role, tape, drives) == (19.0 if count == 4 else 5.0)
    assert minimum_crit_total(role, None, drives) == 5.0
    assert minimum_crit_total(role, {**tape, "virtual": True}, drives) == 5.0


def test_role_panel_consumes_attack_and_defense_suit_rows():
    detail = {
        "attributes": {
            "AtkUp": {"show_percent": True},
            "DefUp": {"show_percent": True},
        },
        "profile": {}, "forks": [], "likeability_bonus": {}, "world_bonus": {},
        "growth_rows": [], "shape_bonus": {},
        "suit_definitions": {"S": {"required_shape_ids": ("A", "B")}},
        "suit_permanent_properties": {"S": {"2": [
            {"property_id": "AtkUp", "value": .1},
            {"property_id": "DefUp", "value": .15},
        ]}},
    }
    rows = calculate_official_role_attribute_summaries(detail, [
        {"kind": "core", "suit_id": "S", "geometry": "Core"},
        {"kind": "module", "geometry": "A", "grid_count": 4, "quality": "Gold", "sub_stats": []},
        {"kind": "module", "suit_id": "S", "geometry": "B", "grid_count": 4,
         "quality": "Gold", "sub_stats": []},
    ])
    values = {row.key: row.value for row in rows["equipment"]}
    assert values["AtkUp"] == .1
    assert values["DefUp"] == .15


def test_two_piece_activates_without_all_four_shapes():
    result = project_suit_permanent_properties(
        [
            {"kind": "core", "suit_id": "S", "geometry": "Core"},
        {"kind": "module", "geometry": "A", "grid_count": 4, "quality": "Gold", "sub_stats": []},
            {"kind": "module", "suit_id": "S", "geometry": "B"},
        ],
        {"S": {"required_shape_ids": ("A", "B", "C", "D")}},
        {("S", 2): ({"property_id": "AtkUp", "value": .1},)},
    )
    assert result[0]["required_count"] == 2


@pytest.mark.parametrize("count", range(5))
def test_real_core_does_not_count_and_suitless_modules_activate_by_distinct_shape(count):
    modules = [{"kind": "module", "geometry": f"EquipmentGeometry_{shape}", "suit_id": None}
               for shape in ("A", "B", "C", "D")[:count]]
    items = [{"kind": "core", "suit_id": "S", "geometry": "Core"}, *modules]
    properties = {"S": {"2": [{"property_id": "AtkUp", "value": .1}],
                        "4": [{"property_id": "CritBase", "value": .14}]}}
    definitions = {"S": {"required_shape_ids": ("A", "B", "C", "D")}}
    expected = [2, 4] if count == 4 else [2] if count >= 2 else []
    assert [row["required_count"] for row in project_suit_permanent_properties(items, definitions, properties)] == expected
    if modules:
        duplicated = [items[0], modules[0], modules[0], {**modules[0], "virtual": True}]
        assert not project_suit_permanent_properties(duplicated, definitions, properties)


def test_panel_and_damage_use_same_suit_totals_and_preserve_original_items():
    from copy import deepcopy
    from src.services.official_role.panel.attributes import _role_panel_damage_inputs

    items = [{"kind": "core", "suit_id": "S", "geometry": "Core", "main_stats": [], "sub_stats": []},
             *[{"kind": "module", "geometry": shape, "grid_count": 1, "quality": "Gold", "sub_stats": []}
               for shape in ("A", "B", "C", "D")]]
    detail = {"profile": {"character_level": 80, "breakthrough_stage": 6},
              "growth_rows": [{"level": 80, "breakthrough_stage": 6, "atk_base": 200, "hp_base": 1000, "def_base": 100}],
              "character": {"element_type": "Element_CHAOS"},
              "suit_definitions": {"S": {"required_shape_ids": ("A", "B", "C", "D")}},
              "suit_permanent_properties": {"S": {"2": [
                  {"property_id": "AtkUp", "value": .1}, {"property_id": "HPMaxUp", "value": .2},
                  {"property_id": "DefUp", "value": .15}], "4": [
                  {"property_id": "DamagePenetrateChaos", "value": .12}]}},
              "equipment_contexts": {"current": {"items": items}}}
    before = deepcopy(items)
    summaries = calculate_official_role_attribute_summaries(detail, iter(items))
    values = {row.key: row.value for row in summaries["character"]}
    assert values["PanelAtk"] == pytest.approx(200 * 1.1 + 84 + 20)
    assert values["PanelHP"] == pytest.approx(1000 * 1.2 + 1120)
    # 未给 world_bonus 时按默认满家具：妖刀攻击 +20（见上）与铠甲防御 +30 均计入面板。
    assert values["PanelDef"] == pytest.approx(100 * 1.15 + 30)
    damage = _role_panel_damage_inputs(detail, "current")[0]
    assert (damage.attack_up, damage.health_up, damage.defense_up) == (.1, .2, .15)
    assert damage.resistance_penetrations == (.12,)
    assert items == before
