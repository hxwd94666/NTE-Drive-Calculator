# 通过公共run/save和真实临时SQLite覆盖同尺寸同mtime换字节、持pin与原子拒绝。
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import os

import pytest
from src.features.weighted_allocation import runner
from src.features.weighted_allocation.save_owner import WeightedOperation
from src.services.allocation import plan_save, weighted_save
from src.services.allocation.locks import build_allocation_lock_snapshot
from src.services.equipment.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.release_freeze_inputs import PublicReleaseInputs
from src.integrations.static_catalog_release import StaticCatalogReleaseReader
from tests.allocation.test_weighted_save_transactions import rows

NTE_TEST_TIER = "core"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ReleaseScenario(PublicReleaseInputs):
    def __init__(self, root):
        super().__init__(root)
        self.b = self.other
        self.release_a = StaticCatalogReleaseReader().freeze(self.static)
        self.release_b = StaticCatalogReleaseReader().freeze(self.b)
        self.bytes_a, self.bytes_b = self.static.read_bytes(), self.b.read_bytes()
        self.manifest_b = self.b.with_name("manifest.json").read_bytes()
        self.snapshot_id, self.identity = self.snapshot, self.current[0]
        self.request = runner.WeightedAllocationRequest(self.database, self.snapshot, self.profile["profile_id"],
            self.profile["version"]["version_number"], 1, include_role_top_k=False,
            static_database_path=self.static, scoring_rules_path=self.rules,
            ownership=self.owner.capture(self.identity))
        self.swaps = []

    def swap(self, *, manifest=True):
        stat = self.static.stat()
        # actor只替换同一当前来源的真实字节；不改Preview、reader结果或权限判定。
        staging = self.static.with_name(".next-static.tmp")
        staging.write_bytes(self.bytes_b)
        os.replace(staging, self.static)
        os.utime(self.static, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert (self.static.stat().st_size, self.static.stat().st_mtime_ns) == (stat.st_size, stat.st_mtime_ns)
        assert digest(self.static) == digest(self.b)
        if manifest:
            self.static.with_name("manifest.json").write_bytes(self.manifest_b)

    def attempt_swap_under_pin(self):
        try:
            self.swap()
        except PermissionError:
            self.swaps.append("blocked_by_real_pin")
            assert digest(self.static) == hashlib.sha256(self.bytes_a).hexdigest()
        else:
            self.swaps.append("replaced")

    def calculate(self):
        return runner.run_weighted_allocation(replace(self.request, ownership=self.owner.capture(self.identity)))

    def save(self, preview, *, explicit):
        task = self.owner.begin_save(preview)
        targets = self.slots if explicit else None
        return runner.save_weighted_allocation_preview(preview, ownership=task, slot_ids_by_character=targets)


@pytest.fixture
def scenario(tmp_path):
    scenario = ReleaseScenario(tmp_path)
    yield scenario
    assert scenario.owner.close()


def test_before_run_uses_complete_current_b_and_detached_a_preview(scenario):
    s = scenario
    first = s.calculate()
    assert first.result.unified.selected, first.result.unified.explanation
    original_score = first.result.unified.total_score
    assert tuple(row.character_id for row in first.result.unified.selected) == (2001, 2002)
    assert dict(first.context.roles[0].effective_property_weights)["AtkUp"] == 1.
    assert first.role_details[2001]["static_release"].sha256 == s.release_a.sha256
    s.swap()
    second = s.calculate()
    assert second.static_release.sha256 == digest(s.static) == s.release_b.sha256
    assert second.static_release.database_path == s.static
    assert dict(second.context.roles[0].effective_property_weights)["AtkUp"] == 3.
    assert second.result.unified.total_score > original_score
    assert first.result.unified.total_score == original_score
    assert dict(first.context.roles[0].effective_property_weights)["AtkUp"] == 1.


@pytest.mark.parametrize("explicit", [False, True], ids=["primary", "slots"])
def test_same_size_mtime_before_save_requires_recompute_without_partial_write(scenario, explicit):
    s = scenario
    preview = s.calculate()
    s.save(preview, explicit=explicit)
    before = rows(s.database)
    s.swap()
    with pytest.raises(RuntimeError):
        s.save(preview, explicit=explicit)
    assert rows(s.database) == before
    fresh = s.calculate()
    assert fresh.result.unified.total_score != preview.result.unified.total_score
    ids = s.save(fresh, explicit=explicit)
    with UserDataDao(s.database) as user:
        assert set(ids) <= {row["plan_id"] for row in user.list_loadout_plans()}
        assert all(user.get_loadout_plan(pid)["payload"]["static_dataset"]["dataset_id"] == fresh.static_dataset.dataset_id for pid in ids)


@pytest.mark.parametrize("when", ["during-pure", "after-solve"])
def test_real_pin_spans_pure_solver_and_after_solve(scenario, monkeypatch, when):
    s = scenario
    real = runner.solve_allocation_context
    observed = []
    def actor(context, **kwargs):
        if when == "during-pure":
            s.attempt_swap_under_pin()
        result = real(context, **kwargs)
        if when == "after-solve":
            s.attempt_swap_under_pin()
        observed.append(result.unified.total_score)
        return result
    monkeypatch.setattr(runner, "solve_allocation_context", actor)
    preview = s.calculate()
    assert s.swaps == ["blocked_by_real_pin"]
    assert observed == [preview.result.unified.total_score]
    assert preview.static_release.sha256 == digest(s.static) == s.release_a.sha256


@pytest.mark.parametrize("when", ["during-pure", "after-solve"])
def test_swap_in_pure_solve_rejects_requested_source_change_without_preview(scenario, monkeypatch, when):
    s = scenario
    real = runner.solve_allocation_context
    before = rows(s.database)
    def actor(context, **kwargs):
        if when == "during-pure":
            s.swap()  # 同原公共case的真实os.replace；pin拒绝后原RuntimeError传播。
        result = real(context, **kwargs)
        if when == "after-solve":
            s.swap()
        return result
    monkeypatch.setattr(runner, "solve_allocation_context", actor)
    with pytest.raises(RuntimeError):
        s.calculate()
    assert rows(s.database) == before
    assert digest(s.static) == s.release_a.sha256
    s.swap()  # 失败已释放pin；不是改变actor输入或fallback读取B来求绿。


def test_changed_manifest_after_solve_discards_preview_and_releases_pin(scenario, monkeypatch):
    s = scenario
    real = runner.solve_allocation_context
    def actor(context, **kwargs):
        result = real(context, **kwargs)
        s.static.with_name("manifest.json").write_bytes(s.manifest_b)
        return result
    monkeypatch.setattr(runner, "solve_allocation_context", actor)
    before = rows(s.database)
    with pytest.raises(RuntimeError):
        s.calculate()
    assert rows(s.database) == before
    s.swap()  # 异常退场已释放真实pin，不允许一个失败任务留下来源锁。


@pytest.mark.parametrize("explicit", [False, True], ids=["primary", "slots"])
@pytest.mark.parametrize("when", ["prepare", "pre-commit"])
def test_changed_source_during_save_rejects_whole_transaction(scenario, monkeypatch, explicit, when):
    s = scenario
    preview = s.calculate()
    before = rows(s.database)
    changed = []
    def actor():
        if not changed:
            s.attempt_swap_under_pin()
            changed.append(when)
            s.static.with_name("manifest.json").write_bytes(s.manifest_b)
    if when == "prepare":
        real = SavedStateLoadoutBridge.prepare_role_plan
        def prepare(bridge, **kwargs):
            result = real(bridge, **kwargs)
            actor()
            return result
        monkeypatch.setattr(SavedStateLoadoutBridge, "prepare_role_plan", prepare)
    else:
        real = WeightedOperation.commit_context
        @contextmanager
        def pre_commit(operation):
            actor()
            with real(operation):
                yield
        monkeypatch.setattr(WeightedOperation, "commit_context", pre_commit)
    with pytest.raises(RuntimeError):
        s.save(preview, explicit=explicit)
    assert changed == [when] and s.swaps == ["blocked_by_real_pin"]
    assert rows(s.database) == before
    s.swap()  # 保存异常同样释放来源pin；旧方案/槽位事实完整保留。


def test_old_preview_without_release_and_cancel_require_recompute(scenario, monkeypatch):
    s = scenario
    preview = s.calculate()
    before = rows(s.database)
    missing = replace(preview, static_release=None)
    with pytest.raises(RuntimeError, match="重新计算"):
        s.save(missing, explicit=True)
    assert rows(s.database) == before
    real = runner.solve_allocation_context
    request = replace(s.request, ownership=s.owner.capture(s.identity))
    # 真正运行的token撤权，纯求解器/返回检查传播原取消，而非静态默认值回退。
    def revoke(context, **kwargs):
        request.ownership.cancelled.set()
        return real(context, **kwargs)
    monkeypatch.setattr(runner, "solve_allocation_context", revoke)
    with pytest.raises(CancelledError):
        runner.run_weighted_allocation(request)
    assert rows(s.database) == before
    s.swap()


@pytest.mark.parametrize("changed", [False, True])
def test_classic_save_uses_same_release_and_final_commit_recheck(scenario, changed):
    s = scenario
    preview = s.calculate()
    with UserDataDao(s.database) as user:
        locks = build_allocation_lock_snapshot(user, inventory_snapshot_id=s.snapshot_id)
    option = next(option for option in preview.result.unified.selected if option.character_id == 2001)
    row = {"role_name": "Byte-A-2001", "role_state": weighted_save.role_state(option),
           "character_id": 2001, "snapshot_id": s.snapshot_id, "name": "fixture",
           "score": option.score, "slot_id": s.slots[2001], "payload": {}}
    stat = s.static.stat()
    before = rows(s.database)
    def progress(stage):
        if changed and stage[0].startswith("正在写入"):
            s.attempt_swap_under_pin()
            s.static.with_name("manifest.json").write_bytes(s.manifest_b)
    kwargs = dict(database_path=s.database, static_database_path=s.static,
                  static_identity=(s.static, preview.static_dataset.dataset_id, (stat.st_size, stat.st_mtime_ns)),
                  static_release=preview.static_release, lock_snapshot=locks, rows=[row], checkpoint=lambda: None, progress=progress)
    if changed:
        with pytest.raises(RuntimeError):
            plan_save.save_allocation_plans(**kwargs)
        assert rows(s.database) == before
        assert s.swaps == ["blocked_by_real_pin"]
    else:
        assert plan_save.save_allocation_plans(**kwargs) == 1
        assert rows(s.database) != before
