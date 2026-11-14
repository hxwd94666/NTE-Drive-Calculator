# 验证装备建议的原生数学事实保存、schema49升级回滚、编排与历史入口；伪组件仅证明编排。
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from src.domain.teams.models import TeamDraft
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.integrations.battle_analysis.graduation.team_axis_graduation_archive import archive_axis_result
from src.integrations.battle_analysis.graduation.team_axis_graduation_equipment_advice import _stat_row
from src.services.teams.axis_graduation.service import TeamAxisGraduationService, _request_digest
from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.graduation.test_team_axis_graduation_history_ui import wait_for
from tests.teams.graduation.test_team_axis_graduation_history_ui import application
from tests.teams.graduation.test_team_axis_graduation_current import current_response

NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def exported():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_EQUIPMENT_FLOW_RESULT")
    if not path:
        pytest.skip("explicit controlled native benchmark/equipment pair required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_equipment_flow_v1"
    return value


def stored_pair(path, exported, monkeypatch, *, old=False):
    from src.storage.sqlite import user_data_base
    from src.storage.sqlite.teams import teams_dao
    raw = json.loads(exported["benchmark"]["request_bytes"])["request"]
    with monkeypatch.context() as patch:
        if old:
            patch.setattr(user_data_base, "SCHEMA_VERSION", 49)
        patch.setattr(teams_dao, "uuid4", lambda: SimpleNamespace(hex=raw["current"]["team_id"]))
        with UserDataDao(path, account_id=raw["current"]["account_id"]) as dao:
            team = dao.save_team(TeamDraft("原生合成建议", tuple(raw["current"]["character_ids"])))
            benchmark = archive_axis_result(raw, exported["benchmark"]["response"])
            dao.save_team_axis_result(team, benchmark, expected_target_revision=0, expected_selection_revision=0)
    equipment = archive_axis_result(json.loads(exported["equipment"]["request_bytes"])["request"], exported["equipment"]["response"])
    return team, benchmark, equipment


def test_schema49_upgrade_preserves_selection_payload_and_consistent_backup(tmp_path, exported, monkeypatch):
    path = tmp_path / "user.sqlite3"
    team, baseline, equipment = stored_pair(path, exported, monkeypatch, old=True)
    with sqlite3.connect(path) as db:
        old_row = db.execute("SELECT * FROM team_axis_graduation_evaluation").fetchone()
        selected = db.execute("SELECT * FROM team_axis_graduation_selection").fetchone()
    with UserDataDao(path) as dao:
        assert dao._db().execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == SCHEMA_VERSION
        assert dao._db().execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []
        assert tuple(dao._db().execute("SELECT * FROM team_axis_graduation_evaluation").fetchone()) == old_row
        assert tuple(dao._db().execute("SELECT * FROM team_axis_graduation_selection").fetchone()) == selected
        dao.save_team_axis_result(team, equipment, expected_target_revision=1, expected_selection_revision=1, select=False)
        assert dao.get_team_axis_selection(team.team_id).evaluation_id == baseline.evaluation_id
        dao.select_team_axis_result(team, equipment.evaluation_id, expected_revision=1)
    with UserDataDao(path) as dao:
        assert dao.get_team_axis_evaluation(team.team_id, equipment.evaluation_id) == equipment
        assert dao.get_team_axis_benchmark(team.team_id, baseline.benchmark_id) == baseline
    backups = list((tmp_path / ".migration-backups").glob("user-data-v49-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 49
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("SELECT * FROM team_axis_graduation_selection").fetchone() == selected


def test_schema50_failure_rolls_back_parent_table_and_selection_then_retries(tmp_path, exported, monkeypatch):
    from src.storage.sqlite import user_data_base
    path = tmp_path / "user.sqlite3"
    team, baseline, _ = stored_pair(path, exported, monkeypatch, old=True)
    broken = tmp_path / "broken.sql"
    broken.write_text(user_data_base.USER_MIGRATIONS[50].read_text("utf8") + "\nSELECT * FROM synthetic_missing;", encoding="utf8")
    with monkeypatch.context() as patch:
        patch.setitem(user_data_base.USER_MIGRATIONS, 50, broken)
        with pytest.raises(RuntimeError):
            UserDataDao(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 49
        assert db.execute("SELECT evaluation_id FROM team_axis_graduation_selection").fetchone()[0] == baseline.evaluation_id
        assert db.execute("SELECT name FROM sqlite_schema WHERE name='team_axis_graduation_evaluation_v50'").fetchone() is None
    with UserDataDao(path) as dao:
        assert dao.get_team_axis_benchmark(team.team_id, baseline.benchmark_id) == baseline
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []


def test_native_equipment_history_restores_full_target_without_starting_analysis(tmp_path, exported, monkeypatch):
    application()
    path = tmp_path / "user.sqlite3"
    team, baseline, equipment = stored_pair(path, exported, monkeypatch)
    with UserDataDao(path) as dao:
        dao.save_team_axis_result(team, equipment, expected_target_revision=1, expected_selection_revision=1)
    calls = []
    inputs = SimpleNamespace(database_path=path, account_id=equipment.account_id, checkpoint=lambda _cancelled: None)
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path=tmp_path / "missing.json",
                                       client_factory=lambda **kw: calls.append(kw))
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert "固定观测轴毕业率" in dialog.headline.text()
        assert "整队联合" in dialog.advice.text() and "piece-" in dialog.advice.text()
        assert "不是背包可达" in dialog.advice.text() and "强化" in dialog.advice.text()
        owner.restore_saved(equipment.evaluation_id)
        wait_for(lambda: not owner.is_running())
        assert owner.axis_selection is not None and owner.can_update
        assert owner.axis_selection.target == baseline.target
        assert service.selected_result(team)[1].reference == baseline.reference
        assert calls == []
    finally:
        cleanup(owner, dialog)


def equipment_stub(body, old):
    # 只为编排造合法参数形状与固定结果；真实37项数值由exported夹具覆盖，不在此实现伤害公式。
    out = current_response(body, old, _request_digest(body, "fixture"))
    rules = body["rules"]
    preset = next(p for p in body["target"]["presets"] if p["preset_id"] == body["reference"]["payload"]["preset_id"])
    specs = {s["item_id"]: s for s in rules["items"]}
    goals = []
    for actor, layout in zip(body["reference"]["payload"]["build"], preset["layouts"]):
        pieces = []
        anchors = {p["piece_id"]: p for p in layout["pieces"]}
        for choice in actor["pieces"]:
            spec = specs[choice["item_id"]]
            coef = dict(rules["quality_coefficients"])[spec["quality"]]
            if spec["kind"] == "core":
                main = next(r for r in rules["core_main_stats"] if r["property_id"] == choice["main_property_id"])
                stats, pool, scale = [_stat_row(main, coef, "main")], rules["core_sub_stats"], coef
            else:
                stats, pool, scale = [_stat_row(r, 1, "main") for r in spec["intrinsic_main_stats"]], rules["drive_sub_stats"], coef*spec["grid_count"]
            stats += [_stat_row(next(r for r in pool if r["property_id"] == prop), scale, "sub") for prop in choice["sub_property_ids"]]
            item = {"theoretical_piece_id": choice["piece_id"], "item_id": spec["item_id"], "kind": spec["kind"], "quality": spec["quality"],
                "level": spec["max_level"], "max_level": spec["max_level"], "geometry": spec["geometry_id"], "grid_count": spec["grid_count"] or 0,
                "suit_id": spec["suit_id"], "stats": stats}
            if choice["piece_id"] in anchors:
                anchor = anchors[choice["piece_id"]]
                item.update(target_row=anchor["row"], target_column=anchor["column"])
            pieces.append(item)
        pieces.sort(key=lambda p: (p["kind"] != "core", p["theoretical_piece_id"]))
        goals.append({"character_id": actor["character_id"], "equipment": pieces})
    evaluation = deepcopy(out["current"]["axis_evaluation"])
    evaluation.update(damage=150, known_damage=150)
    changes = [{"character_id": goals[0]["character_id"], "mode": "replace_loadout", "piece_ids": [p["theoretical_piece_id"] for p in goals[0]["equipment"]]}]
    out["equipment_recommendations"] = {"scope": "equipment_to_reference_build_v1", "preset_id": preset["preset_id"], "target_equipment": goals,
        "piece_steps_unavailable_members": [a["character_id"] for a in goals], "candidate_limit": 64, "generated_candidates": 1,
        "attempted_candidates": 1, "evaluated_candidates": 1, "preparation_unavailable_candidates": 0, "omitted_candidates": 0,
        "stop_reason": "declared_candidates_complete", "items": [{"candidate_id": "synthetic-one", "kind": "member_equipment", "changes": changes,
        "status": "mathematical_only", "error_code": None, "evaluation": evaluation,
        "impact": {"damage_delta": 10, "relative_gain": 10/140, "reference_fraction_delta": .05, "graduation_percentage_points": None}}]}
    return out


def test_equipment_owner_freezes_previews_saves_and_missing_capability_keeps_reference(tmp_path, monkeypatch):
    service, inputs, client, values, team = case(tmp_path)
    client.supports_team_axis_graduation_equipment_advice = True
    original = client.evaluate_team_axis_graduation
    def evaluate(body, *, checkpoint):
        if body["operation"] != "equipment_advice_evaluation":
            return original(body, checkpoint=checkpoint)
        checkpoint()
        client.operations.append(body["operation"])
        return equipment_stub(body, client.old)
    monkeypatch.setattr(client, "evaluate_team_axis_graduation", evaluate)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        owner.restore_saved(owner.history.selected_evaluation_id)
        wait_for(lambda: not owner.is_running())
        reference = service.selected_result(team)[1].reference
        owner.load_equipment_advice()
        wait_for(lambda: not owner.is_running())
        assert owner.preview.request.payload["operation"] == "equipment_advice_evaluation"
        assert "budget" not in owner.preview.request.payload and "seeds" not in owner.preview.request.payload
        assert owner.can_save and owner.preview.reference == reference
        owner.save_preview()
        wait_for(lambda: not owner.is_running())
        assert service.selected_result(team)[1].operation == "equipment_advice_evaluation"
        client.supports_team_axis_graduation_equipment_advice = False
        count = len(client.operations)
        owner.load_equipment_advice()
        wait_for(lambda: not owner.is_running())
        assert owner.state == "component_pending" and len(client.operations) == count and owner.can_update
        assert service.selected_result(team)[1].reference == reference
    finally:
        cleanup(owner, dialog)


def test_account_staging_validates_new_schema_and_keeps_native_equipment_result(tmp_path, exported, monkeypatch):
    from src.storage.sqlite.accounts.transfer_database import backup_account_database, prepare_account_database
    source = tmp_path / "source.sqlite3"
    team, _, equipment = stored_pair(source, exported, monkeypatch)
    with UserDataDao(source) as dao:
        dao.save_team_axis_result(team, equipment, expected_target_revision=1, expected_selection_revision=1)
    staged = tmp_path / "staged.sqlite3"
    backup_account_database(source, staged)
    prepare_account_database(staged, account_id=equipment.account_id, account_name="合成建议")
    with UserDataDao(staged) as dao:
        assert dao.get_team_axis_evaluation(team.team_id, equipment.evaluation_id) == equipment
        assert dao.get_team_axis_selection(team.team_id).evaluation_id == equipment.evaluation_id


def test_equipment_save_commit_cancellation_keeps_previous_pointer_and_rejects_other_account(tmp_path, exported, monkeypatch):
    from concurrent.futures import CancelledError
    path = tmp_path / "user.sqlite3"
    team, baseline, equipment = stored_pair(path, exported, monkeypatch)
    with UserDataDao(path) as dao:
        calls = []
        def checkpoint():
            calls.append(1)
            if len(calls) == 2:
                raise CancelledError
        with pytest.raises(CancelledError):
            dao.save_team_axis_result(team, equipment, expected_target_revision=1, expected_selection_revision=1, checkpoint=checkpoint)
        assert dao.get_team_axis_selection(team.team_id).evaluation_id == baseline.evaluation_id
        assert dao.get_team_axis_evaluation(team.team_id, equipment.evaluation_id) is None
        assert dao.get_team_axis_benchmark(team.team_id, baseline.benchmark_id) == baseline
    with UserDataDao(tmp_path / "other.sqlite3", account_id="other-synthetic") as dao:
        with pytest.raises(ValueError):
            dao.save_team_axis_result(team, equipment, expected_target_revision=1, expected_selection_revision=0)
        assert dao._db().execute("SELECT COUNT(*) FROM team_axis_graduation_evaluation").fetchone()[0] == 0


def test_unsaved_first_benchmark_is_not_lost_by_opening_equipment_advice(tmp_path):
    from src.domain.teams.report_matching import ReportRange
    from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection
    service, _inputs, client, values, team = case(tmp_path, stored=False)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        owner.set_selection(AxisGraduationSelection(team, ReportRange(1), values['target'], values['rules']))
        owner.run_benchmark(seeds=values['seeds'], max_evaluations=values['max_evaluations'], max_millis=values['max_millis'])
        wait_for(lambda: not owner.is_running())
        preview = owner.preview
        assert owner.can_save and not owner.can_load_equipment_advice
        assert not dialog.equipment_recommend.isEnabled() and not owner.load_equipment_advice()
        assert owner.preview is preview and len(client.operations) == 1
        owner.save_preview()
        wait_for(lambda: not owner.is_running())
        assert owner.can_load_equipment_advice and dialog.equipment_recommend.isEnabled()
    finally:
        cleanup(owner, dialog)
