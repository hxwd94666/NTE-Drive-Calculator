# 独立持有毕业任务、取消与末次请求投影；账号或选择变化撤权，物理线程退场前不释放owner。
from concurrent.futures import CancelledError
from dataclasses import asdict, dataclass, replace
import json
from threading import Event
from uuid import uuid4

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from src.domain.teams.graduation.axis_graduation_reference import FrozenBenchmarkReference
from src.domain.teams.graduation.axis_graduation_snapshot import FrozenAxisContextSnapshot
from src.domain.teams.graduation.axis_graduation_rules import TheoryEquipmentRules
from src.domain.teams.graduation.axis_graduation_target import GraduationTarget
from src.domain.teams.graduation.axis_graduation_configuration import configuration_seeds
from src.features.teams.graduation.axis_graduation_presentation import axis_graduation_display, axis_history_view, empty_axis_display, validity_display
from src.domain.teams.graduation.axis_graduation_validity import (
    AxisPlanChangeScope,
    AxisResultValidity,
)
from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.domain.teams.models import Team
from src.domain.teams.report_matching import ReportRange
from src.integrations.battle_analysis.graduation.team_axis_graduation_archive import saved_axis_context
from src.services.teams.axis_graduation.service import AxisGraduationComponentPending, AxisGraduationPreview


@dataclass(frozen=True, slots=True)
class AxisGraduationSelection:
    team: Team
    axis: ReportRange
    target: GraduationTarget
    rules: TheoryEquipmentRules

    def __post_init__(self):
        if (not isinstance(self.team, Team) or not isinstance(self.axis, ReportRange)
                or not isinstance(self.target, GraduationTarget) or not isinstance(self.rules, TheoryEquipmentRules)):
            raise ValueError("轴伤选择需要正式队伍、范围、目标和规则")
        self.target.verify_rules(self.rules)
        if self.team.team_id != self.target.team_id or self.team.character_ids != self.target.character_ids:
            raise ValueError("毕业目标与当前队伍不一致")


@dataclass(frozen=True, slots=True)
class _Job:
    sequence: int
    selection: AxisGraduationSelection | Team
    service: object
    operation: str
    request_id: str
    options_json: str
    reference: FrozenBenchmarkReference | None
    axis_snapshot: FrozenAxisContextSnapshot | None
    preview: AxisGraduationPreview | None


def _team(selected):
    return selected.team if isinstance(selected, AxisGraduationSelection) else selected


@dataclass(frozen=True, slots=True)
class _Delivery:
    kind: str
    state: str
    preview: AxisGraduationPreview | None = None
    reference: FrozenBenchmarkReference | None = None
    snapshot: FrozenAxisContextSnapshot | None = None
    history: object = None
    selection: AxisGraduationSelection | None = None
    display: object = None
    configuration: object = None
    validity: AxisResultValidity | None = None
    source_scope: AxisPlanChangeScope | None = None


