# 固定轴毕业比较只接受同版本理论基准与实际构筑评价，验证身份和单位而不实现伤害重放公式。
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
import hashlib
import json
from math import isclose, isfinite

from src.domain.teams.report_matching import ReportRange


# 固定轴模型的正式标识；本机是否可计算仍由实际分析组件的身份与能力判定。
MODEL_ID = "team_axis_graduation_expected_v1"
CRITICAL_POLICY = "expected_candidate_state_v1"
DAMAGE_UNIT = "calculated_expected_axis_damage"
MAX_INTEGER = 2 ** 63 - 1
MAX_EVALUATION_BYTES = 128 * 1024


class GraduationStatus(StrEnum):
    NO_AXIS = "no_axis"
    NO_BENCHMARK = "no_benchmark"
    INPUT_MISSING = "input_missing"
    COMPONENT_REQUIRED = "component_required"
    RUNNING = "running"
    READY = "ready"
    PARTIAL = "partial"
    SEARCH_LIMITED = "search_limited"
    ABOVE_REFERENCE = "above_reference"
    OUTSIDE_TARGET = "outside_target"
    CURRENT_STALE = "current_stale"
    BENCHMARK_STALE = "benchmark_stale"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _text(value, label):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value.encode("utf-8")) > 256 or any(ord(char) < 32 for char in value)):
        raise ValueError(label + "身份文本无效")


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("轴伤毕业摘要必须为规范SHA-256")


def _integer(value, label, *, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INTEGER:
        raise ValueError(label + "整数无效")


def _number(value, label, *, minimum=None):
    try:
        valid = type(value) in (int, float) and isfinite(value) and (minimum is None or value >= minimum)
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + "数值缺失、非有限或越界")


