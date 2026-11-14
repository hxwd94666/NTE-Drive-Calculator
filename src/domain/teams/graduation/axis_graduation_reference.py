# 不可变数学基准引用绑定账号、组件、固定构筑与重放输入；摘要只保证内容一致，不代替独立重放复核。
from dataclasses import dataclass
import hashlib
import json
import math
import struct

from src.domain.teams.graduation.axis_graduation import MODEL_ID, CRITICAL_POLICY, DAMAGE_UNIT


REFERENCE_FORMAT = "team_axis_graduation_reference_v2"
MAX_REFERENCE_BYTES = 512 * 1024
PAYLOAD_KEYS = frozenset("account_id component scope target_sha256 rules_sha256 preset_id build scoring_domain_sha256 "
    "axis_snapshot_sha256 anchor_world replay_input_sha256 layout_sha256 cultivation_sha256 origin_request_sha256 reference_damage engine_version "
    "model_id critical_policy unit axis_feasibility search".split())


def _check(value):
    if not value:
        raise ValueError("不可变轴伤基准引用无效或内容已变化")


def _keys(value, names):
    _check(isinstance(value, dict) and set(value) == set(names.split() if isinstance(names, str) else names))
    return value


def _integer(value, minimum=0, maximum=2**63-1):
    _check(type(value) is int and minimum <= value <= maximum)


def _text(value):
    _check(isinstance(value, str) and bool(value) and value.strip() == value
           and len(value.encode("utf8")) <= 256 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _hash(value):
    _check(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value))


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _reject(_value):
    raise ValueError("不可变轴伤基准引用含非有限数值")


def _payload_digest(payload):
    body = dict(payload)
    body["reference_damage"] = struct.pack(">d", float(payload["reference_damage"])).hex()
    body["anchor_world"] = {key: struct.pack(">d", float(value) if value else 0.0).hex()
                            for key, value in payload["anchor_world"].items()}
    return hashlib.sha256(_canonical(body).encode("utf8")).hexdigest()


