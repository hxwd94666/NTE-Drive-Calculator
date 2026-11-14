# 验证同批采集目录完整导出新增属性，旧翻译不覆盖新单位，异常不发布半成品。
from copy import deepcopy
import json

import pytest

from tools.game_data.export_native_sync_catalogs import export_catalogs

NTE_TEST_TIER = "core"
ATTRIBUTE_TABLE = "DataTable/PackData/ModifyData/DT_AttributeStaticData.json"
NEW_ATTRIBUTE = "DamagePenetrateChaos"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def fixture(tmp_path):
    source, resources, output = (tmp_path / name for name in ("Content", "resources", "candidate"))
    attributes = {
        "AtkAdd": {"AttributeName": "AtkAdd", "AttributeText": {"LocalizedString": "攻击力"}, "bShowPercent": False},
        NEW_ATTRIBUTE: {"AttributeName": NEW_ATTRIBUTE, "AttributeText": {"SourceString": "暗属性异能抗性穿透"}, "bShowPercent": True},
    }
    matrix = [["-1"] * 7 for _ in range(7)]
    matrix[1][1:3] = ["drive", "0"]
    equipment = {}
    for identity, name, core in (("drive", "驱动", False), ("core", "卡带", True)):
        equipment[identity] = {
            "ItemName": {"LocalizedString": name}, "ItemQuality": "ITEM_QUALITY_BLUE",
            "ElementData": {"IsCore": core, "EquipmentGeometryType": "EquipmentGeometry_Core" if core else "EquipmentGeometry_Hen2",
                            "OwnGridNum": 2, "SuitPackID": "Suit1", "MaxStrengthenLevel": 20,
                            "RandomBaseCount": 1, "RandomAttributeMaxCount": 4},
        }
    tables = {
        "DataTable/Character/DT_Character.json": {
            "1003": {"ItemName": {"LocalizedString": "角色"}, "ElementData": {"CharacterElementType": "incantation"}},
        },
        "DataTable/Character/DT_CharacterAbilityConfig.json": {"1003": {"CharacterAbilityList": ["Skill"]}},
        "DataTable/Equipment/DT_Equipment.json": equipment,
        "DataTable/Equipment/CT_EquipmentBaseAttribute.json": {"AtkAdd_curve": {"Keys": [{"Time": 0, "Value": 1}]}},
        ATTRIBUTE_TABLE: attributes,
        "DataTable/Equipment/DT_EquipmentShapeFeatureData.json": {"EquipmentGeometry_Hen2": {"Shape": [{"X": 0, "Y": 0}, {"X": 0, "Y": 1}]}},
        "DataTable/Equipment/DT_EquipmentPlanData.json": {
            "1003": {"EquipmentSlots": [",".join(row) for row in matrix], "EquipmentList": ["drive"], "CoreID": "core"},
        },
        "DataTable/Equipment/DT_EquipmentSuitData.json": {"Suit1": {}},
    }
    for name, rows in tables.items():
        write_json(source / name, [{"Rows": rows}])
    write_json(resources / "characters/characters.json", {"characters": {"1003": {"name_en": "Role"}}})
    old = {
        "items": {key: {"name_zh": row["ItemName"]["LocalizedString"], "name_en": key, "name_ja": key} for key, row in equipment.items()},
        "attributes": {"AtkAdd": {"name_zh": "既有显示", "name_en": "ATK", "name_ja": "攻撃力", "percent": True}},
        "suits": {"Suit1": {"name_zh": "套装"}}, "shapes": {}, "plans": {}, "curves": {},
    }
    write_json(resources / "equipment/equipment.json", old)
    return source, resources, output, attributes, old


def test_new_attributes_and_source_units_are_exported_without_mutating_old_catalog(tmp_path):
    source, resources, output, attributes, old = fixture(tmp_path)
    old["attributes"]["AtkAdd"]["name_en"] = "AtkAdd"
    write_json(resources / "equipment/equipment.json", old)
    before = (resources / "equipment/equipment.json").read_bytes()
    report = export_catalogs(source, resources, output)
    actual = json.loads((output / "equipment/equipment.json").read_bytes())["attributes"]
    assert set(actual) == set(attributes)
    assert actual[NEW_ATTRIBUTE]["name_zh"] == "暗属性异能抗性穿透"
    assert actual[NEW_ATTRIBUTE]["percent"] is True
    # 没有可靠外语译文时展示正式 ID，保持消费端要求的非空字段，不冒充官方翻译。
    assert actual[NEW_ATTRIBUTE]["name_en"] == actual[NEW_ATTRIBUTE]["name_ja"] == NEW_ATTRIBUTE
    assert actual["AtkAdd"] == {**old["attributes"]["AtkAdd"], "percent": False}
    assert report["attributes"] == report["source_attributes"] == 2
    assert report["added_attributes"] == [NEW_ATTRIBUTE]
    assert report["attribute_translation_fallbacks"] == [NEW_ATTRIBUTE]
    assert (resources / "equipment/equipment.json").read_bytes() == before


@pytest.mark.parametrize("identity", ["AtkAdd", NEW_ATTRIBUTE])
def test_conflicting_official_property_identity_does_not_publish(tmp_path, identity):
    source, resources, output, attributes, _old = fixture(tmp_path)
    attributes[identity]["AttributeName"] = "OtherIdentity"
    write_json(source / ATTRIBUTE_TABLE, [{"Rows": attributes}])
    with pytest.raises(ValueError, match="属性正式身份"):
        export_catalogs(source, resources, output)
    assert not output.exists()


@pytest.mark.parametrize("value", [None, 0, "false"])
def test_unknown_percent_unit_preserves_previous_candidate(tmp_path, value):
    source, resources, output, attributes, _old = fixture(tmp_path)
    attributes[NEW_ATTRIBUTE]["bShowPercent"] = value
    write_json(source / ATTRIBUTE_TABLE, [{"Rows": attributes}])
    old_output = {"equipment/equipment.json": b"previous equipment", "characters/characters.json": b"previous characters"}
    for name, data in old_output.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    with pytest.raises(ValueError, match="属性百分比单位"):
        export_catalogs(source, resources, output)
    assert all((output / name).read_bytes() == data for name, data in old_output.items())


def test_new_property_without_official_display_name_is_not_invented(tmp_path):
    source, resources, output, attributes, _old = fixture(tmp_path)
    attributes[NEW_ATTRIBUTE].pop("AttributeText")
    write_json(source / ATTRIBUTE_TABLE, [{"Rows": attributes}])
    with pytest.raises(ValueError, match="新增属性缺少正式中文"):
        export_catalogs(source, resources, output)
    assert not output.exists()


def test_missing_previous_identity_still_requires_review(tmp_path):
    source, resources, output, attributes, _old = fixture(tmp_path)
    attributes = deepcopy(attributes)
    attributes.pop("AtkAdd")
    write_json(source / ATTRIBUTE_TABLE, [{"Rows": attributes}])
    with pytest.raises(ValueError, match="属性正式身份"):
        export_catalogs(source, resources, output)
    assert not output.exists()
