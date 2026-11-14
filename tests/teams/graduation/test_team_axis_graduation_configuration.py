# 以既有原生合成战报副本验证首次目标冻结、真实图纸锚点、合法起点及只显式提交的编辑交互。
from collections import Counter
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox

from tests.support.auto_sync_ui_fixture import application
from tests.teams.matching.team_report_fixture import wait_for
from src.domain.teams.graduation.axis_graduation_configuration import configuration_seeds, make_configuration_target
from src.domain.teams.graduation.axis_graduation_target import TargetWorldBonus
from src.domain.teams.models import TeamDraft
from src.domain.teams.report_matching import MatchStatus, ReportRange, TeamReportMatch
from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.graduation.axis_graduation_target_dialog import GraduationTargetDialog
from src.features.teams.controller import TeamController
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.storage.sqlite.accounts.transfer_database import backup_account_database
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"


def configuration_case(directory):
    fixture_path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_FILE_FIXTURE")
    if not fixture_path:
        pytest.skip("explicit existing synthetic file fixture required")
    original = json.loads(Path(fixture_path).read_text("utf8"))
    assert original["fixture_kind"] == "synthetic_team_axis_graduation_files_v1"
    for key, sha in original["source_sha256"].items():
        assert hashlib.sha256(Path(original["selector"][key]).read_bytes()).hexdigest() == sha
    wire_path = Path(fixture_path).parent / "synthetic-graduation-wire-result-v5.json"
    wire = json.loads(wire_path.read_text("utf8"))
    assert wire["fixture_kind"] == "synthetic_graduation_wire_v1"
    request = json.loads(wire["request_bytes"])["request"]
    path = directory / "bootstrap-user.sqlite3"
    assert not path.exists()
    backup_account_database(Path(original["selector"]["user_database_path"]), path)
    native, assignments = [], {}
    for actor in request["current"]["build"]["characters"]:
        cid, rows = actor["character_id"], []
        for n, item in enumerate(actor["equipment"]):
            slot, serial = (4 if item["kind"] == "core" else 3), cid*100+n
            native.append(dict(uid={"slot": slot, "serial": serial}, kind=item["kind"], item_id=item["item_id"],
                suit_id=item["suit_id"], geometry=item["geometry"], grid=item["grid_count"] if item["kind"] == "module" else None,
                quality={"Gold": "orange", "Purple": "purple", "Blue": "blue"}[item["quality"]], level=item["level"],
                max_level=item["max_level"], locked=False, discarded=False, equipped=False, equipped_character_id=None,
                names={}, suit_names={}, main_stats=[{"property_id": s["property_id"], "value": s["value"], "percent": s["is_percent"]}
                    for s in item["stats"] if s["stat_group"] == "main"],
                sub_stats=[{"property_id": s["property_id"], "value": s["value"], "percent": s["is_percent"]}
                    for s in item["stats"] if s["stat_group"] == "sub"]))
            rows.append(dict(uid_slot=slot, uid_serial=serial, kind=item["kind"], rotation=0,
                             target_row=item.get("target_row"), target_column=item.get("target_column")))
        assignments[cid] = rows
    account = original["selector"]["account_id"]
    with UserDataDao(path) as dao:
        assert dao.profile()["account_id"] == account
        snapshot = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 11, "sequence": 1, "observed_at_unix_ms": 1787000000000,
            "item_count": len(native), "items": native}})
        team = dao.save_team(TeamDraft("初始目标合成队伍", tuple(request["current"]["character_ids"])))
        for actor in request["current"]["build"]["characters"]:
            cid = actor["character_id"]
            dao.save_character_profile(character_id=cid, **actor["profile"])
            slot = dao.create_loadout_slot(cid, "目标图纸", slot_key="primary")
            dao.save_plan_to_slot(slot, name="冻结落位", assignments=assignments[cid], source_snapshot_id=snapshot,
                status="ready", score=1, payload={"schema": "game-observed-loadout-v1"})
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 0, "quantao_crit_damage": 0})
    inputs = TeamAxisCurrentInputService(account_id=account, generation=1, database_path=path,
        static_database_path=original["selector"]["static_database_path"], context_is_current=lambda: True)
    calls = []
    def client(**kwargs):
        calls.append(kwargs)
        return None
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path=original["selector"]["semantics_path"], client_factory=client)
    return service, inputs, team, calls, original, request


