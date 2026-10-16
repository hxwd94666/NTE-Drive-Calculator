# 验证扩展图纸恢复和原生坐标保存：不重跑 Python 摆盘，坏证明不留下方案。
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

import pytest

from tests.allocation.test_direct_postprocess import fixture
from tests.allocation.test_direct_postprocess_save import setup_case
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.models.equipment import Drive
from src.services.allocation.plan_save import save_allocation_plans
from src.services.allocation.postprocess_facts import allocation_plan_wire, allocation_role_state_from_plan, restore_postprocess_results
from src.services.equipment.saved_state_loadout_bridge import SavedStateLoadoutError
from src.storage.sqlite.user_data_dao import UserDataDao

NTE_TEST_TIER = "core"


def _proof(state):
    return {"version": 1, "tape_uid": state["equipped_tape"]["uid"], "drives": [
        {**state["equipped_drives"][0], "target_row": 2, "target_column": 2}]}


def test_native_layout_is_saved_without_legacy_block_or_anchor_search(tmp_path):
    path, _, _, kwargs = setup_case(tmp_path)
    state = kwargs["rows"][0]["role_state"]
    state["native_layout"] = _proof(state)
    with patch("src.services.equipment.saved_state_loadout_bridge.extract_drive_blocks_from_state",
               side_effect=AssertionError("legacy block search called")), \
            patch("src.services.equipment.saved_state_loadout_bridge._official_anchor",
                  side_effect=AssertionError("legacy anchor search called")):
        assert save_allocation_plans(**kwargs) == 1
    with UserDataDao(path) as dao:
        plan = dao.list_loadout_slots(1003)[0]["current_plan"]
    module = next(row for row in plan["assignments"] if row["kind"] == "module")
    assert (module["target_row"], module["target_column"]) == (2, 2)
    assert plan["score"] == 65.0
    assert plan["payload"]["assignment_scores"] == kwargs["rows"][0]["payload"]["assignment_scores"]


def test_bad_native_layout_never_falls_back_to_legacy_search_or_creates_partial_plan(tmp_path):
    path, _, _, kwargs = setup_case(tmp_path)
    state = kwargs["rows"][0]["role_state"]
    valid = _proof(state)
    variants = []
    for key, value in (("target_row", 4), ("target_column", True), ("uid", "nte-module-999-999")):
        bad = deepcopy(valid)
        bad["drives"][0][key] = value
        variants.append(bad)
    variants.extend([None, {**valid, "drives": []}, {**valid, "drives": valid["drives"] * 2}])
    with patch("src.services.equipment.saved_state_loadout_bridge.extract_drive_blocks_from_state",
               side_effect=AssertionError("invalid proof fell back to legacy search")):
        for bad in variants:
            state["native_layout"] = bad
            with pytest.raises(SavedStateLoadoutError):
                save_allocation_plans(**kwargs)
            with UserDataDao(path) as dao:
                assert dao.list_loadout_slots(1003) == []


def test_rematch_restores_only_frozen_blueprint_and_preserves_new_score_and_native_layout():
    request, scorer, _, _, _ = fixture()
    old_bp = {"set_effect_mode": "none", "set_pieces": [], "extra_pieces": ["H_2", "H_2"],
              "board": [["H_2"] * 4]}
    new_bp = {"set_effect_mode": "none", "set_pieces": [], "extra_pieces": ["H_4"],
              "board": [["H_4"] * 4]}
    def drive(uid, shape, area):
        return Drive(uid=uid, shape_id=shape, area=area, quality="Gold",
                     main_stats={"攻击力": 21.0 * area, "生命值": 280.0 * area}, sub_stats={"攻击力%": 6.0})
    old = [drive("nte-module-11-110", "H_2", 2), drive("nte-module-12-120", "H_2", 2)]
    new = drive("nte-module-13-130", "H_4", 4)
    tape = request.inventory[-1].model_copy(deep=True)
    weights = request.roles_db["A"]["weights"]
    maximum = scorer.max_theoretical_weight(weights)
    for item in [*old, new, tape]:
        item.role_scores["A"] = (scorer.calculate_drive_score(item, weights, maximum)
                                 if item.item_type == "drive" else scorer.calculate_cartridge_score(item, weights, maximum))
    request = replace(request, inventory=(*old, new, tape), blueprints_db={"A": [old_bp, new_bp]})
    baseline = {"A": {"valid": True, "blueprint": old_bp, "assigned_tape": tape,
                      "assigned_set_drives": [], "assigned_extra_drives": old,
                      "score": sum(item.role_scores["A"] for item in [*old, tape])}}
    candidate = deepcopy(baseline)
    candidate["A"].update(blueprint=new_bp, assigned_extra_drives=[new],
        score=new.role_scores["A"] + tape.role_scores["A"], native_layout={"version": 1, "tape_uid": tape.uid,
        "drives": [{"uid": new.uid, "shape_id": "H_4", "target_row": 1, "target_column": 2}]})
    before = allocation_plan_wire(baseline)
    wire = allocation_plan_wire(candidate)
    with pytest.raises(NativeAnalysisError):
        restore_postprocess_results(request, scorer, baseline, wire, {"A"}, ())
    scores = {"A": {item.uid: item.role_scores["A"] for item in [new, tape]}}
    with patch.object(scorer, "max_theoretical_weight", side_effect=AssertionError("Python scoring called")), \
            patch.object(scorer, "calculate_drive_score", side_effect=AssertionError("Python scoring called")), \
            patch.object(scorer, "calculate_cartridge_score", side_effect=AssertionError("Python scoring called")):
        restored = restore_postprocess_results(request, scorer, baseline, wire, {"A"}, (),
                                               allow_blueprint_changes=True, base_scores=scores)
        forged = deepcopy(scores)
        forged["A"][new.uid] += 1
        with pytest.raises(NativeAnalysisError):
            restore_postprocess_results(request, scorer, baseline, wire, {"A"}, (),
                                        allow_blueprint_changes=True, base_scores=forged)
    state = allocation_role_state_from_plan(restored["A"])
    assert len(state["equipped_drives"]) == 1 and state["blueprint_layout"] == [["H_4"] * 4]
    assert state["native_layout"]["drives"][0]["target_column"] == 2
    assert allocation_plan_wire(baseline) == before
    bad = deepcopy(wire)
    bad["A"]["blueprint"]["set_effect_mode"] = "four_piece"
    with pytest.raises(NativeAnalysisError):
        restore_postprocess_results(request, scorer, baseline, bad, {"A"}, (), allow_blueprint_changes=True)