def _matches(value, expected, label):
    _number(value, label)
    _number(expected, label)
    if not isclose(value, expected, rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError(label + "与当前方案及同版本基准不一致")


@dataclass(frozen=True, slots=True)
class AxisGraduationContext:
    team_id: str
    character_ids: tuple[int, ...]
    axis: ReportRange
    axis_sha256: str
    environment_sha256: str
    dataset_id: str
    static_schema: int
    static_sha256: str
    rules_sha256: str
    scoring_domain_sha256: str
    engine_version: str
    component_sha256: str
    component_manifest_sha256: str
    model_id: str = MODEL_ID
    critical_policy: str = CRITICAL_POLICY
    catalog_scope: str = "game"
    damage_unit: str = DAMAGE_UNIT

    def __post_init__(self):
        for label in ("team_id", "dataset_id", "engine_version"):
            _text(getattr(self, label), label)
        if not isinstance(self.character_ids, (tuple, list)) or len(self.character_ids) != 4:
            raise ValueError("轴伤毕业上下文需要四名正式成员")
        ids = tuple(self.character_ids)
        for key in ids:
            _integer(key, "角色", minimum=1)
        if len(set(ids)) != 4:
            raise ValueError("轴伤毕业上下文包含重复成员")
        # 复制调用方容器；顺序也属于冻结身份，不让后续草稿修改穿透不可变结果。
        object.__setattr__(self, "character_ids", ids)
        if not isinstance(self.axis, ReportRange):
            raise ValueError("轴伤毕业上下文缺少明确战报范围")
        _integer(self.axis.record_id, "战报", minimum=1)
        for time in (self.axis.start_us, self.axis.end_us):
            if time is not None:
                _integer(time, "轴时刻")
        if self.catalog_scope != "game":
            raise ValueError("轴伤毕业上下文只接受完整主静态，不接受图鉴用途")
        _integer(self.static_schema, "静态schema", minimum=1)
        for name in ("axis_sha256", "environment_sha256", "static_sha256", "rules_sha256",
                     "scoring_domain_sha256", "component_sha256", "component_manifest_sha256"):
            _digest(getattr(self, name))
        if self.damage_unit != DAMAGE_UNIT:
            raise ValueError("轴伤毕业伤害单位不支持；观测权重目标不等于同口径计算轴伤")
        if self.model_id != MODEL_ID or self.critical_policy != CRITICAL_POLICY:
            raise ValueError("轴伤毕业模型或暴击策略不支持；旧冻结观测触发不等于候选条件传播")

    @property
    def sha256(self):
        encoded = json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AxisScoringCoverage:
    scoring_domain_sha256: str
    event_count: int
    quantified_event_count: int
    estimated_event_count: int
    excluded_event_count: int
    critical_gaps: tuple[str, ...] = ()

    def __post_init__(self):
        _digest(self.scoring_domain_sha256)
        _integer(self.event_count, "事件总量", minimum=1)
        for name in ("quantified_event_count", "estimated_event_count", "excluded_event_count"):
            _integer(getattr(self, name), name)
        if self.quantified_event_count + self.estimated_event_count + self.excluded_event_count != self.event_count:
            raise ValueError("轴伤毕业覆盖计数不守恒")
        if not isinstance(self.critical_gaps, (tuple, list)) or len(self.critical_gaps) > 256:
            raise ValueError("轴伤毕业关键缺口格式无效")
        gaps = tuple(self.critical_gaps)
        for gap in gaps:
            _text(gap, "关键缺口")
        if len(set(gaps)) != len(gaps):
            raise ValueError("轴伤毕业关键缺口重复")
        object.__setattr__(self, "critical_gaps", gaps)

    @property
    def comparable(self):
        return not self.critical_gaps and self.quantified_event_count + self.estimated_event_count > 0

    @property
    def complete(self):
        return self.comparable and self.estimated_event_count == self.excluded_event_count == 0


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    stop_reason: str
    evaluated_candidates: int
    evaluation_budget: int
    time_budget_ms: int
    elapsed_ms: int
    seed: int
    legal_domain_sha256: str
    neighborhood_complete: bool
    optimality: str = "bounded_best"
    unquantified_candidates: int = 0

    def __post_init__(self):
        # 只有完整合法incumbent可成为基准；取消/输入失效/失败是任务状态，不是可保存结果。
        if (not isinstance(self.stop_reason, str)
                or self.stop_reason not in {"neighborhood_complete", "evaluation_budget", "time_budget", "preset_budget", "quantification_limited"}):
            raise ValueError("轴伤毕业搜索未形成可发布的完成结果")
        _integer(self.evaluated_candidates, "已评价数量", minimum=1)
        _integer(self.evaluation_budget, "评价预算", minimum=1)
        _integer(self.time_budget_ms, "时间预算毫秒", minimum=1)
        _integer(self.elapsed_ms, "耗时毫秒")
        _integer(self.seed, "随机种子")
        _integer(self.unquantified_candidates, "未知候选数量")
        if self.unquantified_candidates > self.evaluated_candidates:
            raise ValueError("未知候选数量超过已评价数量")
        if (self.stop_reason == "quantification_limited" and self.unquantified_candidates == 0
                or self.stop_reason == "neighborhood_complete" and self.unquantified_candidates != 0):
            raise ValueError("搜索量化受限与未知候选证据不一致")
        _digest(self.legal_domain_sha256)
        if self.stop_reason == "time_budget" and self.elapsed_ms < self.time_budget_ms:
            raise ValueError("时间预算停止原因与已用时间不一致")
        if self.evaluated_candidates > self.evaluation_budget:
            raise ValueError("轴伤毕业已评价数量超过冻结预算")
        if (type(self.neighborhood_complete) is not bool
                or self.stop_reason == "neighborhood_complete" and not self.neighborhood_complete
                or self.stop_reason == "preset_budget" and self.neighborhood_complete):
            raise ValueError("轴伤毕业邻域完成状态无效")
        # 首版尚无全局证明校验器；即使带一个哈希或上下界相等也不接纳最优证书声明。
        if self.optimality != "bounded_best":
            raise ValueError("轴伤毕业首版只声明有界搜索基准，不接纳未经验证的全局最优证明")

    @property
    def limited(self):
        return self.stop_reason in {"evaluation_budget", "time_budget", "preset_budget", "quantification_limited"}


@dataclass(frozen=True, slots=True)
class BenchmarkSummary:
    benchmark_id: str
    target_id: str
    target_revision: int
    target_sha256: str
    context: AxisGraduationContext
    reference_damage: float
    theoretical_build_sha256: str
    coverage: AxisScoringCoverage
    search: SearchEvidence

    def __post_init__(self):
        _text(self.benchmark_id, "基准")
        _text(self.target_id, "目标")
        _integer(self.target_revision, "目标修订", minimum=1)
        _digest(self.target_sha256)
        _digest(self.theoretical_build_sha256)
        if not isinstance(self.context, AxisGraduationContext) or not isinstance(self.coverage, AxisScoringCoverage):
            raise ValueError("轴伤毕业基准上下文或覆盖格式无效")
        if not isinstance(self.search, SearchEvidence):
            raise ValueError("轴伤毕业基准搜索证据无效")
        _number(self.reference_damage, "理论基准伤害", minimum=0)
        if self.reference_damage <= 0:
            raise ValueError("理论基准伤害为零，不生成毕业率")
        if (self.coverage.scoring_domain_sha256 != self.context.scoring_domain_sha256
                or not self.coverage.comparable):
            raise ValueError("理论基准缺少同口径计分域或存在关键机制缺口")


@dataclass(frozen=True, slots=True)
class GraduationEvaluation:
    benchmark: BenchmarkSummary
    current_build_sha256: str
    current_context: AxisGraduationContext
    current_coverage: AxisScoringCoverage
    current_damage: float
    current_ratio_percent: float
    remaining_damage: float
    relative_gain_to_reference: float | None
    remaining_percentage_points: float
    comparison_note: str = ""

    def __post_init__(self):
        if not isinstance(self.benchmark, BenchmarkSummary):
            raise ValueError("当前轴伤评价缺少不可变基准")
        _digest(self.current_build_sha256)
        if self.current_context != self.benchmark.context:
            raise ValueError("当前与理论基准的轴伤上下文不一致，请建立新基准")
        if self.current_coverage != self.benchmark.coverage:
            raise ValueError("当前与理论基准的计分覆盖不一致，保留未知而不删事件对齐")
        if self.comparison_note:
            _text(self.comparison_note, "比较诊断")
        elif not isinstance(self.comparison_note, str):
            raise ValueError("轴伤毕业比较诊断格式无效")
        _number(self.current_damage, "当前轴伤", minimum=0)
        _number(self.current_ratio_percent, "当前百分比", minimum=0)
        reference = self.benchmark.reference_damage
        # 仅校验组件返回的比例及单位；伤害与反事实均须由独立分析组件计算，不在此复刻公式。
        _matches(self.current_ratio_percent, self.current_damage / reference * 100, "当前毕业率")
        _matches(self.remaining_damage, reference - self.current_damage, "基准伤害差值")
        _matches(self.remaining_percentage_points, 100 - self.current_ratio_percent, "毕业率百分点差")
        if self.current_damage == 0:
            if self.relative_gain_to_reference is not None:
                raise ValueError("当前零伤害时相对提升未知，不填零或无穷")
        else:
            _matches(self.relative_gain_to_reference, (reference - self.current_damage) / self.current_damage, "相对提升")
        if self.current_ratio_percent > 100 and not self.comparison_note:
            raise ValueError("当前值超过基准需要明确诊断，不截断到100%")

    @property
    def status(self):
        if self.current_ratio_percent > 100:
            return GraduationStatus.ABOVE_REFERENCE
        if not self.current_coverage.complete:
            return GraduationStatus.PARTIAL
        if self.benchmark.search.limited:
            return GraduationStatus.SEARCH_LIMITED
        return GraduationStatus.READY


def _payload_fields(cls, value):
    # 保存与协议读取都只接纳正式字段，不能靠忽略未知字段把另一种指标解释成毕业率。
    if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
        raise ValueError("轴伤毕业结果字段与正式格式不一致")
    return dict(value)


def _context_payload(value):
    data = _payload_fields(AxisGraduationContext, value)
    data["axis"] = ReportRange(**_payload_fields(ReportRange, data["axis"]))
    return AxisGraduationContext(**data)


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("轴伤毕业结果包含重复JSON字段")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("轴伤毕业结果包含非有限JSON数值")


def decode_graduation_evaluation(raw: str) -> GraduationEvaluation:
    """重建不可变比较摘要；此格式不携带原始逐击、完整库存或任何数据库路径。"""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_EVALUATION_BYTES:
        raise ValueError("轴伤毕业结果正文超限或格式无效")
    try:
        envelope = json.loads(raw, object_pairs_hook=_unique_fields, parse_constant=_reject_constant)
        if (not isinstance(envelope, dict) or set(envelope) != {"version", "evaluation"}
                or type(envelope["version"]) is not int or envelope["version"] != 1):
            raise ValueError("轴伤毕业结果版本不支持")
        current = _payload_fields(GraduationEvaluation, envelope["evaluation"])
        base = _payload_fields(BenchmarkSummary, current["benchmark"])
        base["context"] = _context_payload(base["context"])
        base["coverage"] = AxisScoringCoverage(**_payload_fields(AxisScoringCoverage, base["coverage"]))
        base["search"] = SearchEvidence(**_payload_fields(SearchEvidence, base["search"]))
        current["benchmark"] = BenchmarkSummary(**base)
        current["current_context"] = _context_payload(current["current_context"])
        current["current_coverage"] = AxisScoringCoverage(**_payload_fields(AxisScoringCoverage, current["current_coverage"]))
        return GraduationEvaluation(**current)
    except (TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError("轴伤毕业结果嵌套数据类型无效") from error


def encode_graduation_evaluation(value: GraduationEvaluation) -> str:
    if not isinstance(value, GraduationEvaluation):
        raise ValueError("只保存正式轴伤毕业比较结果")
    try:
        raw = json.dumps({"version": 1, "evaluation": asdict(value)}, ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("轴伤毕业结果编码失败") from error
    # 统一验证写入和读取，后续DAO不得只校验JSON可解析就承诺历史结果可比。
    decode_graduation_evaluation(raw)
    return raw
