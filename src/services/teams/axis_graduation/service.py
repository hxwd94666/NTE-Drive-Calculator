# 编排冻结输入、独立分析与发布复核；仅显式保存账号数学结果，不实现伤害公式或启动游戏操作。
from concurrent.futures import CancelledError
from dataclasses import dataclass
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from src.domain.teams.graduation.axis_graduation_inputs import CurrentBuild
from src.domain.teams.graduation.axis_graduation_record import SavedAxisHistory
from src.domain.teams.graduation.axis_graduation_snapshot import FrozenAxisContextSnapshot, freeze_axis_context_snapshot
from src.domain.teams.graduation.axis_graduation_reference import FrozenBenchmarkReference, freeze_benchmark_reference
from src.domain.teams.graduation.axis_graduation_rules import TheoryEquipmentRules
from src.domain.teams.graduation.axis_graduation_target import GraduationTarget
from src.domain.teams.report_matching import ReportRange
from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError, REQUEST_SCHEMA
from src.integrations.battle_analysis.graduation.team_axis_graduation import FIXED_AXIS_RATIO_POLICY, BATCH_KIND, MAX_WIRE_BYTES, validate_request, validate_response
from src.integrations.battle_analysis.graduation.team_axis_graduation_archive import archive_axis_result, validate_saved_axis_result
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.teams.axis_graduation.requests import (
    actual_component_input, build_advice_request, build_benchmark_request, build_current_request, build_equipment_advice_request,
)


class AxisGraduationComponentPending(NativeAnalysisError):
    """缺少本操作组件能力；与身份/响应错误分开交给页面投影。"""


def _pairs(entries):
    value = {}
    for key, item in entries:
        if key in value:
            raise ValueError("冻结轴伤请求含重复字段")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("冻结轴伤输入含非有限数值")


def _decode(raw):
    if not isinstance(raw, str) or len(raw.encode("utf8")) > MAX_WIRE_BYTES:
        raise ValueError("冻结轴伤正文超限")
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_reject_constant)


