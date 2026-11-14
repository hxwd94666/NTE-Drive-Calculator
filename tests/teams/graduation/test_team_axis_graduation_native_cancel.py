# 真实分析EXE接收长轴请求后验证取消、代次重置和关闭的物理退场；不模拟传输或关闭无关窗口。
from dataclasses import replace
from threading import Event
import json
import subprocess
import time
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from shiboken6 import isValid

from tests.support.auto_sync_ui_fixture import application
from tests.teams.matching.team_report_fixture import wait_for
from src.domain.teams.report_matching import ReportRange
from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection, TeamAxisGraduationController
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.graduation.test_team_axis_graduation_long_axis import frozen_facts
from tests.teams.graduation.test_team_axis_graduation_long_axis import prepare
from tests.teams.graduation.test_team_axis_graduation_long_axis import repeated_axis


NTE_TEST_TIER = "core"


class OwnedProcesses:
    """只观察本例实际Popen及实际communicate；不替换结果、不阻塞管道、不发额外指令。"""

    def __init__(self):
        self.original = subprocess.Popen
        self.children, self.benchmarks = [], []
        self.received = Event()

    def start(self, *args, **kwargs):
        process = self.original(*args, **kwargs)
        self.children.append(process)
        communicate = process.communicate
        benchmark = False

        def observe(*positional, **named):
            nonlocal benchmark
            payload = named.get("input")
            if payload is not None:
                value = json.loads(payload)
                if value.get("batch_kind") == "team_axis_graduation_v1" and value.get("request", {}).get("operation") == "theoretical_benchmark":
                    benchmark = True
                    self.benchmarks.append(process)
            try:
                return communicate(*positional, **named)
            except subprocess.TimeoutExpired:
                # 请求已写完并关闭stdin，且实际子进程仍活着，才允许主线程触发取消。
                if benchmark and process.stdin.closed and process.poll() is None:
                    self.received.set()
                raise

        process.communicate = observe
        return process

    def reaped(self):
        return all(process.poll() is not None for process in self.children)


@pytest.mark.parametrize("action", ["cancel", "generation", "close"])
def test_real_long_axis_child_retires_before_owner_is_reused_or_destroyed(tmp_path, action):
    _unused, inputs, team, original, options = prepare(tmp_path)
    # 显式smoke门禁在Qt应用创建之前，默认收集不遗留本例的GUI生命周期。
    app = application()
    axis, count = repeated_axis(inputs, original["selector"]["battle_record_id"])
    active = {"value": True}
    current = TeamAxisCurrentInputService(account_id=inputs.account_id, generation=inputs.generation,
        database_path=inputs.database_path, static_database_path=inputs.static_database_path,
        context_is_current=lambda: active["value"])
    services = [TeamAxisGraduationService(current_inputs=current, semantics_path=original["selector"]["semantics_path"])]
    owner = TeamAxisGraduationController(service_provider=lambda: services[0])
    selection = AxisGraduationSelection(team, axis, options["target"], options["rules"])
    owner.set_selection(selection)
    delivered, errors, drained = [], [], []
    owner.preview_ready.connect(delivered.append)
    owner.error.connect(errors.append)
    owner.drained.connect(lambda: drained.append(True))
    observer = OwnedProcesses()
    with UserDataDao(inputs.database_path) as dao:
        before = frozen_facts(dao, axis.record_id)
        assert dao.list_team_axis_targets(team.team_id) == ()
    with patch("src.integrations.analysis_core.nte_analysis_core.subprocess.Popen", side_effect=observer.start):
        try:
            assert owner.run_benchmark(seeds=options["seeds"], max_evaluations=256, max_millis=15000)
            wait_for(observer.received.is_set, timeout=30)
            assert owner.is_running() and len(observer.benchmarks) == 1
            native = observer.benchmarks[0]
            assert native.poll() is None and native.stdin.closed
            threads = owner.findChildren(QThread)
            assert threads
            started = time.monotonic()
            if action == "cancel":
                owner.cancel()
            elif action == "generation":
                active["value"] = False
                owner.reset_account()
            else:
                owner.close()
            wait_for(lambda: not owner.is_running(), timeout=10)
            cancel_seconds = time.monotonic() - started
            assert observer.reaped() and native.poll() is not None
            assert all(not isValid(thread) or not thread.isRunning() for thread in threads)
            assert not delivered and not errors and drained and owner.preview is None and not owner.can_save
            with UserDataDao(inputs.database_path) as dao:
                assert frozen_facts(dao, axis.record_id) == before
                assert dao.get_team_axis_selection(team.team_id) is None
                assert dao.list_team_axis_targets(team.team_id) == ()
            if action == "close":
                assert owner.state == "closed"
                assert not owner.run_benchmark(seeds=options["seeds"], max_evaluations=2, max_millis=15000)
            else:
                active["value"] = True
                if action == "generation":
                    current = TeamAxisCurrentInputService(account_id=inputs.account_id, generation=inputs.generation + 1,
                        database_path=inputs.database_path, static_database_path=inputs.static_database_path,
                        context_is_current=lambda: active["value"])
                    services[0] = TeamAxisGraduationService(current_inputs=current,
                        semantics_path=original["selector"]["semantics_path"])
                owner.set_selection(replace(selection, axis=ReportRange(original["selector"]["battle_record_id"])))
                assert owner.run_benchmark(seeds=options["seeds"], max_evaluations=2, max_millis=15000)
                wait_for(lambda: not owner.is_running(), timeout=30)
                assert len(delivered) == 1 and not errors and observer.reaped()
                assert owner.preview.request.current.generation == current.generation
            result = {"fixture_kind": "synthetic_real_graduation_native_retirement_v1", "action": action,
                "events_in_cancelled_axis": count, "cancel_after_native_request_stdin_closed": True,
                "cancel_to_owner_drained_seconds": cancel_seconds,
                "owned_child_exit_codes": [p.returncode for p in observer.children],
                "all_owned_processes_reaped": observer.reaped(), "owned_qthreads_stopped": True,
                "new_work_succeeded": action != "close", "implicit_save": False,
                "actual_transport_and_model_replaced": False}
            (tmp_path / "native-retirement-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
            print(json.dumps(result, ensure_ascii=False))
        finally:
            owner.close()
            wait_for(lambda: not owner.is_running(), timeout=15)
            assert observer.reaped()
            owner.deleteLater()
            QCoreApplication.sendPostedEvents(owner, QEvent.DeferredDelete)
            app.processEvents()