def target_for(bundle):
    return make_configuration_target(bundle, members=tuple(m.default for m in bundle.members),
        slot_ids=tuple((m.default.character_id, m.preferred_slot_id) for m in bundle.members), world=bundle.world)


def cleanup(owner, dialog=None):
    if dialog is not None:
        dialog.reject()
        dialog.deleteLater()
    owner.close()
    wait_for(lambda: not owner.is_running())
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application().processEvents()


def test_real_configuration_preserves_exact_layout_and_does_not_write_or_probe_component(tmp_path):
    service, inputs, team, calls, _original, request = configuration_case(tmp_path)
    before = inputs.freeze_current(team)
    bundle = service.load_configuration(team)
    assert calls == [] and inputs.freeze_current(team).sha256 == before.sha256
    assert all(t.layout is not None for t in bundle.templates), [(t.label, t.reason) for t in bundle.templates]
    target = target_for(bundle)
    assert target.revision == 1 and target.character_ids == team.character_ids
    assert target.world_bonus == TargetWorldBonus(20, .04)
    for source, layout in zip(request["current"]["build"]["characters"], target.presets[0].layouts):
        assert Counter((i["item_id"], i["target_row"], i["target_column"]) for i in source["equipment"] if i["kind"] == "module") == Counter(
            (p.item_id, p.row, p.column) for p in layout.pieces)
    seeds = configuration_seeds(target, bundle.rules)
    specs = {s.item_id: s for s in bundle.rules.items}
    for actor in seeds[0]["seeds"][0]:
        for piece in actor["pieces"]:
            assert len(piece["sub_property_ids"]) == specs[piece["item_id"]].sub_stat_count
            assert len(set(piece["sub_property_ids"])) == len(piece["sub_property_ids"])
            assert "uid_serial" not in piece
    with UserDataDao(inputs.database_path) as dao:
        assert dao.list_team_axis_targets(team.team_id) == ()
        assert dao.get_team_axis_selection(team.team_id) is None


def test_missing_anchor_is_reported_not_repositioned_and_growth_prerequisite_is_enforced(tmp_path):
    service, inputs, team, _calls, _original, _request = configuration_case(tmp_path)
    with UserDataDao(inputs.database_path) as dao:
        slot = next(s for s in dao.list_visible_loadout_slots_with_plans() if s["character_id"] == team.character_ids[0])
        plan = slot["current_plan"]
        rows = deepcopy(plan["assignments"])
        next(r for r in rows if r["kind"] == "module")["target_row"] = None
        dao.save_plan_to_slot(slot["slot_id"], name="缺少落位", source_snapshot_id=plan["source_snapshot_id"],
            assignments=rows, score=1, status="ready", payload=plan["payload"])
    bundle = service.load_configuration(team)
    assert any(t.layout is None and "锚点" in t.reason for t in bundle.templates)
    with pytest.raises(ValueError):
        target_for(bundle)


def test_skills_growth_are_frozen_and_cancellation_never_creates_target(tmp_path):
    service, inputs, team, calls, _original, _request = configuration_case(tmp_path)
    with pytest.raises(CancelledError):
        service.load_configuration(team, cancelled=lambda: True)
    assert calls == []
    bundle = service.load_configuration(team)
    actor = bundle.members[0]
    assert (actor.default.character_level, actor.default.breakthrough_stage) == max(actor.growth)
    assert all(any(n == level and b <= actor.default.breakthrough_stage and a <= actor.default.awakening_level
                   for n, b, a in dict(actor.skill_levels)[skill]) for skill, level in actor.default.skill_levels)
    modified = tuple([replace(actor.default, character_level=999), *(m.default for m in bundle.members[1:])])
    with pytest.raises(ValueError):
        make_configuration_target(bundle, members=modified,
            slot_ids=[(m.default.character_id, m.preferred_slot_id) for m in bundle.members], world=bundle.world)
    with UserDataDao(inputs.database_path) as dao:
        assert dao.get_team_axis_target(team.team_id) is None


