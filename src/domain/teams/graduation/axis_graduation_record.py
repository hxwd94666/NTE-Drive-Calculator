# 保存独立的轴伤数学事实及内容身份；移除本机会话路径，摘要只证明字节完整而非来源认证。
from dataclasses import dataclass
import hashlib
import json

from src.domain.teams.graduation.axis_graduation_reference import freeze_benchmark_reference
from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.domain.teams.models import Team


MAX_RECORD_BYTES = 20 * 1024 * 1024
FORMAT = "team_axis_graduation_saved_result_v1"
SELECTOR_KEYS = frozenset({"account_id", "battle_record_id", "start_us", "end_us", "detail_scope", "detail_level"})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("保存的轴伤结果含重复字段")
        result[key] = value
    return result


def _json(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(raw.encode("utf8")) > MAX_RECORD_BYTES:
        raise ValueError("保存的轴伤结果正文超限")
    return raw


@dataclass(frozen=True, slots=True)
class SavedAxisResult:
    """DAO只核对内容与关联；完整协议语义由保存/导入应用边界的正式适配器验证。"""
    body_json: str

    def __post_init__(self):
        try:
            self._validate()
        except (KeyError, TypeError, UnicodeError, OverflowError, RecursionError):
            raise ValueError("保存的轴伤结果正文无效") from None

    def _validate(self):
        if not isinstance(self.body_json, str) or len(self.body_json.encode("utf8")) > MAX_RECORD_BYTES:
            raise ValueError("保存的轴伤结果正文超限")
        value = json.loads(self.body_json, object_pairs_hook=_pairs)
        if not isinstance(value, dict) or set(value) != {"format", "request", "response"} or value["format"] != FORMAT:
            raise ValueError("保存的轴伤结果格式无效")
        request, response = value["request"], value["response"]
        operation = request["operation"]
        if operation not in {"theoretical_benchmark", "current_evaluation", "recommendation_evaluation", "equipment_advice_evaluation"}:
            raise ValueError("保存的轴伤操作无效")
        if (request["version"] != 2 or response["version"] != 2 or response["operation"] != operation
                or "generation" in request["current"] or not isinstance(request["battle_request"], dict)
                or not set(request["battle_request"]) <= SELECTOR_KEYS):
            raise ValueError("保存的轴伤结果混入本机路径或会话代次")
        if "reference" in request or "axis_snapshot" in request:
            raise ValueError("保存正文不能重复携带参照和轴快照")
        target = decode_graduation_target(_json(request["target"]))
        reference = freeze_benchmark_reference(response["benchmark_reference"])
        snapshot = freeze_axis_context_snapshot(response["axis_snapshot"])
        p, meta, current = reference.to_payload()["payload"], snapshot.to_payload(), request["current"]
        scope = {k: request["battle_request"].get(k) for k in ("battle_record_id", "start_us", "end_us", "detail_scope")}
        if (p["axis_snapshot_sha256"] != snapshot.sha256 or p["scope"] != scope or meta["scope"] != scope
                or not (p["account_id"] == meta["account_id"] == current["account_id"] == request["battle_request"]["account_id"])
                or target.team_id != current["team_id"] or list(target.character_ids) != current["character_ids"]
                or target.sha256 != p["target_sha256"] or request["target_sha256"] != target.sha256
                or response["target_sha256"] != target.sha256 or response["current_ratio_percent"] is not None):
            raise ValueError("保存的轴伤结果关联不一致或虚报正式百分比")
        for key in ("dataset_id", "static_schema", "static_sha256"):
            if meta[key] != getattr(target, key) or current[key] != meta[key] or request["rules"][key] != meta[key]:
                raise ValueError("保存的轴伤静态身份不一致")
        if (request["source_input_digest"] != meta["source_input_digest"]
                or response["source_input_digest"] != meta["source_input_digest"]
                or response["current"]["source_sha256"] != current["source_sha256"]
                or request["component_provenance"] != p["component"]
                or response["engine_version"] != p["engine_version"] or p["engine_version"] != meta["engine_version"]):
            raise ValueError("保存的轴伤来源或组件身份不一致")
        object.__setattr__(self, "body_json", _json(value))

    def to_payload(self):
        return json.loads(self.body_json)

    @property
    def sha256(self):
        return hashlib.sha256(self.body_json.encode("utf8")).hexdigest()

    @property
    def evaluation_id(self):
        return "axis-evaluation-" + self.sha256

    @property
    def reference(self):
        return freeze_benchmark_reference(self.to_payload()["response"]["benchmark_reference"])

    @property
    def benchmark_id(self):
        return self.reference.benchmark_id

    @property
    def target(self):
        return decode_graduation_target(_json(self.to_payload()["request"]["target"]))

    @property
    def account_id(self):
        return self.reference.to_payload()["payload"]["account_id"]

    @property
    def operation(self):
        return self.to_payload()["request"]["operation"]


@dataclass(frozen=True, slots=True)
class SavedAxisSelection:
    team_id: str
    benchmark_id: str
    evaluation_id: str
    revision: int


@dataclass(frozen=True, slots=True)
class SavedAxisHistory:
    team: Team
    selected: SavedAxisSelection | None
    selected_result: SavedAxisResult | None
    target_revision: int
    benchmarks: tuple[SavedAxisResult, ...]
    evaluations: tuple[SavedAxisResult, ...]
    benchmark_id: str | None
    benchmark_offset: int
    evaluation_offset: int
    more_benchmarks: bool
    more_evaluations: bool
