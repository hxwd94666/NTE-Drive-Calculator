# 验证自动采集来源选择、逐击身份保真与未知血量证据的入库边界。
from __future__ import annotations

from contextlib import closing
from unittest.mock import patch
import json
import sqlite3
import sys

import pytest

from src.integrations.nte_core import NteCoreClient, NteCoreProcessError, NteCoreProtocolError
from src.integrations.nte_core_battle import parse_battle_axis, parse_battle_record
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.native_execution_fixture import execution_evidence


def test_old_native_provider_gives_actionable_restart_error() -> None:
    script = ('import sys; '
              'sys.stderr.write("error: native capture native_context_capability_required_restart_game\\n"); '
              'sys.stderr.flush(); sys.exit(1)')
    client = NteCoreClient(command=[sys.executable, '-c', script])
    with pytest.raises(NteCoreProcessError, match='重启游戏'):
        client.start()
    assert not client.is_running


def test_auto_capture_selects_dll_only_when_endpoint_exists() -> None:
    client = NteCoreClient(command=["test-core"], required_source="native")
    with patch("src.integrations.nte_core.native_capture_game_pid", return_value=42):
        assert client._serve_command() == ["test-core", "serve-native", "--stdio", "--game-pid", "42"]
    assert client.native_capture
    with patch.object(client, "call", return_value={}) as call:
        client.start_capture(profile="combat", raw_capture="enabled")
    assert call.call_args.args[1]["raw_capture"] == "enabled"
    with patch.object(client, "call", return_value={}) as call:
        client.start_capture(profile="combat", raw_capture="disabled")
    assert call.call_args.args[1]["raw_capture"] == "disabled"


def test_native_capture_receives_account_raw_capture_directory(tmp_path) -> None:
    client = NteCoreClient(command=["test-core"], required_source="native", data_dir=tmp_path)
    with patch("src.integrations.nte_core.native_capture_game_pid", return_value=42):
        assert client._serve_command() == [
            "test-core", "serve-native", "--stdio", "--game-pid", "42", "--data-dir", str(tmp_path),
        ]


def test_absent_native_provider_and_detection_failure_never_fall_back() -> None:
    client = NteCoreClient(command=["test-core"], required_source="native")
    with patch("src.integrations.nte_core.native_capture_game_pid", return_value=None):
        with pytest.raises(NteCoreProcessError, match="不会切换"):
            client._serve_command()
    assert not client.native_capture
    with patch("src.integrations.nte_core.native_capture_game_pid", side_effect=NteCoreProcessError("identity failed")):
        with pytest.raises(NteCoreProcessError, match="identity failed"):
            client._serve_command()


def test_inventory_client_does_not_probe_or_switch_to_native() -> None:
    client = NteCoreClient(command=["test-core"])
    with patch("src.integrations.nte_core.native_capture_game_pid") as probe:
        assert client._serve_command() == ["test-core", "serve", "--stdio"]
    probe.assert_not_called()


_OBSERVATION_CASES = [
    {"hitObservedUnixUs": "99000000", "settlementObservedUnixUs": "101000000"},
    {"hitObservedUnixUs": None, "settlementObservedUnixUs": None},
    {},
    {"executionEvidence": execution_evidence()},
    {"executionEvidence": None},
]


def _page(observations: dict | None = None) -> dict:
    if observations is None:
        observations = _OBSERVATION_CASES[0]
    return {
        "contract_version": 5, "battle_record_id": "native-battle", "generation": "1",
        "finalized": True, "complete": True, "total_hits": "1", "rows": [{
            "battle_record_id": "native-battle", "sequence": "9007199254740993",
            "timestamp_unix": 100.0, "relative_time_seconds": 0.0,
            "direction": "outgoing", "damage": 10.0, "total_damage": 10.0,
            "overkill_damage": None, "max_hp_reduction": None,
            "native_capture": {
                "providerId": "provider", "captureId": "capture", "hitId": "9007199254740993",
                "rawHit": {"hitId": "9007199254740993", "unixUs": "100000000", "victimMaxHp": None,
                           **observations,
                           "attackerEffects": {"complete": True, "effects": []},
                           "victimEffects": None, "futureField": {"retained": True}},
            },
        }],
    }


@pytest.mark.parametrize("observations", _OBSERVATION_CASES,
                         ids=["distinct", "unknown", "legacy", "execution", "no_execution"])
def test_native_axis_preserves_exact_identity_empty_missing_and_extensions(observations) -> None:
    source = _page(observations)
    row = parse_battle_axis(source)["rows"][0]
    assert row["native_capture"] == source["rows"][0]["native_capture"]
    assert row["sequence"] == "9007199254740993"
    assert row["max_hp_reduction"] is None
    assert row["overkill_damage"] is None
    assert row["timestamp_unix"] == 100.0
    assert row["relative_time_seconds"] == 0.0
    assert row["damage"] == row["total_damage"] == 10.0


def test_native_axis_rejects_renumbered_hit() -> None:
    source = _page()
    source["rows"][0]["sequence"] = "1"
    with pytest.raises(NteCoreProtocolError, match="identity"):
        parse_battle_axis(source)


def test_packet_axis_does_not_gain_nullable_calibration_contract() -> None:
    source = _page()
    del source["rows"][0]["native_capture"]
    with pytest.raises(NteCoreProtocolError):
        parse_battle_axis(source)


def test_record_keeps_source_coverage_separate_from_received_axis() -> None:
    native = {
        "complete": False, "sourceCoverage": "unknown", "integrityReasons": ["source_coverage_unverified"],
        "rawCapture": {"path": "synthetic/raw.jsonl", "complete": False, "accepted": "4", "written": "3",
                       "partial": "1", "rejected": "0", "error": "write_failed"},
    }
    record = parse_battle_record({
        "contract_version": 5, "battle_record_id": "native-battle", "generation": "1",
        "state": "finalized", "source": "native", "axis_complete": True,
        "time_stop_intervals": [], "summary": {}, "native_capture": native,
    })
    assert record["axis_complete"]
    assert record["native_capture"] == native


@pytest.mark.parametrize("observations", _OBSERVATION_CASES,
                         ids=["distinct", "unknown", "legacy", "execution", "no_execution"])
def test_native_evidence_survives_final_axis_replacement_and_database_reopen(tmp_path, observations) -> None:
    source = _page(observations)
    page = parse_battle_axis(source)
    database = tmp_path / "native.sqlite3"
    with UserDataDao(database, account_id="synthetic") as dao:
        dao.begin_battle_axis_capture(capture_operation_id="op", captured_at_utc="2026-09-08T00:00:00+00:00", account_generation=1)
        dao.append_battle_axis_page(capture_operation_id="op", page=page)
        result = dao.replace_staged_battle_axis(capture_operation_id="op", pages=(page,), source_generation="1")
        assert result["complete"]
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("SELECT sequence_text, max_hp_reduction, raw_hit_json FROM battle_hit_evidence").fetchone()
    assert row[0] == "9007199254740993"
    assert row[1] is None
    assert json.loads(row[2])["native_capture"] == source["rows"][0]["native_capture"]
    saved = json.loads(row[2])
    assert saved["timestamp_unix"] == 100.0
    assert saved["relative_time_seconds"] == 0.0
    assert saved["damage"] == saved["total_damage"] == 10.0