class _GraduationWorker(QThread):
    delivered = Signal(object)
    failed = Signal(object)

    def __init__(self, job, token, parent):
        super().__init__(parent)
        self.job, self.token = job, token

    def run(self):
        job, selected, cancelled = self.job, self.job.selection, self.token.is_set
        try:
            options = json.loads(job.options_json)
            if job.operation == "validity":
                validity = job.service.check_validity(selected.team, axis=selected.axis, target=selected.target,
                    rules=selected.rules, preview=job.preview, cancelled=cancelled, **options)
                job.service.checkpoint(cancelled)
                if not cancelled():
                    self.delivered.emit(_Delivery("validity", validity.state, validity=validity))
                return
            if job.operation == "configure":
                configuration = job.service.load_configuration(_team(selected), cancelled=cancelled)
                job.service.checkpoint(cancelled)
                if not cancelled():
                    self.delivered.emit(_Delivery("configuration", "configuration_ready", configuration=configuration))
                return
            if job.operation in {"history", "save", "restore"}:
                team = _team(selected)
                restored = record = None
                if job.operation == "restore":
                    history = job.service.activate_saved_result(team, cancelled=cancelled, **options)
                    record = history.selected_result
                    axis, target, rules = saved_axis_context(record)
                    restored = AxisGraduationSelection(team, axis, target, rules)
                else:
                    if job.operation == "save":
                        job.service.save_completed_result(job.preview, cancelled=cancelled, **options)
                        options = {}
                    history = job.service.load_history(team, cancelled=cancelled, **options)
                view = axis_history_view(history)
                display = view.display
                reference = record.reference if record else None
                snapshot = record.to_payload()["response"]["axis_snapshot"] if record else None
                if snapshot is not None:
                    from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
                    snapshot = freeze_axis_context_snapshot(snapshot)
                job.service.checkpoint(cancelled)
                if not cancelled():
                    self.delivered.emit(_Delivery(job.operation,
                        {"history": "history_ready", "save": "saved_math", "restore": "historical_math"}[job.operation],
                        reference=reference, snapshot=snapshot, history=view, selection=restored, display=display,
                        source_scope=AxisPlanChangeScope.from_request(record.to_payload()["request"]) if record else None))
                return
            common = dict(target=selected.target, rules=selected.rules, request_id=job.request_id, cancelled=cancelled)
            if job.operation == "benchmark":
                if "seeds" not in options:
                    options["seeds"] = configuration_seeds(selected.target, selected.rules)
                task = job.service.prepare_benchmark(selected.team, selected.axis, **common, **options)
                preview = job.service.solve_benchmark(task, cancelled=cancelled)
            else:
                reference = job.reference
                if job.operation == "equipment_advice":
                    prepare, execute = job.service.prepare_equipment_advice, job.service.evaluate_equipment_advice
                else:
                    prepare = job.service.prepare_current if job.operation == "current" else job.service.prepare_advice
                    execute = job.service.evaluate_current if job.operation == "current" else job.service.evaluate_advice
                task = prepare(selected.team, selected.axis, reference=reference, axis_snapshot=job.axis_snapshot, **common)
                preview = execute(task, cancelled=cancelled)
            if not isinstance(preview, AxisGraduationPreview) or preview.request != task:
                raise ValueError("轴伤任务返回了其他请求的预览")
            job.service.checkpoint(cancelled)
            # 大正文解析留在worker；GUI只消费状态标签和不可变预览，不在槽函数重新遍历完整结果。
            response = preview.response
            reference, axis_snapshot = preview.reference, preview.axis_snapshot
            if response["status"] == "outside_target":
                state = "outside_target"
            elif response["current"]["axis_evaluation"]["status"] != "complete":
                state = "model_incomplete"
            elif reference is not None and reference.to_payload()["payload"]["search"]["stop_reason"] != "neighborhood_complete":
                state = "search_limited_math"
            else:
                state = "mathematical_preview"
            if not cancelled():
                self.delivered.emit(_Delivery("preview", state, preview, reference, axis_snapshot,
                    display=axis_graduation_display(response, selected.target.to_payload(), rules=asdict(selected.rules)),
                    source_scope=AxisPlanChangeScope.from_request(task.payload)))
        except CancelledError:
            self.token.set()
        except Exception as error:
            if not cancelled():
                self.failed.emit(error)


