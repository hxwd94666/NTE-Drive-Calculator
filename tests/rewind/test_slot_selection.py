# 验证倒带仅消费所选槽位并隔离备用方案缺项。
from contextlib import nullcontext
from copy import deepcopy

import pytest

from src.domain.rewind_loadout import RewindSlotReference, default_slot, read_slot_preferences, inspect_slot, shape_key
from src.services.rewind.recommendation import RewindShapeRecommendationService


SHAPES = [{"shape_id": "EquipmentGeometry_Hen4", "cell_count": 4,
           "cells": [{"x": 0, "y": n} for n in range(4)]}]

SHAPE_MAP = {shape_key(row["shape_id"]): row for row in SHAPES}


def slot_fixture(slot_id=1, score=10, *, virtual=False, missing=False):
    items, assignments, scores = {}, [], {}
    for row in range(1, 6):
        uid = (row, slot_id)
        assignment = dict(kind="module", uid_slot=row, uid_serial=slot_id,
                          target_row=row, target_column=1, rotation=0)
        assignments.append(assignment)
        items[uid] = dict(kind="module", uid_slot=row, uid_serial=slot_id,
                          geometry="Hen4", grid_count=4)
        scores[f"nte-module-{row}-{slot_id}"] = score
    if virtual:
        assignments[0]["raw_assignment"] = {"virtual": True}
        assignments[0]["uid_slot"] = 0
    if missing:
        items.pop((1, slot_id))
    slot = dict(slot_id=slot_id, character_id=1004, slot_key="primary" if slot_id == 1 else "second",
                slot_name="主力" if slot_id == 1 else "备用", sort_order=slot_id, is_archived=False)
    plan = dict(plan_id=slot_id + 10, character_id=1004, source_snapshot_id=3,
                assignments=assignments, score=score * 5,
                payload={"schema": "allocation-official-snapshot-v1", "assignment_scores": scores})
    slot["current_plan"] = plan
    return slot, plan, items


@pytest.mark.parametrize("kind,expected", [("virtual", "incomplete"), ("missing", "source_missing")])
def test_missing_slot_states_are_distinct(kind, expected):
    slot, plan, items = slot_fixture(**{kind: True})
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == expected


def test_missing_assignment_without_placeholder_is_not_a_partial_success():
    slot, plan, items = slot_fixture()
    plan["assignments"].pop()
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "incomplete"


def test_overlap_is_not_complete_even_when_areas_add_up():
    slot, plan, items = slot_fixture()
    plan["assignments"][1]["target_row"] = 1
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "layout_invalid"


def test_no_cartridge_and_unknown_total_score_do_not_reject_complete_drives():
    slot, plan, items = slot_fixture()
    plan.update(status="incomplete", score=None)
    row = inspect_slot(slot, plan, items, SHAPE_MAP)
    assert row.state == "ready"
    assert row.score is None
    assert len(row.drives) == 5


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), None])
def test_missing_or_invalid_score_is_not_zero(bad):
    slot, plan, items = slot_fixture()
    plan["payload"]["assignment_scores"]["nte-module-1-1"] = bad
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "score_missing"


def test_real_zero_is_a_valid_saved_score():
    slot, plan, items = slot_fixture(score=0)
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "ready"


def test_default_prefers_usable_score_and_never_replaces_explicit_invalid_choice():
    first = inspect_slot(*slot_fixture(), SHAPE_MAP)
    second = inspect_slot(*slot_fixture(2, 20, virtual=True), SHAPE_MAP)
    assert default_slot((first, second), None) == first.reference
    assert default_slot((first, second), 2) == second.reference
    assert default_slot((first, second), 99) == RewindSlotReference(1004, 99, None)


def test_preference_ids_are_strict_and_unknown_versions_do_not_guess():
    assert read_slot_preferences({"slot_selection_version": 1, "selected_slots": {"1004": 2}}) == {1004: 2}
    assert read_slot_preferences({"slot_selection_version": 1, "selected_slots": {"1004": True}}) == {1004: None}
    assert read_slot_preferences({"slot_selection_version": 1, "selected_slots": {"1004": "2"}}) == {1004: None}
    assert read_slot_preferences({"slot_selection_version": 9, "selected_slots": {"1004": 2}}) == {1004: None}


class StaticDao:
    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def list_shapes(self):
        return deepcopy(SHAPES)

    def list_characters(self):
        return [{"character_id": 1004, "name_zh": "测试"}]

    def get_character_graduation_template(self, *_):
        return None

    def summary(self):
        return {"dataset": {"dataset_id": "fixture"}}


