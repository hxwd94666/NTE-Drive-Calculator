# 从同批解包导出采集目录；完整重建属性、数值和图纸，旧目录只提供已核对展示资源。
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def export_catalogs(source: Path, resources: Path, output: Path) -> dict:
    sources = {}

    def table(name):
        path = source / name
        payload = read(path)
        rows = payload[0]["Rows"]
        if not isinstance(rows, dict) or not rows:
            raise ValueError(f"必要表为空：{name}")
        sources[name] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return rows

    characters = table("DataTable/Character/DT_Character.json")
    abilities = table("DataTable/Character/DT_CharacterAbilityConfig.json")
    equipment = table("DataTable/Equipment/DT_Equipment.json")
    curves = table("DataTable/Equipment/CT_EquipmentBaseAttribute.json")
    attributes = table("DataTable/PackData/ModifyData/DT_AttributeStaticData.json")
    shapes = table("DataTable/Equipment/DT_EquipmentShapeFeatureData.json")
    plans = table("DataTable/Equipment/DT_EquipmentPlanData.json")
    suits = table("DataTable/Equipment/DT_EquipmentSuitData.json")
    old_characters = read(resources / "characters/characters.json")["characters"]
    result = deepcopy(read(resources / "equipment/equipment.json"))

    def text(value):
        return value.get("LocalizedString") or value.get("SourceString") if isinstance(value, dict) else None

    # 目录只扩展有正式图纸、姓名及技能配置的角色；NPC/新变身行不据表行数自动加入。
    selected = set(old_characters) & set(characters)
    for key in plans:
        if key not in characters or not text(characters[key].get("ItemName")) or not abilities.get(key, {}).get("CharacterAbilityList"):
            raise ValueError(f"正式角色图纸缺少身份或技能证据：{key}")
        selected.add(key)
    names = {}
    for key in sorted(selected, key=int):
        row = characters[key]
        official = text(row.get("ItemName"))
        if not official:
            raise ValueError(f"角色缺少官方名称：{key}")
        value = deepcopy(old_characters.get(key, {}))
        value["name_zh"] = official
        value["verified"] = True
        value["source"] = {**sources["DataTable/Character/DT_Character.json"], "row": key,
                           "element_type": row["ElementData"]["CharacterElementType"]}
        names[key] = value

    # 旧语言文字和图标仅是显示元数据。所有同步消费的数值及关系均由本批来源重新生成。
    items = {}
    for key, row in equipment.items():
        if key not in result["items"] or text(row["ItemName"]) != result["items"][key]["name_zh"]:
            raise ValueError(f"装备展示身份变化，需另行核对语言/图标：{key}")
        value = deepcopy(result["items"][key])
        element = row["ElementData"]
        core = element["IsCore"]
        value.update(kind="core" if core else "module", quality=row["ItemQuality"].split("_")[-1].lower(),
                     geometry=element["EquipmentGeometryType"].split("EquipmentGeometry_")[-1],
                     grid=None if core else int(element["OwnGridNum"]), suit=element["SuitPackID"] if core else None,
                     max_level=int(element["MaxStrengthenLevel"]), main_count=int(element["RandomBaseCount"]),
                     sub_count=int(element["RandomAttributeMaxCount"]))
        items[key] = value
    result["items"] = items
    old_attributes = result["attributes"]
    missing = set(old_attributes) - attributes.keys()
    if missing:
        raise ValueError(f"属性正式身份变化，需核对删除或改名：{sorted(missing)}")
    exported_attributes = {}
    for key, row in attributes.items():
        # 旧显示可能带“加成”等单位后缀；属性身份只按正式属性键核对，不靠文字相等。
        if row.get("AttributeName") not in (key, "None"):
            raise ValueError(f"属性正式身份变化：{key}")
        percent = row.get("bShowPercent")
        if type(percent) is not bool:
            raise ValueError(f"属性百分比单位缺失或无效：{key}")
        value = deepcopy(old_attributes.get(key, {}))
        if key not in old_attributes:
            official = text(row.get("AttributeText"))
            if not isinstance(official, str) or not official.strip():
                raise ValueError(f"新增属性缺少正式中文名称：{key}")
            # 新外语译文缺少证据时显式显示正式 ID，满足消费端非空约束，不编造翻译。
            value.update(name_zh=official, name_en=key, name_ja=key)
        value["percent"] = percent
        exported_attributes[key] = value
    result["attributes"] = exported_attributes
    if set(suits) != set(result["suits"]):
        raise ValueError("套装身份变化，需要成套更新展示数据")
    result["curves"] = {key: [[float(p["Time"]), float(p["Value"])] for p in row["Keys"]] for key, row in curves.items()}
    result["shapes"] = {key.removeprefix("EquipmentGeometry_"): {"cells": [
        {"row": int(cell["X"]), "column": int(cell["Y"])} for cell in row["Shape"]]} for key, row in shapes.items()}
    normalized = {}
    for key, row in plans.items():
        valid, recommended = [], []
        matrix = [line.split(",") for line in row["EquipmentSlots"]]
        if len(matrix) != 7 or any(len(line) != 7 for line in matrix):
            raise ValueError(f"图纸不是正式 7×7 带边框结构：{key}")
        for y, line in enumerate(matrix):
            for x, item in enumerate(line):
                if item == "-1":
                    continue
                if not 1 <= y <= 5 or not 1 <= x <= 5:
                    raise ValueError(f"图纸有效格越过内部区域：{key}")
                valid.append({"row": y, "column": x})
                if item != "0":
                    if item not in items or items[item]["kind"] != "module":
                        raise ValueError(f"图纸引用未知驱动：{key}/{item}")
                    recommended.append({"item_id": item, "row": y, "column": x})
        if sorted(p["item_id"] for p in recommended) != sorted(row["EquipmentList"]):
            raise ValueError(f"图纸网格与装备清单不一致：{key}")
        occupied = set()
        valid_set = {(p["row"], p["column"]) for p in valid}
        for item in recommended:
            cells = result["shapes"][items[item["item_id"]]["geometry"]]["cells"]
            for cell in cells:
                target = (item["row"] + cell["row"], item["column"] + cell["column"])
                if target not in valid_set or target in occupied:
                    raise ValueError(f"正式推荐图纸重叠或越界：{key}")
                occupied.add(target)
        if row["CoreID"] not in items or items[row["CoreID"]]["kind"] != "core":
            raise ValueError(f"图纸引用未知核心：{key}")
        normalized[key] = {"valid_cells": valid, "recommended_core": row["CoreID"], "recommended_modules": recommended}
    result["plans"] = normalized
    result["sources"] = sources
    result["display_metadata"] = "Existing ID-bound translations/icons retained after identity checks; all numeric data and layouts rebuilt from the declared source batch"
    write(output / "characters/characters.json", {"characters": names})
    write(output / "equipment/equipment.json", result)
    report = {"characters": len(names), "plans": len(normalized), "items": len(items),
              "attributes": len(exported_attributes), "source_attributes": len(attributes),
              "added_attributes": sorted(set(attributes) - set(old_attributes)),
              # 只登记本次新增的兜底；旧译文与正式 ID 相同不自动视为缺翻译。
              "attribute_translation_fallbacks": sorted(set(attributes) - set(old_attributes)),
              "added_characters": sorted(set(names) - set(old_characters)), "sources": sources,
              "output_sha256": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                                for name in ("characters/characters.json", "equipment/equipment.json")}}
    write(output / "catalog-build.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.resources.resolve()):
        raise ValueError("先导出到隔离候选，不直接覆盖私库资源")
    print(json.dumps(export_catalogs(args.source, args.resources, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