class TeamAxisGraduationController(QObject):
    state_changed = Signal(str)
    preview_ready = Signal(object)
    display_ready = Signal(object)
    history_ready = Signal(object)
    result_saved = Signal()
    configuration_ready = Signal(object)
    error = Signal(str)
    drained = Signal()

    def __init__(self, *, service_provider, parent=None):
        super().__init__(parent)
        self._provider = service_provider
        self._selection = None
        self._preview = self._reference = self._axis_snapshot = None
        self._history = self._display = None
        self._base_display = self._validity = self._source_scope = self._event_service = None
        self._saved_evaluation_id = None
        self._configuration = None
        self._preview_stale = False
        self._saved_reference_id = None
        self._worker = self._pending = None
        self._sequence = 0
        self._closed = False
        self._state = "unselected"

    @property
    def state(self):
        return self._state

    @property
    def preview(self):
        return self._preview

    @property
    def history(self):
        return self._history

    @property
    def display(self):
        return self._display

    @property
    def team(self):
        return _team(self._selection)

    @property
    def axis_selection(self):
        return self._selection if isinstance(self._selection, AxisGraduationSelection) else None

    @property
    def configuration(self):
        return self._configuration

    @property
    def can_update(self):
        return (isinstance(self._selection, AxisGraduationSelection) and self._reference is not None and self._axis_snapshot is not None
                and (self._validity is None or self._validity.benchmark == "matching" and self._validity.component == "ready"))

    @property
    def can_check_validity(self):
        return (isinstance(self._selection, AxisGraduationSelection) and self._reference is not None
                and (self._preview is not None or self._saved_evaluation_id is not None))

    @property
    def can_load_equipment_advice(self):
        return self.can_update and self._reference.benchmark_id == self._saved_reference_id

    @property
    def can_save(self):
        return self._preview is not None and self._history is not None and not self._preview_stale and not self.is_running()

    @property
    def current_stale(self):
        return self._preview_stale

    def _set_state(self, value):
        if value != self._state:
            self._state = value
            self.state_changed.emit(value)

    def is_running(self):
        return self._worker is not None or self._pending is not None

    def set_selection(self, selection):
        if self._closed:
            raise RuntimeError("毕业任务owner已关闭")
        if not isinstance(selection, AxisGraduationSelection):
            raise ValueError("毕业任务缺少完整选择")
        self._replace_selection(selection)

    def set_team(self, team):
        if self._closed or not isinstance(team, Team):
            raise ValueError("历史任务需要当前正式队伍")
        if team != self.team:
            self._replace_selection(team)

    def _replace_selection(self, selection):
        if selection != self._selection:
            expected_sequence = self._sequence + 1
            self.cancel()
            if self._closed or self._sequence != expected_sequence:
                return
            if _team(selection) != self.team:
                self._history = None
                self._configuration = None
            self._selection, self._preview, self._reference, self._axis_snapshot = selection, None, None, None
            self._saved_reference_id = None
            self._saved_evaluation_id = None
            self._base_display = self._validity = self._source_scope = self._event_service = None
            self._display = empty_axis_display() if isinstance(selection, AxisGraduationSelection) else None
            self._preview_stale = False
            self._set_state("no_benchmark")
            if not self._closed and self._sequence == expected_sequence:
                self.display_ready.emit(self._display or empty_axis_display())

    def _submit(self, operation, *, options=None, reference=None, axis_snapshot=None, preview=None):
        if self._closed or self._selection is None:
            return False
        # UI草稿在提交时冻结，不等旧worker退出后再读可变种子；provider只构造窄服务，不启动求解。
        frozen = json.dumps(options or {}, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        selected = self._selection
        # 新意图先撤销旧结果发布权；provider故障也不能让旧成功回调覆盖新故障。
        self.cancel()
        sequence = self._sequence
        if self._closed or self._selection != selected:
            return False
        try:
            service = self._provider()
            service.checkpoint()
        except CancelledError:
            self.reset_account()
            return False
        except Exception as error:
            self._set_state("failed")
            self.error.emit(str(error))
            return False
        if self._closed or sequence != self._sequence or self._selection != selected:
            return False
        job = _Job(sequence, selected, service, operation, uuid4().hex, frozen, reference, axis_snapshot, preview)
        self._pending = job
        self._set_state("queued")
        if self._worker is None:
            self._start()
        return True

    def run_benchmark(self, *, seeds, max_evaluations, max_millis):
        if not isinstance(self._selection, AxisGraduationSelection):
            return False
        return self._submit("benchmark", options=dict(seeds=seeds, max_evaluations=max_evaluations, max_millis=max_millis))

    def configure_target(self):
        return self._submit("configure")

    def run_target_benchmark(self, *, max_evaluations, max_millis):
        if not isinstance(self._selection, AxisGraduationSelection):
            return False
        return self._submit("benchmark", options=dict(max_evaluations=max_evaluations, max_millis=max_millis))

    def load_history(self, **page):
        return self._submit("history", options=page)

    def check_validity(self):
        if not self.can_check_validity:
            return False
        self.mark_unverified()
        return self._submit("validity", preview=self._preview,
                            options={} if self._preview is not None else {"evaluation_id": self._saved_evaluation_id})

    def mark_unverified(self, *, preserve_benchmark=False):
        if self._base_display is None:
            return
        self._preview_stale = True
        # 重开窗口/核对被取消都不把上次读取的结果自动声明为当前；保留已证实的基准过期结论。
        previous = self._validity
        self._validity = AxisResultValidity(previous.benchmark if previous else
            "matching" if preserve_benchmark else "unverified", "unverified",
            previous.component if previous else "ready", ("尚未核对本次当前输入；历史仍可读取",))
        self._display = validity_display(self._base_display, self._validity)
        sequence, display = self._sequence, self._display
        self._set_state(self._validity.state)
        if not self._closed and self._sequence == sequence and self._display is display:
            self.display_ready.emit(display)

    def save_preview(self, *, select=True):
        if not self.can_save:
            return False
        return self._submit("save", preview=self._preview, options=dict(select=select,
            expected_target_revision=self._history.target_revision,
            expected_selection_revision=self._history.selection_revision))

    def restore_saved(self, evaluation_id):
        if self._history is None:
            return False
        return self._submit("restore", options=dict(evaluation_id=evaluation_id,
                            expected_revision=self._history.selection_revision))

    def update_current(self):
        if not self.can_update:
            return False
        reference = self._reference
        return self._submit("current", reference=reference, axis_snapshot=self._axis_snapshot) if reference is not None and self._axis_snapshot is not None else False

    def load_advice(self):
        if not self.can_update:
            return False
        reference = self._reference
        return self._submit("advice", reference=reference, axis_snapshot=self._axis_snapshot) if reference is not None and self._axis_snapshot is not None else False

    def load_equipment_advice(self):
        if not self.can_load_equipment_advice:
            return False
        reference = self._reference
        return self._submit("equipment_advice", reference=reference, axis_snapshot=self._axis_snapshot) if reference is not None and self._axis_snapshot is not None else False

    def _current(self, worker):
        return (not self._closed and not worker.token.is_set() and worker.job.sequence == self._sequence
                and _team(worker.job.selection) == self.team)

    def _start(self):
        job, self._pending = self._pending, None
        if job is None or self._closed:
            return
        worker = _GraduationWorker(job, Event(), self)
        self._worker = worker
        worker.delivered.connect(lambda value: self._accept(worker, value))
        worker.failed.connect(lambda value: self._failed(worker, value))
        worker.finished.connect(self._retire)
        self._set_state("running_"+job.operation)
        worker.start()

    def _accept(self, worker, value):
        if not self._current(worker):
            return
        try:
            worker.job.service.checkpoint()
        except CancelledError:
            self.reset_account()
            return
        if not self._current(worker):
            return
        if value.kind == "preview":
            self._preview, self._reference, self._axis_snapshot = value.preview, value.reference, value.snapshot
            if value.reference is None or value.reference.benchmark_id != self._saved_reference_id:
                self._saved_reference_id = None
            self._base_display = self._display = value.display
            self._validity = None
            self._preview_stale = False
        if value.history is not None:
            self._history = value.history
        if value.kind == "save":
            self._saved_reference_id = self._reference.benchmark_id if self._reference else None
            self._saved_evaluation_id = value.history.selected_evaluation_id
        if value.kind == "configuration":
            self._configuration = value.configuration
        if value.kind == "restore":
            self._selection, self._preview = value.selection, None
            self._reference, self._axis_snapshot = value.reference, value.snapshot
            self._saved_reference_id = value.reference.benchmark_id if value.reference else None
            self._base_display = self._display = value.display
            self._validity = None
            self._saved_evaluation_id = value.history.selected_evaluation_id
            self._preview_stale = False
        if value.kind in {"preview", "restore"}:
            self._source_scope, self._event_service = value.source_scope, worker.job.service
        if value.kind == "validity":
            self._validity = value.validity
            self._preview_stale = value.validity.state != "context_matching"
            self._display = validity_display(self._base_display, value.validity)
        self._set_state(value.state)
        # 状态信号可同步关闭窗口或切换草稿；发结果信号前重新核对，不泄露已撤权预览。
        if self._current(worker):
            if value.kind == "preview":
                self.preview_ready.emit(value.preview)
            if self._current(worker) and value.history is not None:
                self.history_ready.emit(value.history)
            if self._current(worker) and value.kind in {"preview", "restore"}:
                self.display_ready.emit(value.display)
            if self._current(worker) and value.kind == "validity" and self._display is not None:
                self.display_ready.emit(self._display)
            if self._current(worker) and value.kind == "save":
                self.result_saved.emit()
            if self._current(worker) and value.kind == "configuration":
                self.configuration_ready.emit(value.configuration)

    def _failed(self, worker, error):
        if self._current(worker):
            # 某项建议缺能力不证明原基准已变；保留当前评价等其他操作的独立入口，派发仍各自复核。
            self.mark_unverified(preserve_benchmark=isinstance(error, AxisGraduationComponentPending))
            if not self._current(worker):
                return
            self._set_state("component_pending" if isinstance(error, AxisGraduationComponentPending) else "failed")
            if self._current(worker):
                self.error.emit(str(error))

    def _retire(self):
        worker = self._worker
        if worker is None:
            return
        # finished信号不替代物理join；TLS收尾未完时异步再查，不阻塞GUI或提前销毁QThread。
        if not worker.wait(0):
            QTimer.singleShot(10, self, self._retire)
            return
        self._worker = None
        worker.deleteLater()
        if self._current(worker):
            try:
                worker.job.service.checkpoint()
            except CancelledError:
                self.reset_account()
        if self._pending is not None and not self._closed:
            self._start()
            return
        if self._closed:
            self._set_state("closed")
        elif self._state.startswith("running_"):
            self._set_state("cancelled")
        if not self.is_running():
            self.drained.emit()

    def cancel(self):
        self._sequence += 1
        self._pending = None
        if self._worker is not None:
            self._worker.token.set()
        self._set_state("cancelled" if self._selection is not None else "unselected")

    def invalidate_current(self, team_id):
        if self.team is not None and self.team.team_id == team_id:
            self.cancel()
            self._preview_stale = True
            reason = "队伍所选来源收到变更通知，请显式更新当前评价"
            self._validity = (replace(self._validity, current="stale",
                reasons=tuple(dict.fromkeys((*self._validity.reasons, reason)))) if self._validity else
                AxisResultValidity("matching", "stale", "ready", (reason,)))
            self._display = validity_display(self._base_display, self._validity)
            sequence, display = self._sequence, self._display
            self._set_state(self._validity.state)
            if display is not None and not self._closed and self._sequence == sequence and self._display is display:
                self.display_ready.emit(display)

    def plans_saved(self, event):
        if self._closed or not isinstance(event, SavedPlansChanged) or self.team is None:
            return
        service = self._event_service
        if service is None and self._worker is not None:
            service = self._worker.job.service
        if service is None:
            return
        try:
            accepted = service.accepts_plan_change(event)
        except CancelledError:
            self.reset_account()
            return
        if accepted and (self._source_scope.affected(event) if self._source_scope is not None else
                         bool(set(self.team.character_ids).intersection(event.character_ids))):
            self.invalidate_current(self.team.team_id)

    def reset_account(self):
        self._selection = self._preview = self._reference = self._axis_snapshot = None
        self._saved_reference_id = None
        self._history = self._display = None
        self._base_display = self._validity = self._source_scope = self._event_service = None
        self._saved_evaluation_id = None
        self._configuration = None
        self._preview_stale = False
        self.cancel()

    def close(self):
        self._closed = True
        self.cancel()
        self._selection = self._preview = self._reference = self._axis_snapshot = None
        self._saved_reference_id = None
        self._history = self._display = None
        self._base_display = self._validity = self._source_scope = self._event_service = None
        self._saved_evaluation_id = None
        self._configuration = None
        self._preview_stale = False
        self._set_state("closing" if self.is_running() else "closed")
        if not self.is_running():
            self.drained.emit()
