# 完整保存集合包含优化组和合法 B0 时分别验凭证，再核对同一共享目标的全部构筑依赖。
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from src.integrations.battle_analysis.battle_axis_evaluation import MODEL_ID
from src.services.allocation.axis.axis_postprocess import AxisOptimizationPreviewSource, FrozenAxisAllocationInputs, verify_axis_postprocess_save
from src.services.allocation.execution.baseline_fallback import BaselinePreviewSource, verify_baseline_save
from src.services.allocation.results.postprocess_facts import allocation_inputs_digest, allocation_row_save_projection, locked_allocation_owners
from src.storage.sqlite.user_data_dao import UserDataDao


# 来源、事务与账号失效属于核心边界，显式分层避免仅按文件名遗漏。
NTE_TEST_TIER = "core"


def mixed_rows(dao):
    identity = {"request_id": "fixture-job", "input_sha256": "a"*64}
    owners = allocation_inputs_digest(locked_allocation_owners(dao))
    states = {"A": {"frozen_build": "accepted"}, "B": {"frozen_build": "baseline"}}
    dependencies = dict(source_snapshot_id=1, input_identity=identity, source_input_digest="b"*64,
        roles=[dict(role="A", character_id=1, basis="accepted", role_state=states["A"]),
               dict(role="B", character_id=2, basis="baseline_assumption", role_state=states["B"])])
    frozen = FrozenAxisAllocationInputs(selection=SimpleNamespace(optimize_groups=True, participating_character_ids=(1, 2),
        fixed_team_proof_json=json.dumps({"fixed_build_sha256": "c"*64}), fixed_team_members_json="[]",
        user_database_path=dao.database_path.resolve()), dependencies=None, semantics_path=dao.database_path,
        source=SimpleNamespace(source_input_digest="b"*64), snapshot_id=1, inventory_json="[]",
        role_character_ids=(("A", 1), ("B", 2)), reserved_uids=(), owners_sha256=owners, reason="")
    metadata = dict(objective=MODEL_ID, model_id=MODEL_ID, status="completed", equipment_value_mode="actual_level",
        source_snapshot_id=1, dataset_id="fixture", character_id=1, source_input_digest="b"*64,
        engine_version="fixture", owners_sha256=owners, fixed_team_build_sha256="c"*64, fixed_team_members=[],
        input_identity=identity, save_dependencies=["A", "B"], shared_build_dependencies=dependencies,
        shared_build_dependencies_sha256=allocation_inputs_digest(dependencies), group_index=0,
        group_members=[dict(role="A", character_id=1)])
    axis = dict(role_name="A", character_id=1, snapshot_id=1, score=10, role_state=states["A"],
                payload={"damage_optimization": metadata, "assignment_scores": {"A-item": 10}, "tape_main_values": {}})
    axis["axis_preview_source"] = AxisOptimizationPreviewSource(frozen, allocation_inputs_digest(metadata),
        allocation_inputs_digest(allocation_row_save_projection(axis)))
    baseline_metadata = dict(result_kind="baseline", status="baseline_only",
                             dataset_id="fixture", input_identity=identity)
    base = dict(role_name="B", character_id=2, snapshot_id=1, score=20, role_state=states["B"],
                payload={"damage_optimization": baseline_metadata, "assignment_scores": {"B-item": 20}, "tape_main_values": {}})
    base["baseline_preview_source"] = BaselinePreviewSource(dao.database_path.resolve(), "fixture", 1, 2, owners,
        allocation_inputs_digest(baseline_metadata), allocation_inputs_digest(allocation_row_save_projection(base)))
    return axis, base


def test_complete_mixed_set_is_accepted_without_dropping_dependencies(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("src.services.allocation.axis.axis_postprocess.verify_axis_source", lambda *args, **kwargs: calls.append(args))
    client = SimpleNamespace(dataset_version="fixture", engine_version="fixture", component_provenance={})
    with UserDataDao(tmp_path / "mixed.sqlite3", account_id="mixed") as dao:
        axis, baseline = mixed_rows(dao)
        verify_baseline_save(dao, [baseline], dataset_id="fixture", database_path=dao.database_path)
        verify_axis_postprocess_save(dao, [axis], dataset_id="fixture", client=client, batch_rows=[axis, baseline])
        assert len(calls) == 1
        with pytest.raises(RuntimeError, match="一起保存"):
            verify_axis_postprocess_save(dao, [axis], dataset_id="fixture", client=client)
        altered = deepcopy(baseline)
        altered["role_state"] = {"frozen_build": "different"}
        with pytest.raises(RuntimeError, match="基础配装"):
            verify_baseline_save(dao, [altered], dataset_id="fixture", database_path=dao.database_path)
        with pytest.raises(RuntimeError, match="构筑依赖"):
            verify_axis_postprocess_save(dao, [axis], dataset_id="fixture", client=client, batch_rows=[axis, altered])


def test_mixed_group_cannot_use_unrelated_job_or_character(tmp_path, monkeypatch):
    monkeypatch.setattr("src.services.allocation.axis.axis_postprocess.verify_axis_source", lambda *args, **kwargs: None)
    client = SimpleNamespace(dataset_version="fixture", engine_version="fixture", component_provenance={})
    with UserDataDao(tmp_path / "mixed.sqlite3", account_id="mixed") as dao:
        axis, baseline = mixed_rows(dao)
        baseline["character_id"] = 3
        baseline["baseline_preview_source"] = replace(baseline["baseline_preview_source"], character_id=3,
            save_projection_sha256=allocation_inputs_digest(allocation_row_save_projection(baseline)))
        verify_baseline_save(dao, [baseline], dataset_id="fixture", database_path=dao.database_path)
        with pytest.raises(RuntimeError, match="构筑依赖"):
            verify_axis_postprocess_save(dao, [axis], dataset_id="fixture", client=client, batch_rows=[axis, baseline])