def test_target_editor_cancel_has_no_effect_and_confirm_only_produces_draft(tmp_path):
    application()
    service, inputs, team, calls, _original, _request = configuration_case(tmp_path)
    bundle = service.load_configuration(team)
    dialog = GraduationTargetDialog(configuration=bundle)
    dialog.open()
    try:
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert dialog.target is None and calls == []
        dialog.open()
        QTest.mouseClick(dialog.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        assert dialog.target is not None and not dialog.isVisible()
        with UserDataDao(inputs.database_path) as dao:
            assert dao.get_team_axis_target(team.team_id) is None
    finally:
        dialog.reject()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_first_time_window_requires_axis_and_explicit_run_then_reports_component_pending(tmp_path):
    application()
    service, inputs, team, calls, _original, _request = configuration_case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    try:
        wait_for(lambda: not owner.is_running())
        assert not dialog.benchmark.isEnabled() and calls == []
        QTest.mouseClick(dialog.configure, Qt.LeftButton)
        wait_for(lambda: dialog._target_dialog is not None and not owner.is_running())
        editor = dialog._target_dialog
        QTest.mouseClick(editor.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        assert not dialog.benchmark.isEnabled() and calls == []
        dialog.set_reference_axis(ReportRange(1))
        assert dialog.benchmark.isEnabled() and owner.axis_selection is not None and calls == []
        QTest.mouseClick(dialog.benchmark, Qt.LeftButton)
        wait_for(lambda: not owner.is_running())
        assert owner.state == "component_pending" and len(calls) == 1
        with UserDataDao(inputs.database_path) as dao:
            assert dao.get_team_axis_target(team.team_id) is None
    finally:
        cleanup(owner, dialog)


def test_parent_window_waits_for_axis_picker_owner_before_destroying_child(tmp_path):
    application()
    service, _inputs, team, _calls, _original, _request = configuration_case(tmp_path)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    class Picker(QDialog):
        busy = True
        def is_running(self):
            return self.busy
        def reject(self):
            if not self.busy:
                super().reject()
    picker = Picker(dialog)
    dialog.attach_axis_picker(picker)
    try:
        wait_for(lambda: not owner.is_running())
        dialog.reject()
        assert dialog.isVisible()
        picker.busy = False
        picker.reject()
        assert not dialog.isVisible()
    finally:
        picker.busy = False
        picker.reject()
        cleanup(owner, dialog)


def test_graduation_axis_picker_does_not_apply_battle_enhancement(tmp_path, monkeypatch):
    from src.features.teams import controller as module
    application()
    service, _inputs, team, _calls, _original, _request = configuration_case(tmp_path)
    class Picker(QDialog):
        counts_changed = Signal(object)
        def __init__(self, *, team, parent, **_options):
            super().__init__(parent)
            self.selection = TeamReportMatch(team, SimpleNamespace(range=ReportRange(1)), MatchStatus.MATCHED)
        def is_running(self):
            return False
    monkeypatch.setattr(module, "TeamReportMatchDialog", Picker)
    root = TeamController(service_provider=lambda: None, matching_service_provider=lambda: object(),
        axis_graduation_service_provider=lambda: service)
    root._teams = {team.team_id: team}
    root.open_graduation(team.team_id)
    dialog = root._graduation_dialog
    try:
        wait_for(lambda: not root.is_running())
        dialog.axis_requested.emit()
        root._match_dialog.accept()
        assert dialog.reference_axis == ReportRange(1)
        assert not hasattr(root.page, "enhancement_requested")
    finally:
        cleanup(root.axis_graduation, dialog)
        root.page.close()
        root.page.deleteLater()
        root.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