def _encode(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf8")) > MAX_WIRE_BYTES:
        raise ValueError("冻结轴伤正文超限")
    return raw


def _request_digest(request, dataset):
    # 复核真正传输的根字段顺序和字节，不以另一份sort_keys编码误算线协议请求摘要。
    envelope = {"schema_version": REQUEST_SCHEMA, "dataset_version": dataset, "batch_kind": BATCH_KIND, "request": request}
    raw = json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenAxisGraduationRequest:
    current: CurrentBuild
    engine_version: str
    request_json: str

    def __post_init__(self):
        if not isinstance(self.current, CurrentBuild) or not isinstance(self.engine_version, str) or not self.engine_version:
            raise ValueError("轴伤任务缺少实际输入或分析引擎身份")
        request = _decode(self.request_json)
        validate_request(request, self.current.dataset_id)
        if request["current"] != actual_component_input(self.current):
            raise ValueError("轴伤任务正文与冻结实际来源不一致")
        object.__setattr__(self, "request_json", _encode(request))

    @property
    def payload(self):
        return _decode(self.request_json)


@dataclass(frozen=True, slots=True)
class AxisGraduationPreview:
    request: FrozenAxisGraduationRequest
    response_json: str

    def __post_init__(self):
        if not isinstance(self.request, FrozenAxisGraduationRequest):
            raise ValueError("轴伤预览缺少冻结任务")
        response = _decode(self.response_json)
        body = self.request.payload
        validate_response(response, body, dataset_version=self.request.current.dataset_id,
            engine_version=self.request.engine_version, request_sha256=_request_digest(body, self.request.current.dataset_id))
        object.__setattr__(self, "response_json", _encode(response))

    @property
    def response(self):
        return _decode(self.response_json)

    @property
    def axis_snapshot(self):
        return freeze_axis_context_snapshot(self.response["axis_snapshot"])

    @property
    def reference(self):
        value = self.response["benchmark_reference"]
        return freeze_benchmark_reference(value) if value is not None else None


class TeamAxisGraduationService:
    def __init__(self, *, current_inputs, semantics_path, client_factory=create_bundled_analysis_client, scoring_rules_path=None):
        self._inputs = current_inputs
        self._semantics_path = Path(semantics_path).resolve()
        self._factory = client_factory
        self._scoring_rules_path = Path(scoring_rules_path) if scoring_rules_path is not None else bundled_config_dir() / "stats.json"

    def load_configuration(self, team, *, cancelled=lambda: False):
        from src.services.teams.axis_graduation.configuration import load_graduation_configuration
        return load_graduation_configuration(self._inputs, team, scoring_rules_path=self._scoring_rules_path, cancelled=cancelled)

    def checkpoint(self, cancelled=lambda: False):
        self._inputs.checkpoint(cancelled)

    def accepts_plan_change(self, event):
        self.checkpoint()
        return (isinstance(event, SavedPlansChanged)
                and (event.account_id, event.generation) == (self._inputs.account_id, self._inputs.generation)
                and Path(event.database_path).resolve() == Path(self._inputs.database_path).resolve())

    def check_validity(self, team, *, axis, target, rules, preview=None, evaluation_id=None, cancelled=lambda: False):
        from src.services.teams.axis_graduation.validity import check_result_validity
        record = None
        if preview is None:
            with self._targets(team, cancelled) as dao, dao.read_consistent_state():
                record = validate_saved_axis_result(dao.get_team_axis_evaluation(team.team_id, evaluation_id))
        return check_result_validity(self._inputs, team, axis=axis, target=target, rules=rules,
            preview=preview, record=record, client_factory=self._factory, scoring_rules_path=self._scoring_rules_path,
            cancelled=cancelled)

    def _cancelled(self, cancelled):
        try:
            self.checkpoint(cancelled)
            return False
        except CancelledError:
            return True

    def _client(self, operation, cancelled, frozen=None):
        self.checkpoint(cancelled)
        client = self._factory(static_database_path=self._inputs.static_database_path,
                               cancelled=lambda: self._cancelled(cancelled))
        capability = {"theoretical_benchmark": "supports_team_axis_graduation_benchmark",
            "current_evaluation": "supports_team_axis_graduation_current",
            "recommendation_evaluation": "supports_team_axis_graduation_advice",
            "equipment_advice_evaluation": "supports_team_axis_graduation_equipment_advice"}[operation]
        if (client is None or not getattr(client, capability, False)
                or operation == "theoretical_benchmark" and (
                    not getattr(client, "supports_battle_page", False)
                    or not getattr(client, "supports_battle_page_identity", False))):
            raise AxisGraduationComponentPending("轴伤毕业分析组件待更新，来源、匹配和增强功能保持原状态")
        if frozen is not None:
            body = frozen.payload
            if (client.engine_version != frozen.engine_version or client.dataset_version != frozen.current.dataset_id
                    or client.component_provenance != body["component_provenance"]):
                raise ValueError("分析组件或静态身份已变化，请重新冻结任务；旧基准保持不变")
        self.checkpoint(cancelled)
        return client

    def _identity(self, client, selector, cancelled):
        # 正式identity页保留半场和时段。旧全页快捷helper会丢范围，此处不使用它或自行读取战报库。
        value = client.load_battle_page({**selector, "detail_level": "identity"},
                                       checkpoint=lambda: self.checkpoint(cancelled))
        self.checkpoint(cancelled)
        if not isinstance(value, dict) or set(value) != {"input_digest"}:
            raise NativeAnalysisError("所选轴缺少正式输入身份")
        digest = value["input_digest"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise NativeAnalysisError("所选轴输入身份格式无效")
        return digest

    def _prepare(self, operation, team, axis, *, target, rules, request_id, cancelled, reference=None, axis_snapshot=None, **options):
        self.checkpoint(cancelled)
        if not isinstance(axis, ReportRange) or not isinstance(target, GraduationTarget) or not isinstance(rules, TheoryEquipmentRules):
            raise ValueError("轴伤任务需要明确范围、目标及冻结规则")
        target.verify_rules(rules)
        if team.team_id != target.team_id or team.character_ids != target.character_ids:
            raise ValueError("毕业目标与当前队伍不一致")
        if operation != "theoretical_benchmark" and (not isinstance(reference, FrozenBenchmarkReference)
                or not isinstance(axis_snapshot, FrozenAxisContextSnapshot)):
            raise ValueError("当前或建议评价必须引用不可变基准及其轴快照")
        # 先脱离调用方的可变种子/预算草稿；耗时读取期间的草稿编辑不能渗入本次任务。
        options = _decode(_encode(options))
        try:
            client = self._client(operation, cancelled)
            current = self._inputs.freeze_current(team, cancelled=cancelled)
            if client.dataset_version != current.dataset_id:
                raise ValueError("分析组件与本次完整主静态身份不一致")
            if current.has_gaps:
                raise ValueError("当前养成或来源仍有缺项，请补齐后评价轴伤")
            if (current.dataset_id, current.static_schema, current.static_sha256) != (rules.dataset_id, rules.static_schema, rules.static_sha256):
                raise ValueError("当前实际来源与目标规则的静态身份不一致")
            selector = {"account_id": current.account_id, "generation": current.generation,
                "user_database_path": str(Path(self._inputs.database_path).resolve()),
                "static_database_path": str(Path(self._inputs.static_database_path).resolve()),
                "semantics_path": str(self._semantics_path), "detail_level": "hit", **axis.selector_fields()}
            if operation == "theoretical_benchmark":
                source = self._identity(client, selector, cancelled)
            else:
                source = axis_snapshot.to_payload()["source_input_digest"]
            values = dict(frozen=current, target=target, rules=rules, battle_selector=selector,
                source_input_digest=source, request_id=request_id, component_provenance=dict(client.component_provenance))
            if operation == "theoretical_benchmark":
                body = build_benchmark_request(**values, **options)
            else:
                builder = {"current_evaluation": build_current_request, "recommendation_evaluation": build_advice_request,
                           "equipment_advice_evaluation": build_equipment_advice_request}[operation]
                body = builder(**values, reference=reference, axis_snapshot=axis_snapshot)
            if getattr(client, "supports_team_axis_graduation_fixed_axis_ratio", False):
                body["fixed_axis_ratio_policy"] = FIXED_AXIS_RATIO_POLICY
            self.checkpoint(cancelled)
            return FrozenAxisGraduationRequest(current, client.engine_version, _encode(body))
        except NativeAnalysisCancelled:
            raise CancelledError("轴伤任务已取消或账号已切换") from None

    def prepare_benchmark(self, team, axis, *, target, rules, seeds, request_id,
                          max_evaluations, max_millis, cancelled=lambda: False):
        return self._prepare("theoretical_benchmark", team, axis, target=target, rules=rules, request_id=request_id,
            cancelled=cancelled, seeds=seeds, max_evaluations=max_evaluations, max_millis=max_millis)

    def prepare_current(self, team, axis, *, target, rules, reference, axis_snapshot, request_id, cancelled=lambda: False):
        return self._prepare("current_evaluation", team, axis, target=target, rules=rules,
                             reference=reference, axis_snapshot=axis_snapshot, request_id=request_id, cancelled=cancelled)

    def prepare_advice(self, team, axis, *, target, rules, reference, axis_snapshot, request_id, cancelled=lambda: False):
        return self._prepare("recommendation_evaluation", team, axis, target=target, rules=rules,
                             reference=reference, axis_snapshot=axis_snapshot, request_id=request_id, cancelled=cancelled)

    def prepare_equipment_advice(self, team, axis, *, target, rules, reference, axis_snapshot, request_id, cancelled=lambda: False):
        return self._prepare("equipment_advice_evaluation", team, axis, target=target, rules=rules,
                             reference=reference, axis_snapshot=axis_snapshot, request_id=request_id, cancelled=cancelled)

    def _guard(self, task, cancelled):
        if not isinstance(task, FrozenAxisGraduationRequest):
            raise ValueError("轴伤操作只消费本次冻结任务")
        self._inputs.verify_current(task.current, cancelled=cancelled)
        return self._client(task.payload["operation"], cancelled, task)

    def _execute(self, task, operation, cancelled):
        if not isinstance(task, FrozenAxisGraduationRequest) or task.payload["operation"] != operation:
            raise ValueError("轴伤任务与所选操作不一致")
        try:
            client = self._guard(task, cancelled)
            response = client.evaluate_team_axis_graduation(task.payload, checkpoint=lambda: self.checkpoint(cancelled))
            preview = AxisGraduationPreview(task, _encode(response))
            # 完成后复核实际来源与账号代次；只在新建基准时探测源轴，旧参照使用绑定快照。
            selector = task.payload["battle_request"]
            if operation == "theoretical_benchmark" and self._identity(client, selector, cancelled) != task.payload["source_input_digest"]:
                raise ValueError("所选轴输入已变化，丢弃本次结果并保留旧基准")
            self._inputs.verify_current(task.current, cancelled=cancelled)
            self.checkpoint(cancelled)
            return preview
        except NativeAnalysisCancelled:
            raise CancelledError("轴伤任务已取消或账号已切换") from None

    def solve_benchmark(self, task, *, cancelled=lambda: False):
        return self._execute(task, "theoretical_benchmark", cancelled)

    def evaluate_current(self, task, *, cancelled=lambda: False):
        return self._execute(task, "current_evaluation", cancelled)

    def evaluate_advice(self, task, *, cancelled=lambda: False):
        return self._execute(task, "recommendation_evaluation", cancelled)

    def evaluate_equipment_advice(self, task, *, cancelled=lambda: False):
        return self._execute(task, "equipment_advice_evaluation", cancelled)

    def verify_preview(self, preview, *, cancelled=lambda: False):
        """为后续显式保存提供复核，不写库；数学预览不因此变成正式毕业评价。"""
        if not isinstance(preview, AxisGraduationPreview):
            raise ValueError("待复核对象不是冻结轴伤预览")
        try:
            task = preview.request
            self._guard(task, cancelled)
            # 已完成预览绑定冻结轴，不要求原战报继续存在；保存仍复核当前实际来源和组件。
            if preview.reference is not None and preview.reference.to_payload()["payload"]["axis_snapshot_sha256"] != preview.axis_snapshot.sha256:
                raise ValueError("预览引用了另一份冻结轴")
            self._inputs.verify_current(task.current, cancelled=cancelled)
            self.checkpoint(cancelled)
        except NativeAnalysisCancelled:
            raise CancelledError from None


    @contextmanager
    def _targets(self, team, cancelled):
        self.checkpoint(cancelled)
        # 不提供account_id创建参数；缺库直接报错，不制造替代账号。路径由当前输入owner冻结。
        with UserDataDao(self._inputs.database_path) as dao:
            if dao.profile()["account_id"] != self._inputs.account_id:
                raise CancelledError("目标库不属于本次账号")
            if team not in dao.list_teams():
                raise ValueError("队伍已变化，请刷新后读取或保存目标")
            yield dao
            self.checkpoint(cancelled)

    def list_targets(self, team, *, offset=0, limit=20, cancelled=lambda: False):
        with self._targets(team, cancelled) as dao, dao.read_consistent_state():
            result = dao.list_team_axis_targets(team.team_id, offset=offset, limit=limit)
        return result

    def save_target(self, team, target, *, expected_revision, cancelled=lambda: False):
        """显式保存目标定义，不保存数学预览、不依赖分析组件或启动求解。"""
        with self._targets(team, cancelled) as dao:
            return dao.save_team_axis_target(team, target, expected_revision=expected_revision,
                                             checkpoint=lambda: self.checkpoint(cancelled))

    def save_completed_result(self, preview, *, expected_target_revision, expected_selection_revision,
                              select=True, cancelled=lambda: False):
        """显式保存已完成数学预览，不补输入、不重搜、不生成正式毕业百分比。"""
        self.verify_preview(preview, cancelled=cancelled)
        record = archive_axis_result(preview.request.payload, preview.response)
        with self._targets(preview.request.current.team, cancelled) as dao:
            return dao.save_team_axis_result(preview.request.current.team, record,
                expected_target_revision=expected_target_revision, expected_selection_revision=expected_selection_revision,
                select=select, checkpoint=lambda: self.verify_preview(preview, cancelled=cancelled))

    def list_benchmarks(self, team, *, offset=0, limit=20, cancelled=lambda: False):
        with self._targets(team, cancelled) as dao, dao.read_consistent_state():
            return tuple(validate_saved_axis_result(result) for result in
                         dao.list_team_axis_benchmarks(team.team_id, offset=offset, limit=limit))

    def list_evaluations(self, team, benchmark_id, *, offset=0, limit=20, cancelled=lambda: False):
        with self._targets(team, cancelled) as dao, dao.read_consistent_state():
            return tuple(validate_saved_axis_result(result) for result in
                         dao.list_team_axis_evaluations(team.team_id, benchmark_id, offset=offset, limit=limit))

    def selected_result(self, team, *, cancelled=lambda: False):
        """只读历史不探测分析组件；有效性由当前页面另行投影，不把历史当新计算。"""
        with self._targets(team, cancelled) as dao, dao.read_consistent_state():
            selection = dao.get_team_axis_selection(team.team_id)
            result = dao.get_team_axis_evaluation(team.team_id, selection.evaluation_id) if selection else None
            return selection, validate_saved_axis_result(result) if result is not None else None

    def select_saved_result(self, team, evaluation_id, *, expected_revision, cancelled=lambda: False):
        with self._targets(team, cancelled) as dao:
            # 校验完整历史后才移动指针，不以插入顺序或更高伤害自动选择。
            validate_saved_axis_result(dao.get_team_axis_evaluation(team.team_id, evaluation_id))
            return dao.select_team_axis_result(team, evaluation_id, expected_revision=expected_revision,
                                               checkpoint=lambda: self.checkpoint(cancelled))

    @staticmethod
    def _history_from_dao(dao, team, *, benchmark_id=None, benchmark_offset=0, evaluation_offset=0):
        # 每页十项，多读一项仅判断是否有下一页；选择来自独立指针，不受排序/分页影响。
        benchmarks = dao.list_team_axis_benchmarks(team.team_id, offset=benchmark_offset, limit=11)
        selected = dao.get_team_axis_selection(team.team_id)
        selected_result = dao.get_team_axis_evaluation(team.team_id, selected.evaluation_id) if selected else None
        latest_target = dao.get_team_axis_target(team.team_id)
        if benchmark_id is None:
            benchmark_id = selected.benchmark_id if selected else benchmarks[0].benchmark_id if benchmarks else None
        if benchmark_id is not None and dao.get_team_axis_benchmark(team.team_id, benchmark_id) is None:
            raise ValueError("历史基准不属于当前队伍")
        evaluations = dao.list_team_axis_evaluations(team.team_id, benchmark_id, offset=evaluation_offset, limit=11)
        for result in (*benchmarks, *evaluations, *((selected_result,) if selected_result else ())):
            validate_saved_axis_result(result)
        return SavedAxisHistory(team, selected, selected_result, latest_target.revision if latest_target else 0,
            benchmarks[:10], evaluations[:10], benchmark_id, benchmark_offset, evaluation_offset,
            len(benchmarks) > 10, len(evaluations) > 10)

    def load_history(self, team, *, cancelled=lambda: False, **page):
        with self._targets(team, cancelled) as dao, dao.read_consistent_state():
            result = self._history_from_dao(dao, team, **page)
            self.checkpoint(cancelled)
            return result

    def activate_saved_result(self, team, evaluation_id, *, expected_revision, cancelled=lambda: False):
        # 选用和读取上下文在同一写事务中完成；不重新评价、不要求分析组件就绪。
        with self._targets(team, cancelled) as dao, dao.write_transaction():
            validate_saved_axis_result(dao.get_team_axis_evaluation(team.team_id, evaluation_id))
            dao.select_team_axis_result(team, evaluation_id, expected_revision=expected_revision,
                                       checkpoint=lambda: self.checkpoint(cancelled))
            result = self._history_from_dao(dao, team)
            self.checkpoint(cancelled)
            return result
