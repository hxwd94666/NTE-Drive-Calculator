# 验证理论词条复用Calc评分目录与逐件静态约束，不把角色权重当伤害目标或再等解包随机池。
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.domain.allocation.scoring_rules import AllocationScoringRules
from src.optimizer.scoring import ScoringEngine
from src.services.allocation.inputs.context_builder import read_allocation_scoring_rules
from src.services.teams.axis_graduation.rules import freeze_theory_equipment_rules
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit


NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[3]


def frozen_rules(*, scoring_rules=None, quality=None, static=None):
    source = scoring_rules or read_allocation_scoring_rules(ROOT / "config/stats.json")
    engine = ScoringEngine(roles_db={}, stat_catalog=source.to_stat_catalog())
    result = freeze_theory_equipment_rules(
        scoring_rules=source, quality_coefficients=quality or engine.quality_map,
        static_database_path=static or ROOT / "data/game_static.sqlite3",
    )
    return result, source, engine


def test_reference_values_and_strict_pools_come_from_existing_calc_catalog():
    rules, source, _ = frozen_rules()
    catalog = source.to_stat_catalog()
    drive = {stat.property_id: stat for stat in rules.drive_sub_stats}
    core = {stat.property_id: stat for stat in rules.core_sub_stats}
    main = {stat.property_id: stat for stat in rules.core_main_stats}
    assert drive["CritBase"].value == catalog.gold_base_values["暴击率%"] / 100
    assert core["CritBase"].value == catalog.tape_stat_values["暴击率%"] / 100
    assert main["CritBase"].value == catalog.tape_main_values["暴击率%"] / 100
    assert "HealUp" in main and "HealUp" not in core
    assert "DamageUpCosmosBase" in main and "DamageUpCosmosBase" not in core
    assert len(core) > 4 and len(drive) > 4  # 不按旧角色权重只留四个“必选”属性。
    assert rules.main_sub_overlap_allowed
    assert rules.scoring_source_sha256 == source.source_sha256


def test_quality_and_grid_reference_values_are_frozen_without_invoking_the_score_client():
    rules, _, engine = frozen_rules()
    frozen = rules.sha256
    assert dict(rules.quality_coefficients) == engine.quality_map
    engine.quality_map["Gold"] = .01
    assert dict(rules.quality_coefficients)["Gold"] == 1 and rules.sha256 == frozen
    item = next(row for row in rules.items if row.kind == "module" and row.quality == "Gold" and row.grid_count == 2)
    intrinsic = {stat.property_id: stat.value for stat in item.intrinsic_main_stats}
    from src.domain.equipment.properties.equipment_normalizer import calculate_drive_main_stats
    expected = calculate_drive_main_stats(2, "Gold")
    assert intrinsic == {"AtkAdd": expected["攻击力"], "HPMaxAdd": expected["生命值"]}


def test_each_theoretical_item_keeps_its_formal_identity_shape_and_substat_count():
    rules, _, _ = frozen_rules()
    with static_catalog_read_unit(ROOT / "data/game_static.sqlite3") as unit:
        source = {row["item_id"]: row for row in unit.dao.list_equipment_items()}
    assert rules.items
    for item in rules.items:
        row = source[item.item_id]
        assert item.sub_stat_count == row["random_sub_attribute_max_count"]
        assert item.max_level == row["max_level"]
        assert item.geometry_id == row["geometry_id"] and item.suit_id == row["suit_id"]
        assert item.item_id and not hasattr(item, "uid_serial")


def test_unknown_property_or_conflicting_alias_does_not_silently_shrink_the_domain():
    original = json.loads((ROOT / "config/stats.json").read_text("utf-8-sig"))
    for label, value in (("unmapped-stat", 1), ("暴击率", 999)):
        data = deepcopy(original)
        data["gold_base_values"][label] = value
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        source = AllocationScoringRules.from_data(data, source_sha256=hashlib.sha256(encoded).hexdigest())
        with pytest.raises(ValueError, match="属性|冲突"):
            frozen_rules(scoring_rules=source)


def test_reference_catalog_cannot_supply_theoretical_battle_rules():
    with pytest.raises(ValueError, match="主静态|图鉴"):
        frozen_rules(static=ROOT / "data/role_catalog/game_static.sqlite3")
