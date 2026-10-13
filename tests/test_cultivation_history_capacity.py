# 用发行资料和正式材料体力求解验证历史容量预算，只写临时账号并记录实际占用。
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from src.domain.cultivation_history import MAX_PAYLOAD_BYTES, freeze_configuration
from src.services.progression.history_adapters import batch_history_payload
from src.services.progression.batch_planner import CultivationBatchPlannerService, CultivationBatchRequest, CultivationTargetDraft
from src.services.progression.models import CultivationForkTarget, CultivationRequest, CultivationSkillTarget
from src.services.progression.planner_service import CultivationPlannerService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.cultivation_history_test_support import module_load_tests

# Real release resources are needed; this is an explicit capacity integration gate, not core.
NTE_TEST_TIER = "integration"
load_tests = module_load_tests(__name__, __file__)


@pytest.mark.parametrize("relative", ["data/game_static.sqlite3", "data/role_catalog/game_static.sqlite3"])
def test_entire_selectable_catalog_fits_budget_and_records_storage_sizes(tmp_path, relative):
    dataset = Path(__file__).resolve().parents[1] / relative
    assert dataset.is_file(), "发行资料缺失，容量验收尚未具备输入"
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="capacity-fixture"):
        pass
    single = CultivationPlannerService(user_database_path=database, static_database_path=dataset)
    roles, forks = single.list_roles(), single.list_forks()
    assert roles, "完整角色目录为空，不能作为容量验收"
    targets, configurations = [], []
    for role in roles:
        seed = single.load_seed(role.character_id)
        fork = seed.fork.fork_id if seed.fork is not None else (forks[0].fork_id if forks else None)
        line_id = f"capacity-{role.character_id}"
        request = CultivationRequest(
            role.character_id, 1, 0, 80, 6,
            tuple(CultivationSkillTarget(skill.skill_id, 1, skill.maximum_level) for skill in seed.skills),
            fork=CultivationForkTarget(fork, 1, 0, 80, 6) if fork is not None else None,
        )
        targets.append(CultivationTargetDraft(line_id, role.character_id, request))
        configurations.append({
            "line_id": line_id, "character_id": role.character_id, "name": role.name,
            "current_level": 1, "current_stage": 0, "target_level": 80, "target_stage": 6,
            "character_enabled": True, "skills_enabled": True,
            "skills": [{"skill_id": skill.skill_id, "current_level": 1, "target_level": skill.maximum_level} for skill in seed.skills],
            "fork": {"fork_id": fork, "name": single.load_fork_seed(fork).fork_name, "enabled": True,
                     "current_level": 1, "current_stage": 0, "target_level": 80, "target_stage": 6} if fork is not None else None,
        })
    batch = CultivationBatchPlannerService(single)
    request = CultivationBatchRequest("capacity-fixture", 1, "capacity-dataset", 60, 7, tuple(targets))
    samples = []
    for count in sorted({1, min(5, len(configurations)), len(configurations)}):
        subset = replace(request, ordered_targets=tuple(targets[:count]))
        prepared = batch.prepare(subset)
        configuration = _configuration(prepared, configurations[:count])
        plan = batch.calculate(subset, preparation=prepared)
        payload = batch_history_payload(freeze_configuration(configuration), plan, single.dataset_metadata())
        samples.append((f"configuration_{count}", payload))
    with UserDataDao(database) as dao:
        for label, payload in samples:
            config_size = len(payload.configuration_json.encode("utf-8"))
            result_size = len(payload.result_snapshot_json.encode("utf-8"))
            assert config_size + result_size <= MAX_PAYLOAD_BYTES
            dao.save_cultivation_history("capacity-fixture", label, payload)
            assert dao.get_cultivation_history("capacity-fixture", label).payload == payload
            print(json.dumps({"sample": label, "roles": len(json.loads(payload.configuration_json)["targets"]),
                              "configuration_bytes": config_size, "result_bytes": result_size,
                              "database_bytes": database.stat().st_size,
                              "wal_bytes": database.with_name(database.name + "-wal").stat().st_size}, sort_keys=True))
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def _configuration(prepared, configurations):
    return {"version": 1, "mode": "single" if len(configurations) == 1 else "batch", "targets": configurations,
                     "hunter_level": 60, "identification_level": 7, "material_scope": "all",
                     "owned_materials": [{"item_id": item.item_id, "quantity": 0, "manual_override": True,
                                          "source": "manual", "observed_at_utc": None} for item in prepared.owned_inputs]}
