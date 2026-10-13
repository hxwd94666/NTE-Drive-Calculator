# 验证游戏配装本机导入的冻结评分、真实事务、取消和生产页面后台通道。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import sqlite3
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget

from src.domain.equipment.loadout_management import LoadoutAccount
from src.features.inventory.equipment import display_controller as display
from src.features.inventory.equipment.persistence_controller import EquipmentPersistenceController, LoadoutOperation
from src.optimizer.contracts import EQUIP_SHAPE_ID, EQUIP_SUB_STATS, EQUIP_UID, ROLE_EQUIPPED_DRIVES
from src.services.equipment.game_loadout_projection_service import (
    GameLoadoutImportRequest, GameLoadoutProjectionError, GameLoadoutProjectionService,
)
from src.services.equipment.loadout_management import (
    GameLoadoutMutation, LoadoutManagementService, freeze_game_loadout_request,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.equipment.test_loadout_management import app as app, spin
from tests.equipment.test_role_loadout_slots import inventory_snapshot

NTE_TEST_TIER = "core"


class StaticFixture:
    def list_characters(self):
        return [{"character_id": value, "name_zh": str(value)} for value in (1003, 1004)]

    def list_shapes(self):
        return [{"shape_id": "ZhiJiao1", "cells": [{"x": 0, "y": 0}, {"x": 0, "y": 1}, {"x": 1, "y": 0}]}]

    def get_equipment_plan(self, _character_id):
        return {"cells": [{"row": 1, "column": 1}, {"row": 1, "column": 2}, {"row": 2, "column": 1}]}


@pytest.fixture
def game(tmp_path):
    path = tmp_path / "game.sqlite3"
    native = inventory_snapshot()
    item = native["params"]["items"][0]
    native["params"]["items"] = []
    for character, serial in ((1003, 11), (1004, 12)):
        row = deepcopy(item)
        row["uid"]["serial"] = serial
        row.update(equipped=True, equipped_character_id=character,
                   equipped_character_uid={"slot": 5, "serial": character})
        native["params"]["items"].append(row)
    native["params"]["item_count"] = 2
    with UserDataDao(path, account_id="owner", account_name="fixture") as dao:
        dao.import_inventory_snapshot(native)
        roles = GameLoadoutProjectionService(dao, StaticFixture()).project_current().roles
    assert len(roles) == 2 and all(role.importable for role in roles)
    requests = tuple(freeze_game_loadout_request(GameLoadoutImportRequest(role, 20.0,
        {f"nte-module-22-{11 + index}": 20.0}, create_slot_name=role.role_name)) for index, role in enumerate(roles))
    return LoadoutAccount("owner", 1, path.resolve()), native, requests


def prepare(game, factory=UserDataDao):
    account, _native, requests = game
    operation = LoadoutOperation(account)
    service = LoadoutManagementService(factory)
    return service, operation, GameLoadoutMutation(service.prepare(operation), requests)


def test_import_creates_slots_and_preserves_frozen_score_source_without_static_io(game):
    service, operation, mutation = prepare(game)
    with pytest.raises(TypeError):
        mutation.requests[0].assignment_scores["nte-module-22-11"] = 99
    with pytest.raises(TypeError):
        mutation.requests[0].projection.assignments[0]["uid_serial"] = 99
    result = service.mutate(operation, mutation)
    with UserDataDao(operation.account.database_path) as dao:
        for plan_id, request in zip(result, mutation.requests):
            plan = dao.get_loadout_plan(plan_id)
            assert plan["source_snapshot_id"] == request.projection.snapshot_id
            assert plan["score"] == 20.0 and plan["payload"]["assignment_scores"] == dict(request.assignment_scores)
            assert dao.is_current_loadout_slot_plan(plan_id)
        assert len(dao.list_visible_loadout_slots_with_plans()) == 2


def test_batch_import_trigger_failure_rolls_back_new_slots_and_every_plan(game):
    service, operation, mutation = prepare(game)
    with sqlite3.connect(operation.account.database_path) as connection:
        connection.execute("CREATE TRIGGER abort_second_import BEFORE INSERT ON loadout_plan WHEN NEW.character_id = 1004 BEGIN SELECT RAISE(ABORT, 'second import failed'); END")
    with pytest.raises(GameLoadoutProjectionError, match="保存指定配装槽位失败") as error:
        service.mutate(operation, mutation)
    cause = error.value
    while cause.__cause__ is not None:
        cause = cause.__cause__
    assert isinstance(cause, sqlite3.IntegrityError) and str(cause) == "second import failed"
    with UserDataDao(operation.account.database_path) as dao:
        assert dao.list_loadout_plans() == []
        assert dao.list_visible_loadout_slots_with_plans() == []
        assert dao.current_inventory_snapshot_id() == mutation.requests[0].projection.snapshot_id


def test_import_cancel_before_commit_preserves_empty_original_and_creates_no_slot(game):
    service, operation, mutation = prepare(game)

    class Gate:
        account = operation.account
        checkpoint = operation.checkpoint

        @contextmanager
        def commit_context(self):
            operation.cancelled.set()
            operation.checkpoint()
            yield

    with pytest.raises((CancelledError, GameLoadoutProjectionError)):
        service.mutate(Gate(), mutation)
    with UserDataDao(operation.account.database_path) as dao:
        assert not dao.list_loadout_plans() and not dao.list_visible_loadout_slots_with_plans()


def test_import_rechecks_source_after_preflight_before_writer_transaction(game):
    account, native, _requests = game

    class ChangingDao(UserDataDao):
        def save_calculated_loadout_plans(self, plans, **gates):
            changed = deepcopy(native)
            changed["params"]["sequence"] += 1
            changed["params"]["items"][0]["level"] = 19
            self.import_inventory_snapshot(changed)
            return super().save_calculated_loadout_plans(plans, **gates)

    service, operation, mutation = prepare(game, ChangingDao)
    with pytest.raises(GameLoadoutProjectionError, match="背包快照已经变化"):
        service.mutate(operation, mutation)
    with UserDataDao(account.database_path) as dao:
        assert not dao.list_loadout_plans() and not dao.list_visible_loadout_slots_with_plans()


def test_import_confirmation_rechecks_current_slots_and_locked_uid_donor(game):
    account, _native, requests = game
    with UserDataDao(account.database_path) as dao:
        plan = dao.save_loadout_plan(name="locked donor", character_id=1005,
            assignments=requests[0].projection.assignments, source_snapshot_id=requests[0].projection.snapshot_id,
            status="ready", is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "1005"})
    service, operation, mutation = prepare(game)
    with UserDataDao(account.database_path) as dao:
        dao.set_allocation_lock(plan, True)
    with pytest.raises(GameLoadoutProjectionError, match="锁状态已变化"):
        service.mutate(operation, mutation)
    # 已经冻结为锁定的跨角色UID也不得借出，由既有保存事务保护。
    fresh = replace(mutation, snapshot=service.prepare(operation))
    with pytest.raises(GameLoadoutProjectionError, match="锁定"):
        service.mutate(operation, fresh)
    with UserDataDao(account.database_path) as dao:
        assert len(dao.list_loadout_plans()) == 1 and dao.is_current_loadout_slot_plan(plan)
        assert len(dao.list_visible_loadout_slots_with_plans()) == 1


