# 对照Rust黄金及受控快照校验外层身份与原始字节；不把不透明事实展开为Calc伤害或事件身份。
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import zlib

import pytest

from src.domain.teams.graduation import axis_graduation_snapshot as module
from src.domain.teams.graduation.axis_graduation_snapshot import (
    FrozenAxisContextSnapshot,
    MAX_PAYLOAD_BYTES,
    freeze_axis_context_snapshot,
)


NTE_TEST_TIER = "core"
GOLDEN = Path(__file__).resolve().parents[2] / "fixtures/team_axis_context_snapshot_v1.json"


def fixture():
    return json.loads(GOLDEN.read_text("utf8"))


def seal(value):
    value["payload_sha256"] = hashlib.sha256(value["payload_json"].encode("utf8")).hexdigest()
    identity = {key: item for key, item in value.items() if key not in {"snapshot_sha256", "payload_json"}}
    value["snapshot_sha256"] = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")).hexdigest()
    return value


def test_shared_unicode_large_integer_and_opaque_order_have_exact_golden_identity():
    value = fixture()
    frozen = freeze_axis_context_snapshot(value)
    assert frozen.sha256 == "d50b316e350e0c0569e580598e8c9904dbb4f21cc9d3950ceed931b1d77be7da"
    assert frozen.to_payload()["scope"]["battle_record_id"] == 2**53+1
    assert frozen.to_payload()["payload_json"] == value["payload_json"]
    assert '"b":2,"a":1' in frozen.to_payload()["payload_json"]
    value["scope"]["battle_record_id"] = 1
    frozen.to_payload()["scope"]["battle_record_id"] = 2
    assert frozen.to_payload()["scope"]["battle_record_id"] == 2**53+1
    with pytest.raises(FrozenInstanceError):
        frozen.envelope_json = "changed"


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(format="other"),
    lambda v: v.update(account_id=""),
    lambda v: v.update(dataset_id=""),
    lambda v: v.update(static_schema=True),
    lambda v: v.update(static_sha256="A"*64),
    lambda v: v.update(snapshot_sha256="0"*64),
    lambda v: v.update(source_input_digest="bad"),
    lambda v: v.update(payload_sha256="0"*64),
    lambda v: v.update(payload_json=v["payload_json"]+" "),
    lambda v: v.update(extra="ignored"),
    lambda v: v["scope"].update(battle_record_id=True),
    lambda v: v["scope"].update(start_us=-1),
    lambda v: v["scope"].update(start_us=2, end_us=1),
    lambda v: v["scope"].update(detail_scope=False),
])
def test_metadata_body_mismatch_and_unknown_fields_are_rejected(mutate):
    value = fixture()
    mutate(value)
    with pytest.raises(ValueError):
        freeze_axis_context_snapshot(value)


def test_rehashed_invalid_metadata_and_oversize_are_still_rejected():
    value = fixture()
    value["scope"]["battle_record_id"] = True
    with pytest.raises(ValueError):
        freeze_axis_context_snapshot(seal(value))
    value = fixture()
    value["payload_json"] = "x"*(MAX_PAYLOAD_BYTES+1)
    with pytest.raises(ValueError):
        freeze_axis_context_snapshot(seal(value))


def test_duplicate_outer_fields_cannot_be_lost_during_decode():
    raw = json.dumps(fixture(), ensure_ascii=False)
    duplicate = raw.replace('"format":', '"format":"other","format":', 1)
    with pytest.raises(ValueError):
        FrozenAxisContextSnapshot(duplicate)


def test_calc_keeps_facts_opaque_and_only_normalizes_outer_envelope(monkeypatch):
    value = fixture()
    body = value["payload_json"]
    original = json.loads
    def checked(raw, **kwargs):
        assert raw != body, "raw facts must stay opaque in Calc"
        return original(raw, **kwargs)
    monkeypatch.setattr(module.json, "loads", checked)
    frozen = freeze_axis_context_snapshot(value)
    assert frozen.to_payload()["payload_json"] == body


def test_compact_facts_remain_opaque_and_byte_identical_in_calc(monkeypatch):
    value = fixture()
    raw = value["payload_json"].encode("utf8")
    body = json.dumps({"codec": "zlib_hex_v1", "decoded_bytes": len(raw),
        "decoded_sha256": hashlib.sha256(raw).hexdigest(), "data_hex": zlib.compress(raw).hex()},
        separators=(",", ":"))
    value["payload_json"] = body
    seal(value)
    original = json.loads
    def outer_only(text, **kwargs):
        assert text != body, "Calc must not unpack native compact facts"
        return original(text, **kwargs)
    monkeypatch.setattr(module.json, "loads", outer_only)
    frozen = freeze_axis_context_snapshot(value)
    assert frozen.to_payload()["payload_json"] == body
    assert frozen.to_payload()["payload_sha256"] == hashlib.sha256(body.encode("utf8")).hexdigest()


def test_controlled_native_snapshot_cross_language_envelope_and_deleted_source():
    case = os.environ.get("NTE_TEAM_AXIS_GRADUATION_SNAPSHOT_CASE")
    if not case:
        pytest.skip("explicit controlled native snapshot case required")
    root = Path(case).parent
    exported = json.loads((root / "synthetic-axis-snapshot-result-v1.json").read_text("utf8"))
    assert exported["fixture_kind"] == "synthetic_axis_context_snapshot_v1"
    frozen = freeze_axis_context_snapshot(exported["snapshot"])
    assert frozen.to_payload() == exported["snapshot"]
    assert not Path(json.loads(Path(case).read_text("utf8"))["source_path"]).exists()
    assert exported["evaluation"]["current"]["damage"] > 0 and exported["evaluation"]["theory"]["damage"] > 0