def _validate(value):
    _keys(value, "format benchmark_id reference_sha256 payload")
    _check(value["format"] == REFERENCE_FORMAT)
    p = _keys(value["payload"], PAYLOAD_KEYS)
    _text(p["account_id"])
    _text(p["preset_id"])
    _text(p["engine_version"])
    _check(type(p["reference_damage"]) in (int, float) and math.isfinite(p["reference_damage"]) and p["reference_damage"] > 0)
    for key in ("target_sha256", "rules_sha256", "scoring_domain_sha256", "replay_input_sha256", "layout_sha256",
                "cultivation_sha256", "origin_request_sha256", "axis_snapshot_sha256"):
        _hash(p[key])
    raw_world = p["anchor_world"]
    names = "yaodao_attack_add quantao_crit_damage" + (" kaijia_defense_add" if isinstance(raw_world, dict) and "kaijia_defense_add" in raw_world else "")
    world = _keys(raw_world, names)
    limits = [("yaodao_attack_add", 20), ("quantao_crit_damage", .04)]
    if "kaijia_defense_add" in world:
        limits.append(("kaijia_defense_add", 30))
    for key, maximum in limits:
        bonus = world[key]
        _check(type(bonus) in (int, float) and math.isfinite(bonus) and 0 <= bonus <= maximum)
    component = _keys(p["component"], "component_executable_sha256 component_manifest_sha256")
    for digest in component.values():
        _hash(digest)
    _check(p["model_id"] == MODEL_ID and p["critical_policy"] == CRITICAL_POLICY and p["unit"] == DAMAGE_UNIT
           and p["axis_feasibility"] == "unverified")
    scope = _keys(p["scope"], "battle_record_id start_us end_us detail_scope")
    _integer(scope["battle_record_id"], 1)
    for key in ("start_us", "end_us"):
        if scope[key] is not None:
            _integer(scope[key])
    if scope["detail_scope"] is not None:
        _text(scope["detail_scope"])
    if scope["start_us"] is not None and scope["end_us"] is not None:
        _check(scope["end_us"] > scope["start_us"])
    s = _keys(p["search"], "search_scope optimality stop_reason evaluated_candidates unquantified_candidates evaluation_budget "
              "time_budget_ms elapsed_ms visited_presets total_presets neighborhood_complete")
    _check(s["search_scope"] == "target_presets_bounded_v1" and s["optimality"] == "bounded_best")
    _check(s["stop_reason"] in {"neighborhood_complete", "evaluation_budget", "time_budget", "preset_budget", "quantification_limited"})
    _integer(s["evaluation_budget"], 1, 100_000)
    _integer(s["evaluated_candidates"], 1, s["evaluation_budget"])
    _integer(s["unquantified_candidates"], 0, s["evaluated_candidates"])
    _integer(s["time_budget_ms"], 1, 600_000)
    _integer(s["elapsed_ms"])
    _integer(s["total_presets"], 1, 16)
    _integer(s["visited_presets"], 1, s["total_presets"])
    _check(type(s["neighborhood_complete"]) is bool and s["neighborhood_complete"] == (s["stop_reason"] == "neighborhood_complete"))
    if s["neighborhood_complete"]:
        _check(s["unquantified_candidates"] == 0 and s["visited_presets"] == s["total_presets"])
    _check(isinstance(p["build"], list) and len(p["build"]) == 4)
    actors, pieces = set(), set()
    for actor in p["build"]:
        _keys(actor, "character_id pieces")
        _integer(actor["character_id"], 1)
        _check(actor["character_id"] not in actors)
        actors.add(actor["character_id"])
        _check(isinstance(actor["pieces"], list) and 2 <= len(actor["pieces"]) <= 21)
        for piece in actor["pieces"]:
            _keys(piece, "piece_id item_id main_property_id sub_property_ids")
            _text(piece["piece_id"])
            _text(piece["item_id"])
            _check(piece["piece_id"] not in pieces)
            pieces.add(piece["piece_id"])
            if piece["main_property_id"] is not None:
                _text(piece["main_property_id"])
            stats = piece["sub_property_ids"]
            _check(isinstance(stats, list) and len(stats) <= 32)
            for stat in stats:
                _text(stat)
            _check(len(stats) == len(set(stats)))
    _hash(value["reference_sha256"])
    _check(value["reference_sha256"] == _payload_digest(p) and value["benchmark_id"] == "axis-benchmark-" + value["reference_sha256"])


@dataclass(frozen=True, slots=True)
class FrozenBenchmarkReference:
    body_json: str

    def __post_init__(self):
        try:
            _check(isinstance(self.body_json, str) and len(self.body_json.encode("utf8")) <= MAX_REFERENCE_BYTES)
            value = json.loads(self.body_json, object_pairs_hook=_pairs, parse_constant=_reject)
            _validate(value)
            value["payload"]["anchor_world"] = {key: float(n) if n else 0.0 for key, n in value["payload"]["anchor_world"].items()}
            object.__setattr__(self, "body_json", _canonical(value))
        except (TypeError, KeyError, OverflowError, RecursionError) as error:
            raise ValueError("不可变轴伤基准引用格式无效") from error

    def to_payload(self):
        return json.loads(self.body_json)

    @property
    def sha256(self):
        return self.to_payload()["reference_sha256"]

    @property
    def benchmark_id(self):
        return self.to_payload()["benchmark_id"]


def freeze_benchmark_reference(value):
    try:
        return FrozenBenchmarkReference(_canonical(value))
    except (TypeError, OverflowError, RecursionError) as error:
        raise ValueError("不可变轴伤基准引用编码失败") from error


def seal_benchmark_reference(payload):
    """仅供规范正文构造/导入校验；改变并重新摘要不等于组件已证明新分母正确。"""
    digest = _payload_digest(payload)
    return freeze_benchmark_reference({"format": REFERENCE_FORMAT, "benchmark_id": "axis-benchmark-" + digest,
                                      "reference_sha256": digest, "payload": payload})