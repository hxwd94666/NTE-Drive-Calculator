# 定义固定轴毕业结果与历史的不可变展示载荷，不持有语言目录或格式化文案。
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AxisGraduationDisplay:
    headline: str
    damage: str
    scope: str
    evidence: str
    target: str
    advice: str
    # 卡片只消费结构化比例与状态，禁止从可翻译的详细标题反解析数值。
    ratio_percent: float | None = None
    summary_status: str = "unquantified"
    historical: bool = False
    # 已校验目标的不可变展示副本，UI 不从中文说明反解析；不参与求解或历史持久化。
    target_json: str | None = None


@dataclass(frozen=True, slots=True)
class AxisGraduationHistoryView:
    team_id: str
    target_revision: int
    selection_revision: int
    selected_evaluation_id: str | None
    benchmark_id: str | None
    benchmarks: tuple[tuple[str, str], ...]
    evaluations: tuple[tuple[str, str], ...]
    benchmark_offset: int
    evaluation_offset: int
    more_benchmarks: bool
    more_evaluations: bool
    display: AxisGraduationDisplay | None


