# 通过公开保存入口与真实SQLite验证冻结边界、原子首槽及提交和撤权竞态。
from concurrent.futures import CancelledError
from contextlib import contextmanager, closing
from copy import deepcopy
from dataclasses import replace
from src.services.allocation.context_builder import read_allocation_scoring_rules
from shutil import copyfile
from threading import Event
from types import SimpleNamespace

import pytest
from src.app import page_tasks
from src.features.weighted_allocation.runner import WeightedAllocationPreview, save_weighted_allocation_preview
from src.features.weighted_allocation.save_owner import WeightedOwnerIdentity, WeightedOperation, WeightedSaveOwner
from src.services.allocation import weighted_save
from src.services.allocation.inventory import legacy_shape_id
from src.services.allocation.locks import build_allocation_lock_snapshot
from src.domain.allocation.inputs import AllocationContext, InventorySnapshotReference, StaticDatasetReference
from src.services.allocation_solver import AllocationAssignment, AllocationSolveResult, RoleAllocationOption, UnifiedAllocation
from src.services.equipment.weighted_loadout_comparison_service import WeightedLoadoutComparison
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.equipment.test_role_loadout_slots import assignment, inventory_snapshot
from tests.allocation.static_release_helpers import publish_test_manifest, ROOT
from tests.allocation.test_weighted_save_owner import pump, qt_app  # noqa: F401 - 跨用例复用Qt实例fixture

NTE_TEST_TIER = "core"


TEST_SCORING_RULES = read_allocation_scoring_rules(ROOT / "config/stats.json")


@pytest.fixture
def scenario(tmp_path):
    source_static = ROOT / "data/game_static.sqlite3"
    static_path = tmp_path / "static.sqlite3"
    copyfile(source_static, static_path)
    database = tmp_path / "user.sqlite3"
    payload = deepcopy(inventory_snapshot())
    payload["params"]["generation"] = 91
    second = deepcopy(payload["params"]["items"][0])
    second["uid"]["serial"] = 12
    payload["params"]["items"].append(second)
    payload["params"]["item_count"] = 2
    with UserDataDao(database, account_id="a") as user, StaticGameDataDao(static_path) as static:
        snapshot_id = user.import_inventory_snapshot(payload)
        slot_id = user.create_loadout_slot(1003, "主力", slot_key="primary")
        plan_id = user.save_plan_to_slot(slot_id, name="历史", assignments=[assignment()],
            source_snapshot_id=snapshot_id, score=40,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "历史角色",
                     "assignment_scores": {"nte-module-22-11": 40}})
        lock_snapshot = build_allocation_lock_snapshot(user, inventory_snapshot_id=snapshot_id)
        static_summary = static.summary()
        summary = static_summary["dataset"]
        dataset = StaticDatasetReference(static_summary["schema_version"], summary["dataset_id"],
                                         summary["importer_version"], summary["built_at_utc"])
        shape = next(shape for shape in static.list_shapes() if shape["shape_id"] == "EquipmentGeometry_ZhiJiao1")
        cells = tuple((2 + int(cell["x"]), 2 + int(cell["y"])) for cell in shape["cells"])
    board = [[-1] * 5 for _ in range(5)]
    for row, column in cells:
        board[row-1][column-1] = legacy_shape_id("ZhiJiao1")
    item = AllocationAssignment((22, 11), "module", "cell3_style1_1_Orange", "Suit1", "ZhiJiao1",
                               cells, None, 55., (), (), False, 3)
    option = RoleAllocationOption(1003, 1, 55., (), (item,), tuple(map(tuple, board)), ())
    snapshot = InventorySnapshotReference(snapshot_id, "nte_core", 91, 2, 1, "fixture", 2, 2)
    context = AllocationContext("a", dataset, snapshot, 1, 1, "role_priority", "fixture", (), (), (), (), scoring_rules=TEST_SCORING_RULES)
    result = AllocationSolveResult(snapshot_id, 1, 1, "fixture", 1, (), UnifiedAllocation("role_priority", 55., (option,), (), ()))
    diff = dict(comparison_version=1, score_basis="calculation_weights", changed=False,
                added=[], removed=[], baseline_slot_id=slot_id, baseline_plan_id=plan_id, baseline_snapshot_id=snapshot_id)
    comparison = WeightedLoadoutComparison(slot_id, "主力", "primary", (), diff)
    current = [WeightedOwnerIdentity("a", 7, database.resolve())]
    owner = WeightedSaveOwner(lambda: current[0])
    ownership = owner.capture(current[0])
    release = publish_test_manifest(static_path)
    preview = WeightedAllocationPreview(result, dataset, "a", database, context,
        loadout_comparisons={1003: (comparison,)}, static_database_path=static_path,
        static_release=release, ownership=ownership, lock_snapshot=lock_snapshot)
    return SimpleNamespace(database=database, static=static_path, preview=preview, owner=owner,
                           current=current, slot_id=slot_id, plan_id=plan_id, tmp=tmp_path)