class UserDao(StaticDao):
    def __init__(self, *_):
        self.slots = [slot_fixture()[0], slot_fixture(2, virtual=True)[0]]
        self.items = slot_fixture()[2]
        self.queries = []

    def read_consistent_state(self):
        return nullcontext()

    def list_visible_loadout_slots_with_plans(self):
        return self.slots

    def get_loadout_slot(self, slot_id):
        return next((row for row in self.slots if row["slot_id"] == slot_id), None)

    def list_custom_characters(self):
        return []

    def list_inventory_items(self, snapshot_id, *, kind=None, uids=None):
        self.queries.append((snapshot_id, uids))
        return [item for item in self.items.values() if uids is None or (item["uid_serial"], item["uid_slot"]) in uids]

    def current_inventory_snapshot_id(self):
        return 9

    def inventory_snapshot_summary(self, *_):
        return {"source": "nte_core", "complete": True, "declared_item_count": 5, "stored_item_count": 5}


def service_fixture(tmp_path):
    path = tmp_path / "user.sqlite3"
    path.touch()
    (tmp_path / "static.sqlite3").touch()
    dao = UserDao()
    return RewindShapeRecommendationService(user_database_path=path,
        static_database_path=tmp_path / "static.sqlite3", user_dao_factory=lambda *_: dao,
        static_dao_factory=StaticDao), dao


def test_recommendation_only_counts_selected_slot_and_reads_its_source(tmp_path):
    service, dao = service_fixture(tmp_path)
    result = service.analyze_for_targets(target_character_ids=(1004,),
                                         selected_slots=(RewindSlotReference(1004, 1, 11),))
    assert result.required_count == 5
    assert result.selected_source_snapshots == ((1, 3),)
    assert result.selected_slots[0].slot_id == 1
    assert any(snapshot == 3 for snapshot, _ in dao.queries)
    assert not any(uids and any(serial == 2 for serial, _ in uids) for _, uids in dao.queries)


def test_selecting_incomplete_slot_reports_that_slot(tmp_path):
    service, _ = service_fixture(tmp_path)
    with pytest.raises(ValueError, match="备用.*未装满"):
        service.analyze_for_targets(target_character_ids=(1004,),
                                   selected_slots=(RewindSlotReference(1004, 2, 12),))


def test_changed_plan_and_cross_role_slot_are_rejected(tmp_path):
    service, _ = service_fixture(tmp_path)
    for ref in (RewindSlotReference(1004, 1, 99), RewindSlotReference(9999, 1, 11)):
        with pytest.raises(ValueError):
            service.analyze_for_targets(target_character_ids=(ref.character_id,), selected_slots=(ref,))


def test_unused_target_does_not_interrupt_focused_strategy(tmp_path):
    service, _ = service_fixture(tmp_path)
    result = service.analyze_for_targets(target_character_ids=(9999, 1004), strategy="focused",
        primary_character_ids=(1004,), selected_slots=(RewindSlotReference(1004, 1, 11),))
    assert result.required_count == 5


def test_missing_selection_never_expands_all_slots(tmp_path):
    service, _ = service_fixture(tmp_path)
    with pytest.raises(ValueError, match="选择配装槽位"):
        service.analyze_for_targets(target_character_ids=(1004,), selected_slots=())


@pytest.mark.parametrize("schema,source", [
    ("allocation-official-snapshot-v1", "weighted_allocation"),
    ("saved-state-official-loadout-v1", "equipment_page"),
    ("game-observed-loadout-v1", "game_inventory"),
])
def test_production_save_schemas_accept_full_drives_without_a_cartridge(schema, source):
    slot, plan, items = slot_fixture()
    plan["payload"].update(schema=schema, source=source)
    plan["status"] = "incomplete"
    plan["assignments"].append({"kind": "core", "uid_slot": 0, "uid_serial": 9,
                                "raw_assignment": {"virtual": True}})
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "ready"


@pytest.mark.parametrize("problem", ["unknown_shape", "out_of_bounds", "missing_anchor", "wrong_area", "mask"])
def test_layout_evidence_cannot_be_skipped(problem):
    slot, plan, items = slot_fixture()
    if problem == "unknown_shape":
        items[(1, 1)]["geometry"] = "Unknown"
    elif problem == "out_of_bounds":
        plan["assignments"][0]["target_column"] = 4
    elif problem == "missing_anchor":
        plan["assignments"][0].pop("target_row")
    elif problem == "wrong_area":
        items[(1, 1)]["grid_count"] = 3
    else:
        plan["payload"]["drive_board_cells"] = [(1, 1)]
    assert inspect_slot(slot, plan, items, SHAPE_MAP).state == "layout_invalid"


def test_orientation_offsets_are_preserved_instead_of_rotating_or_normalizing():
    slot, plan, items = slot_fixture()
    shapes = deepcopy(SHAPES)
    shapes[0]["cells"] = [{"x": 0, "y": n - 3} for n in range(4)]
    for row in plan["assignments"]:
        row["target_column"] = 4
    assert inspect_slot(slot, plan, items, {shape_key(row["shape_id"]): row for row in shapes}).state == "ready"


