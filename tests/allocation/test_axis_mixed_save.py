# 核对优化与基础方案混合保存的完整依赖、冻结身份和实际装备。
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import json

import pytest

from src.services.allocation.axis.axis_postprocess import (
    AxisOptimizationPreviewSource,
    verify_axis_postprocess_save,
)
from src.services.allocation.results.postprocess_facts import (
    allocation_inputs_digest,
    allocation_row_save_projection,
)


def mixed_rows(tmp_path):
    identity = {"request_id": "one-frozen-job"}
    builds = [{"role": role, "character_id": cid, "basis": basis,
               "role_state": {"equipment": role}}
              for role, cid, basis in (("A", 1, "accepted"), ("B", 2, "baseline_assumption"))]
    dependencies = {"source_snapshot_id": 3, "input_identity": identity,
                    "source_input_digest": "axis-source", "roles": builds}
    frozen = SimpleNamespace(
        selection=SimpleNamespace(optimize_groups=True, participating_character_ids=(1, 2),
            user_database_path=tmp_path.resolve(), fixed_team_build_json="",
            fixed_team_proof_json=json.dumps({"fixed_build_sha256": "fixed"}), fixed_team_members_json="[]"),
        role_character_ids=(("A", 1), ("B", 2)), source=SimpleNamespace(source_input_digest="axis-source"),
        owners_sha256=allocation_inputs_digest([]), snapshot_id=3, additional_axes=(), dependencies=None, semantics_path=None,
    )
    rows = []
    for build in builds:
        meta = {"input_identity": deepcopy(identity), "dataset_id": "dataset", "source_snapshot_id": 3,
                "character_id": build["character_id"], "status": "baseline_only", "result_kind": "baseline"}
        rows.append({"role_name": build["role"], "character_id": build["character_id"], "snapshot_id": 3,
                     "role_state": deepcopy(build["role_state"]), "score": 1,
                     "payload": {"damage_optimization": meta}})
    meta = rows[0]["payload"]["damage_optimization"]
    meta.pop("result_kind")
    meta.update(status="completed", objective="frozen_axis_expected_v1", model_id="frozen_axis_expected_v1",
        equipment_value_mode="actual_level", engine_version="test", source_input_digest="axis-source",
        fixed_team_build_sha256="fixed", fixed_team_members=[], owners_sha256=frozen.owners_sha256,
        save_dependencies=["A", "B"], group_members=[{"role": "A", "character_id": 1}], group_index=0,
        shared_build_dependencies=dependencies, shared_build_dependencies_sha256=allocation_inputs_digest(dependencies))
    rows[0]["axis_preview_source"] = AxisOptimizationPreviewSource(
        frozen, allocation_inputs_digest(meta), allocation_inputs_digest(allocation_row_save_projection(rows[0])))
    return rows


def verify(tmp_path, rows, batch):
    with patch("src.services.allocation.axis.axis_postprocess.locked_allocation_owners", return_value=[]), \
         patch("src.services.allocation.axis.axis_postprocess.verify_axis_source"):
        verify_axis_postprocess_save(SimpleNamespace(database_path=tmp_path), rows, dataset_id="dataset",
            client=SimpleNamespace(dataset_version="dataset", engine_version="test", component_provenance={}),
            batch_rows=batch)


def test_axis_dependency_accepts_same_batch_baseline(tmp_path):
    rows = mixed_rows(tmp_path)
    verify(tmp_path, rows[:1], rows)


def test_axis_dependency_still_rejects_missing_baseline(tmp_path):
    rows = mixed_rows(tmp_path)
    with pytest.raises(RuntimeError, match="关联组"):
        verify(tmp_path, rows[:1], rows[:1])


@pytest.mark.parametrize("field", ["job", "state", "character", "snapshot", "duplicate"])
def test_axis_dependency_rejects_mixed_or_changed_baseline(tmp_path, field):
    rows = mixed_rows(tmp_path)
    if field == "job":
        rows[1]["payload"]["damage_optimization"]["input_identity"] = {"request_id": "old-job"}
    elif field == "state":
        rows[1]["role_state"] = {"equipment": "different"}
    elif field == "character":
        rows[1]["character_id"] = 99
    elif field == "snapshot":
        rows[1]["snapshot_id"] = 99
    else:
        rows.append(deepcopy(rows[1]))
    with pytest.raises(RuntimeError, match="依赖|预览|重复"):
        verify(tmp_path, rows[:1], rows)


def test_mixed_axis_and_baseline_are_saved_in_one_transaction(tmp_path):
    from dataclasses import replace
    from tests.allocation.test_axis_postprocess_component import axis_facade_case, run_axis_facade
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    from src.services.allocation.direct.direct_postprocess import optimization_payload
    from src.services.allocation.inputs.locks import build_allocation_lock_snapshot
    from src.services.allocation.results.plan_save import save_allocation_plans, legacy_plan_tape_main_values
    from src.services.allocation.results.postprocess_facts import allocation_role_state_from_plan
    from src.storage.sqlite.user_data_dao import UserDataDao

    case = axis_facade_case(tmp_path, joint=True, second=True)
    case["selection"] = replace(case["selection"], rank_baseline=True)
    # 真实组件先返回未改动的 B0；控制首扫接纳边界，稳定构造一组已接纳、一组基础回退。
    with patch("src.services.allocation.axis.axis_postprocess.postprocess_first_scan_cut",
               side_effect=lambda group, **_: group["group_index"] != 0):
        plans, client = run_axis_facade(case, groups=[[role] for role in case["selected_roles"]],
                                      budget={"max_evaluations": 1, "max_rounds": 100})
    assert sorted(plan["damage_optimization"]["status"] for plan in plans.values()) == ["baseline_only", "completed"]
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit:
        release = unit.release
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=case["source_snapshot_id"])
    rows = []
    for role, cid in (("灵可", 1072), ("早雾", 1003)):
        plan = plans[role]
        items = [plan["assigned_tape"], *plan["assigned_set_drives"], *plan["assigned_extra_drives"]]
        rows.append(dict(role_name=role, character_id=cid, snapshot_id=case["source_snapshot_id"], slot_id=None,
            name="混合保存专项", score=plan["score"], role_state=allocation_role_state_from_plan(plan),
            axis_preview_source=plan.get("axis_preview_source"), baseline_preview_source=plan.get("baseline_preview_source"),
            payload={"source_role_name": role, "assignment_scores": {item.uid: item.role_scores[role] for item in items},
                     "tape_main_values": legacy_plan_tape_main_values(plan), **optimization_payload(plan)}))
    arguments = dict(database_path=case["dependencies"].user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        checkpoint=lambda: None, progress=lambda _: None, axis_analysis_client=client)
    optimized = [row for row in rows if row["axis_preview_source"] is not None]
    with pytest.raises(RuntimeError, match="关联组"):
        save_allocation_plans(rows=optimized, **arguments)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        assert not dao.list_loadout_slots(1072) and not dao.list_loadout_slots(1003)
    assert save_allocation_plans(rows=rows, **arguments) == 2
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        for row in rows:
            saved = dao.list_loadout_slots(row["character_id"])[0]["current_plan"]
            assert saved["payload"]["damage_optimization"] == row["payload"]["damage_optimization"]