def rows(database):
    with UserDataDao(database) as dao:
        plans = dao.list_loadout_plans()
        slots = dao.list_visible_loadout_slots()
        assert not dao._db().in_transaction
        assert dao._db().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert dao._db().execute("PRAGMA foreign_key_check").fetchall() == []
    return plans, slots


def save(s, *, explicit, preview=None):
    preview = s.preview if preview is None else preview
    task = s.owner.begin_save(preview)
    return save_weighted_allocation_preview(preview, ownership=task,
        slot_ids_by_character={1003: s.slot_id} if explicit else None)


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-slots", "primary-overlay"])
def test_actual_public_service_dao_preserves_frozen_scores_and_history(scenario, explicit):
    s = scenario
    old, _slots = rows(s.database)
    saved = save(s, explicit=explicit)
    with UserDataDao(s.database) as user:
        current = user.get_loadout_slot(s.slot_id)["current_plan"]
        assert current["plan_id"] == saved[0]
        assert current["score"] == 55
        assert current["payload"]["assignment_scores"] == {"nte-module-22-11": 55}
        assert current["source_snapshot_id"] == s.preview.result.snapshot_id
        assert current["payload"]["last_diff"]["baseline_plan_id"] == s.plan_id
        historical = user.get_loadout_plan(s.plan_id)
        assert historical["score"] == old[0]["score"] == 40
        assert historical["payload"] == old[0]["payload"]
    assert s.preview.context.snapshot.generation == 91
    assert s.preview.ownership.identity.generation == 7
    rows(s.database)