def test_switching_complete_slots_changes_only_that_roles_shortfalls(tmp_path):
    service, dao = service_fixture(tmp_path)
    slot, _, items = slot_fixture(2, score=30)
    dao.slots[1] = slot
    dao.items.update(items)
    first = service.analyze_for_targets(target_character_ids=(1004,),
                                       selected_slots=(RewindSlotReference(1004, 1, 11),))
    second = service.analyze_for_targets(target_character_ids=(1004,),
                                        selected_slots=(RewindSlotReference(1004, 2, 12),))
    assert first.required_count == 5
    assert second.required_count == 0
    assert second.selected_slots == (RewindSlotReference(1004, 2, 12),)


def test_custom_threshold_uses_frozen_individual_drive_scores(tmp_path):
    service, _ = service_fixture(tmp_path)
    result = service.analyze_for_targets(target_character_ids=(1004,), target_custom_percent=25,
                                         selected_slots=(RewindSlotReference(1004, 1, 11),))
    assert result.required_count == 0  # 4 * 10 * 25% equals the saved ten points.


def test_plan_replacement_during_analysis_prevents_publication(tmp_path, monkeypatch):
    service, dao = service_fixture(tmp_path)
    original = dao.get_loadout_slot
    reads = 0

    def changing_slot(identifier):
        nonlocal reads
        reads += 1
        if reads == 2:
            dao.slots[0]["current_plan"]["plan_id"] = 55
        return original(identifier)

    monkeypatch.setattr(dao, "get_loadout_slot", changing_slot)
    with pytest.raises(ValueError, match="已变化"):
        service.analyze_for_targets(target_character_ids=(1004,),
                                   selected_slots=(RewindSlotReference(1004, 1, 11),))


def test_cancellation_at_validation_never_returns_partial_recommendation(tmp_path):
    from concurrent.futures import CancelledError
    service, _ = service_fixture(tmp_path)
    checkpoints = 0

    def cancel():
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            raise CancelledError()

    with pytest.raises(CancelledError):
        service.analyze_for_targets(target_character_ids=(1004,), checkpoint=cancel,
                                   selected_slots=(RewindSlotReference(1004, 1, 11),))


def test_catalog_preserves_unusable_siblings_and_custom_roles(tmp_path, monkeypatch):
    service, dao = service_fixture(tmp_path)
    custom, _, items = slot_fixture(3)
    custom["character_id"] = 9001
    dao.slots.append(custom)
    dao.items.update(items)
    monkeypatch.setattr(dao, "list_custom_characters", lambda: [{"character_id": 9001, "name_zh": "自建"}])
    roles = {row.character_id: row for row in service.list_target_roles()}
    assert [row.state for row in roles[1004].slots] == ["ready", "incomplete"]
    assert roles[9001].is_custom
    assert roles[9001].slots[0].reference.character_id == 9001


def test_unknown_total_does_not_compete_as_zero_and_deleted_choice_is_preserved():
    from src.domain.rewind_loadout import references_for_roles
    from src.services.rewind.recommendation import RewindTargetRole
    slot, plan, items = slot_fixture()
    plan["score"] = None
    rows = (inspect_slot(slot, plan, items, SHAPE_MAP), inspect_slot(*slot_fixture(2, virtual=True), SHAPE_MAP))
    assert default_slot(rows, None) is None
    role = RewindTargetRole(1004, "测试", None, slots=rows)
    assert references_for_roles((role,), {1004: 99})[1004] == RewindSlotReference(1004, 99, None)


@pytest.mark.parametrize("counts", [{}, {"declared_item_count": None, "stored_item_count": None},
                                  {"declared_item_count": 5, "stored_item_count": 4}])
def test_missing_inventory_counts_do_not_prove_completeness(counts):
    from src.services.rewind.recommendation import snapshot_complete
    assert not snapshot_complete({"complete": True, **counts})



def test_same_uid_in_distinct_saved_sources_never_reuses_current_geometry():
    from src.services.rewind.recommendation import RewindLoadoutReader

    first, _, items = slot_fixture()
    second = deepcopy(first)
    second["slot_id"] = 2
    second["current_plan"].update(plan_id=12, source_snapshot_id=4)
    class SourceDao:
        def inventory_snapshot_summary(self, _):
            return {"complete": True, "declared_item_count": 5, "stored_item_count": 5}
        def list_inventory_items(self, source, *, kind, uids):
            rows = deepcopy(list(items.values()))
            if source == 4:
                rows[0]["geometry"] = "Unknown"
            return rows
    reader = RewindLoadoutReader(SourceDao(), SHAPES)
    assert reader.inspect(first).state == "ready"
    assert reader.inspect(second).state == "layout_invalid"
    assert reader.inspect(first).state == "ready"
