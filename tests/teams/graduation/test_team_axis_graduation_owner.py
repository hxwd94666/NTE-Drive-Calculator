# 用本例独立owner验证串行worker、末次请求、账号撤权与物理退场；不关闭其他用例窗口或运行真实分析组件。
from dataclasses import replace
from copy import deepcopy
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent

from tests.support.auto_sync_ui_fixture import application
from tests.teams.matching.team_report_fixture import wait_for
from src.domain.teams.report_matching import ReportRange
from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection, TeamAxisGraduationController
from src.features.teams.controller import TeamController
from tests.teams.graduation.test_team_axis_graduation_service import fixture


NTE_TEST_TIER = "core"


class GatedService:
    def __init__(self, inner):
        self.inner = inner
        self.entered, self.release, self.completed = Event(), Event(), Event()
        self.block_first = True
        self.active = self.max_active = self.executions = 0

    def __getattr__(self, key):
        return getattr(self.inner, key)

    def solve_benchmark(self, task, *, cancelled=lambda: False):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.executions += 1
        try:
            # 首次故意不合作取消，验证owner即使拿到旧结果也不能发布；后续实际服务仍复核来源。
            if self.block_first:
                self.block_first = False
                result = self.inner.solve_benchmark(task, cancelled=lambda: False)
                self.entered.set()
                if not self.release.wait(5):
                    raise RuntimeError("SYNTHETIC_GATE_TIMEOUT")
                self.completed.set()
                return result
            return self.inner.solve_benchmark(task, cancelled=cancelled)
        finally:
            self.active -= 1


def setup(gated=False):
    application()
    service, inputs, client, values, _ = fixture()
    proxy = GatedService(service) if gated else service
    owner = TeamAxisGraduationController(service_provider=lambda: proxy)
    selection = AxisGraduationSelection(inputs.current.team, ReportRange(1), values["target"], values["rules"])
    owner.set_selection(selection)
    options = {key: values[key] for key in ("seeds", "max_evaluations", "max_millis")}
    return owner, proxy, inputs, client, selection, options


def cleanup(owner, proxy=None):
    owner.close()
    if isinstance(proxy, GatedService):
        proxy.release.set()
    wait_for(lambda: not owner.is_running())
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application().processEvents()


def test_no_implicit_work_and_three_explicit_operations_keep_one_fixed_reference():
    owner, proxy, _inputs, client, selection, options = setup()
    results, failures = [], []
    owner.preview_ready.connect(results.append)
    owner.error.connect(failures.append)
    try:
        assert owner.state == "no_benchmark" and client.operations == []
        assert not owner.update_current() and not owner.load_advice()
        assert owner.run_benchmark(**options)
        wait_for(lambda: not owner.is_running())
        reference = owner.preview.reference
        assert len(results) == 1 and owner.state == "search_limited_math"
        assert owner.update_current()
        wait_for(lambda: not owner.is_running())
        assert owner.load_advice()
        wait_for(lambda: not owner.is_running())
        assert len(results) == 3 and not failures
        assert owner.preview.reference == reference
        assert [v["operation"] for v in client.operations] == ["theoretical_benchmark", "current_evaluation", "recommendation_evaluation"]
        old = owner.preview
        owner.invalidate_current("unrelated")
        assert owner.preview is old
        owner.invalidate_current(selection.team.team_id)
        assert owner.state == "current_stale" and owner.preview is old and len(client.operations) == 3
        owner.set_selection(replace(selection, target=replace(selection.target, revision=2)))
        assert owner.preview is None and owner.state == "no_benchmark" and not owner.update_current()
    finally:
        cleanup(owner, proxy)


def test_latest_pending_request_wins_and_jobs_never_overlap_or_reread_mutable_seed():
    owner, proxy, _inputs, client, _selection, options = setup(True)
    results, failures = [], []
    owner.preview_ready.connect(results.append)
    owner.error.connect(failures.append)
    try:
        owner.run_benchmark(**options)
        wait_for(proxy.entered.is_set)
        options["seeds"][0]["seeds"].append(deepcopy(options["seeds"][0]["seeds"][0]))
        owner.run_benchmark(**options)
        options["seeds"][0]["seeds"].append(deepcopy(options["seeds"][0]["seeds"][0]))
        owner.run_benchmark(**options)
        options["seeds"].clear()
        assert owner.is_running()
        proxy.release.set()
        wait_for(lambda: not owner.is_running())
        assert proxy.executions == 2 and proxy.max_active == 1
        assert len(results) == 1 and not failures
        assert [len(v["seeds"][0]["seeds"]) for v in client.operations] == [1, 3]
        assert client.operations[-1]["seeds"]
    finally:
        cleanup(owner, proxy)