@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("changed", ["generation", "database-profile", "locks", "static"])
def test_changed_frozen_boundary_keeps_old_plans(scenario, explicit, changed):
    s = scenario
    if changed == "generation":
        s.current[0] = replace(s.current[0], generation=9)
    elif changed == "database-profile":
        with UserDataDao(s.database) as user:
            user._db().execute("UPDATE database_profile SET account_id='b'")
            user._db().commit()
    elif changed == "locks":
        with UserDataDao(s.database) as user:
            borrowed = assignment()
            borrowed["uid_serial"] = 12
            locked = user.save_loadout_plan(name="保留", character_id=1004, assignments=[borrowed],
                source_snapshot_id=s.preview.result.snapshot_id, is_active=True,
                payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "保留角色"})
            user.set_allocation_lock(locked, True)
    else:
        stat = s.static.stat()
        import os
        import sqlite3
        with closing(sqlite3.connect(s.static)) as connection, connection:
            connection.execute("UPDATE character SET name_zh=replace(name_zh, '早', '晚') WHERE character_id=1003")
        os.utime(s.static, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        publish_test_manifest(s.static)
    before = rows(s.database)
    with pytest.raises((CancelledError, RuntimeError, UserDataValidationError)):
        save(s, explicit=explicit)
    assert rows(s.database) == before


def test_missing_first_slot_is_created_with_plan_and_cancel_creates_neither(scenario, monkeypatch):
    s = scenario
    # 无槽角色1004，默认主槽1003按原contract保留；取消也不释放其旧装备。
    option = replace(s.preview.result.unified.selected[0], character_id=1004)
    preview = replace(s.preview, result=replace(s.preview.result, unified=replace(
        s.preview.result.unified, selected=(option,))), loadout_comparisons={})
    original = rows(s.database)
    # 同一个公开保存入口的末端取消；slot与plan都已暂存，但均未提交。
    real_context = WeightedOperation.commit_context
    @contextmanager
    def revoke_at_final(operation):
        operation.cancelled.set()
        with real_context(operation):
            yield
    monkeypatch.setattr(WeightedOperation, "commit_context", revoke_at_final)
    task = s.owner.begin_save(preview)
    with pytest.raises(CancelledError):
        save_weighted_allocation_preview(preview, ownership=task, slot_ids_by_character={1004: None})
    assert rows(s.database) == original
    monkeypatch.setattr(WeightedOperation, "commit_context", real_context)
    task = s.owner.begin_save(preview)
    saved = save_weighted_allocation_preview(preview, ownership=task, slot_ids_by_character={1004: None})
    with UserDataDao(s.database) as user:
        slots = user.list_loadout_slots(1004)
        assert len(slots) == 1 and slots[0]["current_plan_id"] == saved[0]
    rows(s.database)


@pytest.mark.parametrize("explicit", [True, False], ids=["explicit-slots", "primary-overlay"])
@pytest.mark.parametrize("accepted", [False, True], ids=["revoke-before-accept", "revoke-after-accept"])
def test_real_sqlite_commit_fence_and_physical_finish(scenario, qt_app, monkeypatch, explicit, accepted):
    s = scenario
    before = rows(s.database)
    staged, release_commit, delivered, release_finish = (Event() for _ in range(4))
    final_context = WeightedOperation.commit_context
    user_factory = UserDataDao
    class CommitBarrier:
        def __init__(self, connection):
            self.connection = connection
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            staged.set()  # 已接受后的真实SQLite commit，锁仍由owner持有。
            assert release_commit.wait(3), "commit屏障未释放"
            return self.connection.commit()
    def dao_factory(path):
        dao = user_factory(path)
        dao._connection = CommitBarrier(dao._connection)
        return dao
    @contextmanager
    def before_accept(operation):
        staged.set()  # 两DAO暂存后，进入最终接受检查之前。
        assert release_commit.wait(3), "接受屏障未释放"
        with final_context(operation):
            yield
    if accepted:
        monkeypatch.setattr(weighted_save, "UserDataDao", dao_factory)
    else:
        monkeypatch.setattr(WeightedOperation, "commit_context", before_accept)
    real_run = page_tasks._PageWorker.run
    def delayed_finish(worker):
        real_run(worker)
        delivered.set()
        assert release_finish.wait(3), "物理finished屏障未释放"
    monkeypatch.setattr(page_tasks._PageWorker, "run", delayed_finish)
    task = s.owner.begin_save(s.preview)
    callbacks, transitions = [], []
    s.owner.submit(task, lambda: save_weighted_allocation_preview(s.preview, ownership=task,
                   slot_ids_by_character={1003: s.slot_id} if explicit else None),
                   lambda ids: callbacks.append(("saved", ids)), lambda error: callbacks.append(("error", error)), application_failed=lambda error: callbacks.append(("application", error)))
    try:
        pump(qt_app, staged.is_set)
        started = __import__("time").monotonic()
        assert not s.owner.request_stop()
        assert __import__("time").monotonic() - started < .2
        generation = s.current[0].generation
        def advance():
            transitions.append("once")
            s.current[0] = replace(s.current[0], generation=generation+1)
        s.owner.when_idle(advance)
        qt_app.processEvents()
        assert transitions == [] and s.current[0].generation == 7
        assert rows(s.database) == before
        release_commit.set()
        pump(qt_app, delivered.is_set)
        # result/failed已投递仍禁止推进；物理finished不是同一个事件。
        assert s.owner.is_running() and transitions == [] and callbacks == []
        assert s.current[0].generation == 7
        after_commit = rows(s.database)
        assert (after_commit != before) is accepted
        if accepted:
            with UserDataDao(s.database) as user:
                current = user.get_loadout_slot(s.slot_id)["current_plan"]
                assert current["score"] == 55 and user.get_loadout_plan(s.plan_id)["score"] == 40
    finally:
        release_commit.set()
        release_finish.set()
        pump(qt_app, s.owner.try_retire)
    assert transitions == ["once"] and s.current[0].generation == 8
    assert callbacks == []  # 已撤权的旧结果/错误不触发after_save或新账号页面。
    assert (rows(s.database) != before) is accepted




@pytest.mark.parametrize("replacement", [False, True])
def test_sqlite_commit_remains_saved_when_post_commit_action_raises(scenario, qt_app, replacement):
    from src.domain.allocation.inputs import AllocationRolePreference, RoleEquipmentConstraints, BlueprintCell
    from src.features.weighted_allocation.runner import confirm_weighted_allocation_save
    s = scenario
    role = AllocationRolePreference(1003, 0, 0, None, "none", None, (), (), (),
        RoleEquipmentConstraints(1003, tuple(BlueprintCell(*cell)
            for cell in s.preview.result.unified.selected[0].assignments[0].board_cells)))
    s.preview = replace(s.preview, context=replace(s.preview.context, roles=(role,)))
    option = s.preview.result.unified.selected[0]
    updated = replace(s.preview, result=replace(s.preview.result, unified=replace(
        s.preview.result.unified, selected=(replace(option, score=57., assignments=(
            replace(option.assignments[0], score=57.),)),), total_score=57.))) if replacement else s.preview
    operation = s.owner.begin_save(updated)
    targets = None if replacement else {1003: s.slot_id}
    delivered, actions, failures, application_failures = [], [], [], []
    def persist():
        ids = save_weighted_allocation_preview(updated, ownership=operation, slot_ids_by_character=targets)
        return confirm_weighted_allocation_save(updated, ids, ownership=operation, slot_ids_by_character=targets)
    def after_commit(value):
        delivered.append(value)
        actions.append("once")
        raise RuntimeError("controlled post-commit failure")
    s.owner.submit(operation, persist, after_commit, failures.append, application_failed=application_failures.append)
    pump(qt_app, s.owner.try_retire)
    with UserDataDao(s.database) as user:
        current = user.get_loadout_slot(s.slot_id)["current_plan"]
        assert current["plan_id"] != s.plan_id
        assert current["score"] == (57 if replacement else 55)
        assert user.get_loadout_plan(s.plan_id)["score"] == 40
        assert len(user.list_loadout_plans()) == 2
    advanced, = delivered
    assert advanced is not updated and advanced.result is updated.result and advanced.context is updated.context
    assert advanced.ownership is updated.ownership and advanced.lock_snapshot is updated.lock_snapshot
    assert advanced.save_receipt.plan_ids == (current["plan_id"],)
    assert advanced.slot_bindings[0].slot_id == s.slot_id
    assert advanced.slot_bindings[0].plan_id == current["plan_id"]
    assert actions == ["once"] and failures == []
    assert len(application_failures) == 1 and "controlled post-commit failure" in application_failures[0]
    rows(s.database)


@pytest.mark.parametrize("explicit", [True, False])
def test_frozen_non_target_lock_control_then_unlock_rejects_stale_preview(scenario, explicit):
    s = scenario
    with UserDataDao(s.database) as user:
        second = assignment()
        second["uid_serial"] = 12
        locked = user.save_loadout_plan(name="保留", character_id=1004, assignments=[second],
            source_snapshot_id=s.preview.result.snapshot_id, is_active=True,
            payload={"schema": "allocation-official-snapshot-v1", "source_role_name": "保留角色"})
        user.set_allocation_lock(locked, True)
        frozen = build_allocation_lock_snapshot(user, inventory_snapshot_id=s.preview.result.snapshot_id)
    preview = replace(s.preview, lock_snapshot=frozen)
    saved = save(s, explicit=explicit, preview=preview)
    with UserDataDao(s.database) as user:
        assert user.get_loadout_plan(locked)["allocation_locked"]
        assert user.get_loadout_slot(s.slot_id)["current_plan_id"] == saved[0]
        user.set_allocation_lock(locked, False)
    before = rows(s.database)
    with pytest.raises(UserDataValidationError, match="锁定状态"):
        save(s, explicit=explicit, preview=preview)
    assert rows(s.database) == before  # 保留用户已明确解锁的当前事实，不恢复旧锁。


