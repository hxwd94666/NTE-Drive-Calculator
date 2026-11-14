# 封存Rust轴快照的不透明正文与外层身份；只验字节完整性，不拆逐击、推断环境或重算战报身份。
from dataclasses import dataclass
import hashlib
import json


FORMAT = "team_axis_context_snapshot_v1"
MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
KEYS = frozenset("format account_id scope dataset_id static_schema static_sha256 engine_version source_input_digest payload_sha256 snapshot_sha256 payload_json".split())
MAX_INTEGER = 2**63-1


def _require(value):
    if not value:
        raise ValueError("轴快照正文或身份无效")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _text(value):
    _require(isinstance(value, str) and bool(value) and value.strip() == value
             and len(value.encode("utf8")) <= 256 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value))


def _hash(value):
    _require(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value))


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    _require(type(value) is int and minimum <= value <= maximum)


def _reject(_value):
    raise ValueError("轴快照外层含非有限数值")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _validate(value):
    _require(isinstance(value, dict) and set(value) == KEYS and value["format"] == FORMAT)
    for key in ("account_id", "dataset_id", "engine_version"):
        _text(value[key])
    _integer(value["static_schema"], 1, 2**32-1)
    for key in ("static_sha256", "source_input_digest", "payload_sha256", "snapshot_sha256"):
        _hash(value[key])
    scope = value["scope"]
    _require(isinstance(scope, dict) and set(scope) == {"battle_record_id", "start_us", "end_us", "detail_scope"})
    _integer(scope["battle_record_id"], 1)
    for key in ("start_us", "end_us"):
        if scope[key] is not None:
            _integer(scope[key])
    if scope["start_us"] is not None and scope["end_us"] is not None:
        _require(scope["start_us"] < scope["end_us"])
    if scope["detail_scope"] is not None:
        _text(scope["detail_scope"])
    body = value["payload_json"]
    _require(isinstance(body, str))
    raw = body.encode("utf8")
    _require(len(raw) <= MAX_PAYLOAD_BYTES and hashlib.sha256(raw).hexdigest() == value["payload_sha256"])
    identity = {key: item for key, item in value.items() if key not in {"payload_json", "snapshot_sha256"}}
    _require(hashlib.sha256(_canonical(identity).encode("utf8")).hexdigest() == value["snapshot_sha256"])
    _require(len(_canonical(value).encode("utf8")) <= MAX_SNAPSHOT_BYTES)


@dataclass(frozen=True, slots=True)
class FrozenAxisContextSnapshot:
    envelope_json: str

    def __post_init__(self):
        _require(isinstance(self.envelope_json, str) and len(self.envelope_json.encode("utf8")) <= MAX_SNAPSHOT_BYTES)
        value = json.loads(self.envelope_json, object_pairs_hook=_pairs, parse_constant=_reject)
        _validate(value)
        # 只规范外层。payload_json是字符串，内部浮点拼写和属性插入顺序逐字节保留。
        object.__setattr__(self, "envelope_json", _canonical(value))

    def to_payload(self):
        return json.loads(self.envelope_json)

    @property
    def sha256(self):
        return self.to_payload()["snapshot_sha256"]


def freeze_axis_context_snapshot(value):
    _validate(value)
    return FrozenAxisContextSnapshot(_canonical(value))
