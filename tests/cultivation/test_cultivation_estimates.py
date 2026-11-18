# 验证 Boss 均值估算保留、被动目标退出新流程及旧历史的只读兼容。
import json
import os
from dataclasses import fields
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.domain.progression.stamina import calculate_progression_stamina
from src.features.toolbox.cultivation.stamina_ui import stamina_summary_text
from src.services.progression.models import CultivationMaterial, CultivationRequest, CultivationSeed
from src.services.progression.stamina_planner import calculate_stamina_result


BOSS = "Worldboss_material_04"


@pytest.fixture
def solver(monkeypatch):
    # 隔离入口还会收到求解会话；进程内替身只执行正式纯计算。
    monkeypatch.setattr("src.services.progression.stamina_planner.calculate_isolated_progression_stamina",
                        lambda request, **_kwargs: calculate_progression_stamina(request))


@pytest.mark.parametrize("level,hunter,runs", [(3, 30, 15), (4, 40, 14), (5, 45, 12), (6, 50, 11), (7, 55, 10)])
def test_mean_boss_yield_rounds_runs_up_and_credits_same_run_coins(solver, level, hunter, runs):
    result = calculate_stamina_result((CultivationMaterial(BOSS, "突破材料", 86),
                                       CultivationMaterial("Gold", "货币", 10000)), {}, (),
                                      hunter_level=hunter, effective_identification_level=level)
    assert result.total_stamina == runs * 60
    assert len(result.runs) == 1 and result.runs[0].runs == runs
    assert result.runs[0].source.startswith("cultivation-estimate-")
    assert "估算" in stamina_summary_text(result)


def test_missing_low_level_estimate_and_missing_cost_remain_unknown(solver):
    result = calculate_stamina_result((CultivationMaterial(BOSS, "突破材料", 1),), {}, (),
                                      hunter_level=20, effective_identification_level=2)
    assert result.total_stamina is None and BOSS in result.unresolved_item_ids
    result = calculate_stamina_result((), {}, (), hunter_level=60,
                                      effective_identification_level=7,
                                      material_gaps=("material_cost_unavailable",))
    assert result.total_stamina is None and "material_cost_unavailable" in result.gaps


@pytest.mark.parametrize("batch", [False, True])
def test_editors_and_requests_do_not_offer_removed_targets(batch):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from src.features.toolbox.cultivation.batch.batch_target import CultivationBatchTargetCard
    from src.features.toolbox.cultivation.single.single_editor import CultivationSingleEditor

    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    try:
        seed = CultivationSeed(1072, "角色", 1, 0, (), None)
        if batch:
            editor = CultivationBatchTargetCard("line", seed, parent=owner)
            assert "passives" not in editor.export_history_target()
        else:
            editor = CultivationSingleEditor(owner)
            editor.set_skills(seed)
        assert not hasattr(editor, "passive_editor")
        assert {f.name for f in fields(CultivationRequest)}.isdisjoint({"passives", "include_passives"})
        assert "passives" not in {f.name for f in fields(CultivationSeed)}
    finally:
        owner.close()
        owner.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_single_and_batch_keep_mean_solver_without_removed_costs(solver, tmp_path):
    from src.services.progression.planner_service import CultivationPlannerService
    from src.services.progression.batch_planner import (
        CultivationBatchPlannerService, CultivationBatchRequest, CultivationTargetDraft,
    )

    account = tmp_path / "not-created.sqlite3"
    single = CultivationPlannerService(static_database_path=Path("data/game_static.sqlite3"),
                                       user_database_path=account)
    request = CultivationRequest(1072, 1, 0, 80, 6, ())
    prepared = single.prepare(request)
    assert all(not section.label.startswith("被动天赋") for section in prepared.plan.sections)
    one = single.calculate_stamina(prepared.plan, owned_quantities={}, hunter_level=45,
                                  effective_identification_level=5, farming_stages=prepared.farming_stages)
    assert any(run.source.startswith("cultivation-estimate-") for run in one.total.runs)
    batch = CultivationBatchPlannerService(single)
    target = CultivationTargetDraft("one", request.character_id, request)
    result = batch.calculate(CultivationBatchRequest("account", 1, "dataset", 45, 5, (target,)))
    assert result.combined_stamina == one.total == result.target_plans[0].stamina.total
    assert not account.exists()


def test_all_history_versions_round_trip_and_new_format_rejects_retired_fields(tmp_path):
    from tests.cultivation.history.test_cultivation_history import history_payload
    from src.domain.progression.history import HistoryPayload, PAYLOAD_VERSION
    from src.storage.sqlite.user_data_dao import UserDataDao

    old = history_payload()
    configuration = json.loads(old.configuration_json)
    result = json.loads(old.result_snapshot_json)
    configuration["version"] = result["version"] = 2
    configuration["targets"][0]["skills_enabled"] = True
    configuration["targets"][0]["passives"] = [{"ability_id": "legacy-passive",
                                               "current_unlocked": False, "target_unlocked": True}]
    retired = HistoryPayload.create(configuration, result)
    configuration["version"] = result["version"] = PAYLOAD_VERSION
    with pytest.raises(ValueError):
        HistoryPayload.create(configuration, result)
    configuration["targets"][0].pop("passives")
    current = HistoryPayload.create(configuration, result)
    with UserDataDao(tmp_path / "user.sqlite3", account_id="account") as dao:
        for name, payload in (("old", old), ("retired", retired), ("current", current)):
            dao.save_cultivation_history("account", name, payload)
            assert dao.get_cultivation_history("account", name).payload == payload
            assert HistoryPayload.decode(payload.configuration_json, payload.result_snapshot_json) == payload