@pytest.mark.parametrize("many", [False, True])
def test_real_import_actions_freeze_before_async_and_projection_failure_does_not_repeat(game, app, monkeypatch, many):
    account, _native, requests = game
    host = QWidget()
    host.equipment_persistence_controller = EquipmentPersistenceController(lambda: account, LoadoutManagementService(), host)
    host.equipment_presentation = SimpleNamespace(score_drive=lambda _sub, _shape, weights, _quality: weights["value"], shape_area=lambda *_args: 3)
    host.roles_db = {request.projection.role_name: {"weights": {"value": 20.0}} for request in requests}
    host._game_loadout_states = {request.projection.role_name: {
        "_game_projection": replace(request.projection, items=tuple(dict(item) for item in request.projection.items),
                                    assignments=tuple(dict(item) for item in request.projection.assignments)),
        "_game_importable": True, "_game_imported": False,
        ROLE_EQUIPPED_DRIVES: [{EQUIP_SHAPE_ID: "ZhiJiao1", EQUIP_SUB_STATS: {}, EQUIP_UID: next(iter(request.assignment_scores))}],
    } for request in requests}
    warnings = []
    host.invalidate_saved_equipment_cache = lambda: None
    host._refresh_equip = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failed"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[1:]))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    if many:
        display._import_all_game_loadouts(host)
    else:
        display._import_game_loadout(host, "1003")
    # 工作线程读取期间编辑展示及权重，不影响刚才冻结的正式UID与分数。
    host.roles_db["1003"]["weights"]["value"] = 999
    host._game_loadout_states["1003"]["_game_projection"].assignments[0]["uid_serial"] = 999
    spin(app, lambda: bool(warnings) and not host.equipment_persistence_controller.is_running())
    assert len(warnings) == 1 and warnings[0][0] == "配装已保存，页面刷新未完成"
    with UserDataDao(account.database_path) as dao:
        plans = dao.list_loadout_plans()
        assert len(plans) == (2 if many else 1)
        assert all(plan["score"] == 20.0 for plan in plans)
    host.equipment_persistence_controller.close()
    host.deleteLater()


@pytest.mark.parametrize("many", [False, True])
def test_import_releases_unlocked_current_donor_and_preserves_historical_source_scores(game, many):
    account, _native, requests = game
    first = requests[0]
    with UserDataDao(account.database_path) as dao:
        donor_id = dao.save_loadout_plan(name="donor", character_id=1005,
            assignments=first.projection.assignments, source_snapshot_id=first.projection.snapshot_id,
            status="ready", is_active=True, score=20.0,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "1005",
                     "assignment_scores": dict(first.assignment_scores)})
        before = dao.get_loadout_plan(donor_id)
    service, operation, mutation = prepare(game)
    result = service.mutate(operation, replace(mutation, requests=requests if many else requests[:1]))
    with UserDataDao(account.database_path) as dao:
        historical = dao.get_loadout_plan(donor_id)
        for field in ("source_snapshot_id", "score", "payload", "assignments"):
            assert historical[field] == before[field]
        current = dao.list_current_loadout_slot_plans()
        donor = next(row["plan"] for row in current if row["slot"]["character_id"] == 1005)
        assert donor["plan_id"] != donor_id and donor["is_active"] and donor["score"] == 0.0
        assert donor["source_snapshot_id"] == before["source_snapshot_id"]
        assert all(item["uid_slot"] == 0 for item in donor["assignments"])
        claimed = []
        for row in current:
            claimed.extend((item["uid_slot"], item["uid_serial"]) for item in row["plan"]["assignments"] if item["uid_slot"] > 0)
        assert len(claimed) == len(set(claimed)) == len(result)
        assert all(dao.get_loadout_plan(plan_id)["is_active"] for plan_id in result)
