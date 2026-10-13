# 验证同权重对比的原子保存、旧评分保护和槽位基线防漂移。
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.allocation.slot_comparisons import refresh_legacy_slot_comparisons
from src.services.equipment.weighted_loadout_comparison_service import (
    freeze_role_loadout_comparisons,
    refresh_weighted_loadout_comparisons,
)
from src.services.allocation.weighted_save import save_weighted_preview_records
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.allocation.test_allocation_comparison_scoring import StaticDao, UserDao, item, scorer
from tests.allocation.static_release_helpers import clone_test_release
from src.domain.allocation.inputs import StaticDatasetReference
from tests.equipment.test_role_loadout_slots import assignment, inventory_snapshot


@pytest.fixture
def database(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="comparison-test") as dao:
        yield dao


def saved_slot(dao, character_id=1003, name="主力"):
    source_id = dao.import_inventory_snapshot(inventory_snapshot())
    slot_id = dao.create_loadout_slot(character_id, name, slot_key="primary")
    plan_id = dao.save_plan_to_slot(
        slot_id, name="历史", assignments=[assignment()], source_snapshot_id=source_id, score=40,
        payload={"assignment_scores": {"nte-module-22-11": 40}},
    )
    return slot_id, plan_id, source_id


def row(slot_id, plan_id, source_id, *, score=55, character_id=1003):
    diff = dict(comparison_version=1, score_basis="calculation_weights", changed=False,
                added=[], removed=[], baseline_slot_id=slot_id, baseline_plan_id=plan_id,
                baseline_snapshot_id=source_id)
    return dict(slot_id=slot_id, character_id=character_id, name="新方案", status="ready", score=score,
                assignments=[assignment()], source_snapshot_id=source_id, comparison_baseline=diff,
                payload={"last_diff": diff, "assignment_scores": {"nte-module-22-11": score}})


def test_same_uids_save_new_score_keep_old_history_and_no_diff(database):
    slot_id, old_id, source_id = saved_slot(database)
    old = deepcopy(database.get_loadout_plan(old_id))
    new_ids = database.save_calculated_loadout_plans([row(slot_id, old_id, source_id)], checkpoint=lambda: None)
    current = database.get_loadout_slot(slot_id)["current_plan"]
    assert current["plan_id"] == new_ids[0]
    assert current["score"] == 55
    assert current["payload"]["assignment_scores"]["nte-module-22-11"] == 55
    assert not current["payload"]["last_diff"]["changed"]
    assert database.get_loadout_plan(old_id)["score"] == old["score"]
    assert database.get_loadout_plan(old_id)["payload"] == old["payload"]


@pytest.mark.parametrize("entry", ["calculated", "slots", "active", "direct"])
def test_changed_slot_rejected_inside_transaction_without_overwriting(database, entry):
    slot_id, old_id, source_id = saved_slot(database)
    incoming = row(slot_id, old_id, source_id)
    newer_id = database.save_plan_to_slot(slot_id, name="另一操作", assignments=[assignment()],
                                          source_snapshot_id=source_id, score=70)
    with pytest.raises(UserDataValidationError, match="已改变"):
        if entry == "calculated":
            database.save_calculated_loadout_plans([incoming], checkpoint=lambda: None)
        elif entry == "slots":
            database.save_plans_to_slots([incoming])
        elif entry == "active":
            database.replace_active_loadout_plans([incoming])
        else:
            database.save_plan_to_slot(
                slot_id, name="新方案", assignments=[assignment()], source_snapshot_id=source_id,
                payload={"source_role_name": "角色名"},
                comparison_baseline=incoming["comparison_baseline"],
            )
    assert database.get_loadout_slot(slot_id)["current_plan_id"] == newer_id
    assert not database._db().in_transaction
    assert entry != "direct" or database.get_loadout_slot(slot_id)["slot_name"] == "主力"


def test_cancelled_commit_preserves_original_slot(database):
    from concurrent.futures import CancelledError
    slot_id, old_id, source_id = saved_slot(database)
    calls = []

    def checkpoint():
        calls.append(1)
        if len(calls) == 3:
            raise CancelledError()

    with pytest.raises(CancelledError):
        database.save_calculated_loadout_plans([row(slot_id, old_id, source_id)], checkpoint=checkpoint)
    assert database.get_loadout_slot(slot_id)["current_plan_id"] == old_id
    assert database.get_loadout_plan(old_id)["score"] == 40


def test_other_slot_and_another_account_are_untouched(database, tmp_path):
    slot_id, old_id, source_id = saved_slot(database)
    second = database.create_loadout_slot(1003, "备用")
    second_id = database.save_plan_to_slot(second, name="备用", assignments=[assignment()],
                                           source_snapshot_id=source_id, score=30)
    with UserDataDao(tmp_path / "another.sqlite3", account_id="another") as another:
        other_slot, other_plan, _ = saved_slot(another)
        database.save_calculated_loadout_plans([row(slot_id, old_id, source_id)], checkpoint=lambda: None)
        assert database.get_loadout_slot(second)["current_plan_id"] == second_id
        assert another.get_loadout_slot(other_slot)["current_plan_id"] == other_plan


