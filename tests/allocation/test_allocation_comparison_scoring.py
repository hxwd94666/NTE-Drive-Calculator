# 验证配装变动按本次冻结权重比较且不覆盖历史评分。
from copy import deepcopy
from concurrent.futures import CancelledError
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.optimizer.scoring import ScoringEngine
from src.services.allocation.results.comparison_scoring import (
    FrozenComparisonScorer,
    comparison_display_item,
    comparison_notice,
    persist_comparison_diff,
)
from src.services.equipment.loadouts.weighted_loadout_comparison_service import (
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


def test_warm_native_comparison_freezes_rules_and_quality_without_default_io(monkeypatch):
    engine = ScoringEngine(roles_db={})
    engine.quality_map["Purple"] = .4
    weights, mains = {"攻击力": 1, "暴击率%": 1}, {"暴击率%": .5}
    maximum = engine.max_theoretical_weight(weights)
    expected = engine.calculate_cartridge_score(
        SimpleNamespace(sub_stats={"攻击力": 12}, main_stats="暴击率%", main_value=None, quality="Purple"),
        weights, maximum, mains,
    )

    def unexpected_read(*_args):
        pytest.fail("冻结对比不应重新读取账号或评分规则")

    monkeypatch.setattr(ScoringEngine, "_load_stats", unexpected_read)
    monkeypatch.setattr(ScoringEngine, "_load_roles_from_sqlite", unexpected_read)
    basis = FrozenComparisonScorer.from_engine(
        engine, character_id=1003, weights=weights, main_weights=mains,
        attributes=ATTRIBUTES, dataset_id="fixture",
    )
    before = basis.metadata()
    assert basis.score_inventory_item(item("core", "purple")) == expected
    # 原评分器已持有真实组件客户端；后续草稿变化不得重解释本次预览。
    engine.stat_catalog.gold_base_values["攻击力"] *= 2
    engine.stat_catalog.tape_main_values["暴击率%"] *= 2
    engine.quality_map["Purple"] = .9
    weights.clear()
    mains.clear()
    assert basis.score_inventory_item(item("core", "purple")) == expected
    assert basis.metadata() == before


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


def test_blacklist_zero_weight_matches_public_card_scorer_and_preserves_main():
    basis = scorer(zero_weight_stats=("攻击力",), main_weights={"暴击率%": 1})
    core = item("core")
    expected = basis.engine.calculate_cartridge_score(
        SimpleNamespace(main_stats="暴击率%", sub_stats={"攻击力": 12}, quality="Gold", main_value=None),
        dict(basis.weights), basis.max_weight, dict(basis.main_weights), zero_weight_stats=basis.zero_weight_stats)
    assert basis.score_inventory_item(core) == expected == 50
    snapshot = dict(type="tape", main_stats="暴击率%", sub_stats={"攻击力": 12}, quality="Gold", score=expected)
    assert basis.verify_result_score(snapshot)["score"] == expected


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


@pytest.mark.parametrize("zeros", [(), ("攻击力",)])
def test_batch_old_scores_match_single_and_preserve_unknown_without_mutating_input(zeros):
    basis = scorer(main_weights={"暴击率%": .5}, zero_weight_stats=zeros)
    entries = [(item(kind, quality), main) for kind in ("module", "core")
               for quality in ("orange", "purple", "blue") for main in (None, 10.0)]
    entries += [(item(property_id="unknown"), None), ({"virtual": True}, None)]
    before = deepcopy(entries)
    expected = []
    for raw, value in entries:
        try:
            expected.append(basis.score_inventory_item(raw, main_value=value))
        except (ValueError, TypeError, KeyError):
            expected.append(None)
    client = basis.engine._native_scores._client()
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        assert basis.score_inventory_items(entries) == expected
        dispatch.assert_called_once()
    assert entries == before
    assert expected[-2:] == [None, 0.0]


def test_batch_cancellation_and_component_failure_are_not_hidden_as_unknown():
    basis = scorer()
    client = basis.engine._native_scores._client()

    def cancel():
        raise CancelledError()

    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        with pytest.raises(CancelledError):
            basis.score_inventory_items([(item(), None)], checkpoint=cancel)
        dispatch.assert_not_called()
    error = RuntimeError("component failure")
    with patch.object(basis.engine, "prepare_scores", side_effect=error):
        with pytest.raises(RuntimeError) as caught:
            basis.score_inventory_items([(item(), None)])
    assert caught.value is error


def test_role_without_saved_slots_does_not_prepare_metadata_or_score():
    empty = SimpleNamespace(list_loadout_slots=lambda _: [])
    no_score = SimpleNamespace(metadata=lambda: pytest.fail("无槽位无需准备评分"))
    assert freeze_role_loadout_comparisons(empty, StaticDao(), 1003, no_score) == ()


def test_cross_role_batch_preserves_blacklists_main_values_unknowns_and_profile_identity():
    from src.services.allocation.results.comparison_scoring import (
        freeze_comparison_scorers, score_comparison_batches,
    )
    specs = [dict(character_id=1003 + i, weights={"攻击力": 1, "暴击率%": i / 2},
                  main_weights={"暴击率%": 1 - i / 3},
                  zero_weight_stats=("攻击力",) if i == 1 else ()) for i in range(3)]
    engine = ScoringEngine(roles_db={})
    client = engine._native_scores._client()
    entries = [(item(kind, quality), main) for kind in ("module", "core")
               for quality in ("orange", "purple", "blue") for main in (None, 10.0)]
    entries += [(item(property_id="unknown"), None), ({"virtual": True}, None)]
    expected = [FrozenComparisonScorer.from_engine(
        engine, **spec, attributes=ATTRIBUTES, dataset_id="fixture",
    ).score_inventory_items(entries) for spec in specs]
    before = deepcopy(entries)
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        scorers = freeze_comparison_scorers(engine, specs, attributes=ATTRIBUTES, dataset_id="fixture")
        actual = score_comparison_batches([(basis, entries) for basis in scorers])
    assert actual == expected
    assert dispatch.call_count == 2  # profile/metadata 与旧件矩阵各一次。
    assert len({id(basis.engine) for basis in scorers}) == 1
    assert entries == before
    assert all(row[-2:] == [None, 0.0] for row in actual)
    assert actual[0] != actual[1]


def test_comparison_batch_collects_only_selected_source_uids_once():
    from src.services.allocation.results.comparison_scoring import freeze_comparison_scorers
    from src.services.equipment.loadouts.weighted_loadout_comparison_service import freeze_loadout_comparison_batches
    engine = ScoringEngine(roles_db={})
    specs = [dict(character_id=1003 + i, weights={"攻击力": 1.0 - i / 2}, main_weights=None)
             for i in range(2)]
    scorers = freeze_comparison_scorers(engine, specs, attributes=ATTRIBUTES, dataset_id="fixture")
    dao = UserDao()
    slots = [deepcopy(dao.slots), deepcopy(dao.slots)]
    slots[1][0]["slot_id"] = 10
    slots[1][0]["current_plan"]["assignments"][0]["uid_serial"] = 11
    calls = []

    def inventory(source_id, *, uids):
        calls.append((source_id, set(uids)))
        return [dict(item(), uid_serial=serial) for serial, _slot in uids]

    dao.list_inventory_items = inventory
    before = deepcopy(slots)
    result = freeze_loadout_comparison_batches(dao, StaticDao(), list(zip(scorers, slots)))
    assert calls == [(7, {(10, 1), (11, 1)})]
    assert [rows[0].slot_id for rows in result] == [9, 10]
    assert result[0][0].old_items[0]["score"] == scorers[0].score_inventory_item(item())
    assert result[1][0].old_items[0]["score"] == scorers[1].score_inventory_item(item())
    assert slots == before


def test_comparison_matrix_chunks_large_inputs_without_losing_tail_or_swallowing_cancel():
    from src.services.allocation.results.comparison_scoring import score_comparison_batches
    basis = scorer()
    entries = [(item(), None)] * 4201
    client = basis.engine._native_scores._client()
    expected = basis.score_inventory_item(item())
    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        values, = score_comparison_batches([(basis, entries)])
    assert len(values) == 4201 and values == [expected] * 4201
    assert dispatch.call_count == 2

    def cancelled():
        raise CancelledError("revoked")

    with patch.object(client, "score_equipment", wraps=client.score_equipment) as dispatch:
        with pytest.raises(CancelledError):
            score_comparison_batches([(basis, entries)], checkpoint=cancelled)
        dispatch.assert_not_called()
