# 验证配装变动按本次冻结权重比较且不覆盖历史评分。
from copy import deepcopy

import pytest

from src.optimizer.scoring import ScoringEngine
from src.services.allocation.comparison_scoring import (
    FrozenComparisonScorer, comparison_display_item, comparison_notice, persist_comparison_diff,
)
from src.services.equipment.weighted_loadout_comparison_service import (
    freeze_role_loadout_comparisons,
    select_frozen_comparison,
)


ATTRIBUTES = [
    {"attribute_id": "AtkAdd", "display_name_zh": "攻击力"},
    {"attribute_id": "CritBase", "display_name_zh": "暴击率", "show_percent": True},
    {"attribute_id": "CritDamageBase", "display_name_zh": "暴击伤害", "show_percent": True},
]


def scorer(weights=None, main_weights=None, **options):
    engine = ScoringEngine(roles_db={})
    return FrozenComparisonScorer.from_engine(
        engine, character_id=1003, weights=weights or {"攻击力": 1, "暴击率%": 1},
        main_weights=main_weights, attributes=ATTRIBUTES, dataset_id="fixture", **options,
    )


def item(kind="module", quality="orange", property_id="AtkAdd"):
    return dict(kind=kind, uid_slot=1, uid_serial=10, quality=quality,
                geometry="Hen2", grid_count=2, sub_stats=[dict(property_id=property_id, value=12)],
                main_stats=[dict(property_id="CritBase", value=.2, percent=True)] if kind == "core" else [])


class StaticDao:
    def list_suits(self):
        return []

    def list_shapes(self):
        return [dict(shape_id="EquipmentGeometry_Hen2", legacy_shape_id="H_2")]


class UserDao:
    def __init__(self, source=None):
        self.source = item() if source is None else source
        self.slots = [dict(slot_id=9, slot_key="primary", slot_name="主力", character_id=1003,
                           current_plan_id=8, current_plan=dict(
                               plan_id=8, source_snapshot_id=7,
                               payload={"assignment_scores": {"nte-module-1-10": 999}},
                               assignments=[dict(kind="module", uid_slot=1, uid_serial=10)]))]

    def list_loadout_slots(self, character_id):
        assert character_id == 1003
        return self.slots

    def list_inventory_items(self, snapshot_id, *, uids):
        assert snapshot_id == 7
        return [self.source] if self.source else []


def test_old_equipment_uses_new_weights_without_mutating_saved_scores():
    dao = UserDao()
    history = deepcopy(dao.slots)
    basis = scorer()
    rows = freeze_role_loadout_comparisons(dao, StaticDao(), 1003, basis)
    assert rows[0].old_items[0]["score"] == basis.score_inventory_item(item())
    assert rows[0].old_items[0]["score"] != 999
    assert dao.slots == history


def test_scoring_inputs_are_copied_and_crit_damage_is_not_crit_rate():
    weights = {"攻击力": 1, "暴击率%": 1, "暴击伤害%": 0}
    basis = scorer(weights)
    expected = basis.score_inventory_item(item())
    weights["攻击力"] = 0
    assert basis.score_inventory_item(item()) == expected
    assert basis.score_inventory_item(item(property_id="CritDamageBase")) == 0
    assert basis.metadata()["property_weights"]["CritBase"] == 1
    assert basis.metadata()["property_weights"]["CritDamageBase"] == 0


@pytest.mark.parametrize("quality,coefficient", [("orange", 1), ("purple", .8), ("blue", .6)])
def test_core_main_weight_and_quality_are_shared(quality, coefficient):
    basis = scorer(main_weights={"暴击率%": .5})
    gold = item("core")
    value = basis.engine.stat_catalog.tape_main_values["暴击率%"]
    assert basis.score_inventory_item(item("core", quality), main_value=value * coefficient) == pytest.approx(
        basis.score_inventory_item(gold, main_value=value) * coefficient, abs=.02,
    )


def test_blacklist_zero_weight_matches_public_drive_scorer():
    basis = scorer(zero_weight_stats=("攻击力",))
    assert basis.score_inventory_item(item()) == 0


def test_missing_old_item_preserves_uid_without_fake_zero_score():
    dao = UserDao()
    dao.source = {}
    row = freeze_role_loadout_comparisons(dao, StaticDao(), 1003, scorer())[0]
    assert row.old_items[0]["uid"] == "nte-module-1-10"
    assert "score" not in row.old_items[0]
    assert row.old_items[0]["comparison_score_unavailable"]


def test_unknown_property_is_not_scored_as_zero():
    with pytest.raises(ValueError):
        scorer().score_inventory_item(item(property_id="unknown"))


def test_diff_metadata_survives_json_projection_and_legacy_notice_is_distinct():
    diff = dict(changed=True, added_uids={"b"}, added=[], removed=[],
                comparison_version=1, score_basis="calculation_weights",
                scoring=scorer().metadata(), baseline_plan_id=8)
    stored = persist_comparison_diff(diff)
    assert stored["baseline_plan_id"] == 8
    assert stored["scoring"] == diff["scoring"]
    assert stored["added_uids"] == ["b"]
    assert "本次计算权重" in comparison_notice(stored)
    assert "历史变动" in comparison_notice({"changed": True})


def test_selection_does_not_switch_to_another_slot_or_latest_plan():
    row = freeze_role_loadout_comparisons(UserDao(), StaticDao(), 1003, scorer())[0]
    diff = select_frozen_comparison((row,), 9)
    assert diff["baseline_plan_id"] == 8
    with pytest.raises(RuntimeError):
        select_frozen_comparison((row,), 99)
    assert not select_frozen_comparison((), None)["changed"]


def test_empty_slot_is_frozen_without_removed_equipment():
    dao = UserDao()
    dao.slots[0].update(current_plan=None, current_plan_id=None)
    row = freeze_role_loadout_comparisons(dao, StaticDao(), 1003, scorer())[0]
    assert row.old_items == ()
    assert row.diff["baseline_plan_id"] is None


def test_virtual_placeholder_score_is_zero_not_unknown():
    assert scorer().score_inventory_item({"virtual": True}) == 0


def test_saved_core_main_value_overrides_catalogue_quality_fallback():
    basis = scorer(main_weights={"暴击率%": .5})
    gold_value = basis.engine.stat_catalog.tape_main_values["暴击率%"]
    assert basis.score_inventory_item(item("core"), main_value=gold_value / 2) < basis.score_inventory_item(item("core"))


def test_persistence_is_a_copy_not_a_live_weight_reference():
    diff = dict(changed=False, scoring=scorer().metadata(), added=[], removed=[])
    stored = persist_comparison_diff(diff)
    diff["scoring"]["property_weights"]["AtkAdd"] = 100
    assert stored["scoring"]["property_weights"]["AtkAdd"] != 100


def test_missing_historical_display_score_is_not_replaced_by_live_weights():
    raw = {"uid": "nte-module-1-10", "sub_stats": {"攻击力": 12}}
    display = comparison_display_item(raw, {"changed": True})
    assert display["comparison_score_unavailable"]
    assert "comparison_score_unavailable" not in raw
    known = comparison_display_item({**raw, "score": 0}, {"changed": True})
    assert "comparison_score_unavailable" not in known