def test_mixed_weight_native_score_is_not_presented_as_unified():
    basis = scorer()
    rows = freeze_role_loadout_comparisons(UserDao(), StaticDao(), 1003, basis)
    new_item = {"uid": "nte-module-2-20", "area": 2, "quality": "Gold", "sub_stats": {"攻击力": 12},
                "shape_id": "H_2", "role_scores": {"测试": 999}}
    plans = {"测试": {"valid": True, "assigned_set_drives": [new_item]}}
    updated = refresh_legacy_slot_comparisons({"测试": rows}, plans)["测试"][0]
    added = updated.diff["added"][0]
    assert "score" not in added
    assert added["comparison_score_unavailable"]


def test_changed_drives_compare_at_one_weight_and_retain_freeze_on_refresh():
    basis = scorer()
    frozen = freeze_role_loadout_comparisons(UserDao(), StaticDao(), 1003, basis)
    expected = basis.score_inventory_item(item())
    candidate = SimpleNamespace(uid=(2, 20), kind="module", quality="orange", grid_count=2,
                                geometry="EquipmentGeometry_Hen2", sub_stats=(SimpleNamespace(
                                    property_id="AtkAdd", value=12, percent=False),), main_stats=())
    option = SimpleNamespace(character_id=1003, assignments=(SimpleNamespace(
        uid=(2, 20), kind="module", score=expected, grid_count=2, geometry="EquipmentGeometry_Hen2"),))
    context = SimpleNamespace(candidates=(candidate,), attributes=(SimpleNamespace(property_id="AtkAdd", scoring_name="攻击力"),))
    updated = refresh_weighted_loadout_comparisons({1003: frozen}, context, (option,))[1003][0]
    assert updated.diff["removed"][0]["score"] == expected
    assert updated.diff["added"][0]["score"] == expected
    assert updated.diff["baseline_plan_id"] == 8
    # Pure weight-score differences on an unchanged UID never become equipment swaps.
    kept_assignment = SimpleNamespace(
        uid=(1, 10), kind="module", score=expected, grid_count=2, geometry="EquipmentGeometry_Hen2")
    unchanged = SimpleNamespace(character_id=1003, assignments=(kept_assignment,))
    assert not refresh_weighted_loadout_comparisons({1003: frozen}, context, (unchanged,))[1003][0].diff["changed"]


def test_weighted_save_persists_selected_frozen_diff_not_live_weights(tmp_path):
    basis = scorer()
    baselines = freeze_role_loadout_comparisons(UserDao(), StaticDao(), 1003, basis)
    captured = []

    class Dao:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def profile(self):
            return {"account_id": "comparison-test"}

        def save_plans_to_slots(self, records, *, checkpoint, validate, commit_context):
            checkpoint()
            validate()
            with commit_context():
                captured.extend(records)
            return (10,)

    class Bridge:
        def __init__(self, *_args):
            pass

        def prepare_role_plan(self, **arguments):
            return SimpleNamespace(as_record=lambda: dict(arguments))

    score = basis.score_inventory_item(item())
    assignment_row = SimpleNamespace(uid=(2, 20), kind="module", geometry="EquipmentGeometry_Hen2",
                                     grid_count=2, virtual=False, item_id="fixture", suit_id=None, score=score)
    option = SimpleNamespace(character_id=1003, score=score, assignments=(assignment_row,), generated_board=())
    context = SimpleNamespace(candidates=(SimpleNamespace(
        uid=(2, 20), quality="orange", sub_stats=(SimpleNamespace(property_id="AtkAdd", value=12, percent=False),),
    ),), attributes=(SimpleNamespace(property_id="AtkAdd", scoring_name="攻击力"),))
    frozen = refresh_weighted_loadout_comparisons({1003: baselines}, context, (option,))[1003]
    static_path, release = clone_test_release(tmp_path)
    dataset = StaticDatasetReference(release.schema_version, release.dataset_id,
                                     release.importer_version, release.built_at_utc)
    context.static_dataset = dataset
    context.account_id = "comparison-test"
    context.snapshot = SimpleNamespace(snapshot_id=8)
    preview = SimpleNamespace(
        account_id="comparison-test", ownership=SimpleNamespace(identity=SimpleNamespace(
            account_id="comparison-test", user_database_path=(tmp_path / "user.sqlite3").resolve())),
        lock_snapshot=SimpleNamespace(inventory_snapshot_id=8),
        result=SimpleNamespace(unified=SimpleNamespace(selected=(option,), strategy="role_priority"),
                               snapshot_id=8, profile_id=1, profile_version=1, solver_version="fixture"),
        context=context, loadout_comparisons={1003: frozen},
        slot_bindings=(), explicit_slots=False,
        static_release=release, static_dataset=dataset,
        user_database_path=tmp_path / "user.sqlite3", static_database_path=tmp_path / "static.sqlite3",
    )
    with patch("src.services.allocation.weighted_save.UserDataDao", return_value=Dao()), \
            patch("src.services.allocation.weighted_save.SavedStateLoadoutBridge", Bridge), \
            patch("src.services.allocation.weighted_save.verify_allocation_lock_snapshot"):
        assert save_weighted_preview_records(preview, checkpoint=lambda: None, commit_context=nullcontext, slot_ids_by_character={1003: 9}) == (10,)
    assert captured[0]["payload"]["last_diff"]["baseline_plan_id"] == 8
    assert captured[0]["payload"]["last_diff"]["scoring"] == frozen[0].diff["scoring"]
    assert captured[0]["comparison_baseline"]["baseline_slot_id"] == 9
    assert captured[0]["payload"]["last_diff"]["added"][0]["score"] == score
    assert captured[0]["payload"]["last_diff"]["removed"][0]["score"] == score
    assert captured[0]["payload"]["assignment_scores"] == {"nte-module-2-20": score}