@pytest.mark.parametrize("action", ["cancel", "selection", "account", "close"])
def test_late_result_is_discarded_and_owner_stays_busy_until_physical_exit(action):
    owner, proxy, inputs, _client, selection, options = setup(True)
    results, drained = [], []
    owner.preview_ready.connect(results.append)
    owner.drained.connect(lambda: drained.append(True))
    try:
        owner.run_benchmark(**options)
        wait_for(proxy.entered.is_set)
        if action == "cancel":
            owner.cancel()
        elif action == "selection":
            owner.set_selection(replace(selection, target=replace(selection.target, revision=2)))
        elif action == "account":
            inputs.active = False
            owner.reset_account()
        else:
            owner.close()
            assert owner.state == "closing" and not owner.run_benchmark(**options)
        assert owner.is_running() and not drained
        proxy.release.set()
        wait_for(lambda: not owner.is_running())
        assert not results and owner.preview is None and drained
        if action == "close":
            assert owner.state == "closed"
    finally:
        cleanup(owner, proxy)


def test_expired_context_after_computation_is_dropped_even_without_a_reset_callback():
    owner, proxy, inputs, _client, _selection, options = setup(True)
    results = []
    owner.preview_ready.connect(results.append)
    try:
        owner.run_benchmark(**options)
        wait_for(proxy.entered.is_set)
        proxy.release.set()
        assert proxy.completed.wait(2)
        inputs.active = False
        wait_for(lambda: not owner.is_running())
        assert not results and owner.preview is None and owner.state == "unselected"
    finally:
        cleanup(owner, proxy)


def test_component_pending_during_advice_does_not_erase_previous_mathematical_preview():
    owner, proxy, _inputs, client, _selection, options = setup()
    failures = []
    owner.error.connect(failures.append)
    try:
        owner.run_benchmark(**options)
        wait_for(lambda: not owner.is_running())
        old = owner.preview
        client.supports_team_axis_graduation_advice = False
        owner.load_advice()
        wait_for(lambda: not owner.is_running())
        assert owner.state == "component_pending" and failures and owner.preview is old
        assert len(client.operations) == 1
    finally:
        cleanup(owner, proxy)


def test_parent_team_lifecycle_counts_graduation_worker_and_clears_account_preview():
    application()
    service, inputs, _client, values, _ = fixture()
    proxy = GatedService(service)
    team = TeamController(service_provider=lambda: None, axis_graduation_service_provider=lambda: proxy)
    owner = team.axis_graduation
    owner.set_selection(AxisGraduationSelection(inputs.current.team, ReportRange(1), values["target"], values["rules"]))
    try:
        owner.run_benchmark(**{key: values[key] for key in ("seeds", "max_evaluations", "max_millis")})
        wait_for(proxy.entered.is_set)
        assert team.is_running()
        team.reset_account()
        assert team.is_running() and owner.preview is None
        proxy.release.set()
        wait_for(lambda: not team.is_running())
        assert owner.state == "unselected"
    finally:
        cleanup(owner, proxy)
        team.page.close()
        team.page.deleteLater()
        team.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        application().processEvents()



def test_new_provider_failure_also_revokes_old_inflight_result():
    application()
    service, inputs, _client, values, _ = fixture()
    proxy = GatedService(service)
    broken = False
    def provider():
        if broken:
            raise RuntimeError("SYNTHETIC_PROVIDER_FAILURE")
        return proxy
    owner = TeamAxisGraduationController(service_provider=provider)
    owner.set_selection(AxisGraduationSelection(inputs.current.team, ReportRange(1), values["target"], values["rules"]))
    options = {key: values[key] for key in ("seeds", "max_evaluations", "max_millis")}
    results = []
    owner.preview_ready.connect(results.append)
    try:
        owner.run_benchmark(**options)
        wait_for(proxy.entered.is_set)
        broken = True
        assert not owner.run_benchmark(**options) and owner.state == "failed"
        proxy.release.set()
        wait_for(lambda: not owner.is_running())
        assert not results and owner.preview is None and owner.state == "failed"
    finally:
        cleanup(owner, proxy)


def test_reentrant_close_during_state_notification_does_not_leave_pending_job():
    owner, proxy, _inputs, client, _selection, options = setup()
    owner.state_changed.connect(lambda state: owner.close() if state == "cancelled" else None)
    try:
        assert not owner.run_benchmark(**options)
        assert owner.state == "closed" and not owner.is_running() and client.operations == []
    finally:
        cleanup(owner, proxy)



def test_reentrant_close_on_result_state_revokes_preview_signal():
    owner, proxy, _inputs, _client, _selection, options = setup()
    results = []
    owner.preview_ready.connect(results.append)
    owner.state_changed.connect(lambda state: owner.close() if state == "search_limited_math" else None)
    try:
        owner.run_benchmark(**options)
        wait_for(lambda: not owner.is_running())
        assert not results and owner.preview is None and owner.state == "closed"
    finally:
        cleanup(owner, proxy)


def test_reentrant_close_during_selection_change_keeps_closed_owner_empty():
    owner, proxy, _inputs, _client, selection, _options = setup()
    owner.state_changed.connect(lambda state: owner.close() if state == "cancelled" else None)
    try:
        owner.set_selection(replace(selection, target=replace(selection.target, revision=2)))
        assert owner.state == "closed" and owner.preview is None and not owner.update_current()
    finally:
        cleanup(owner, proxy)
