# 验证正式队伍预检不丢队外角色、不信任残缺分区，并沿用原生取消和组件身份边界。
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.nte_analysis_core import NteAnalysisCoreClient, NativeAnalysisError, NativeAnalysisCancelled

NTE_TEST_TIER = "core"


def selector():
    return dict(account_id="public", generation=1, battle_record_id=1,
                user_database_path=str(Path("user.sqlite3").resolve()),
                static_database_path=str(Path("static.sqlite3").resolve()),
                semantics_path=str(Path("semantics.json").resolve()), detail_level="hit")


def response():
    return dict(schema_version="nte-analysis-response-v1", engine_version="0.3.0", dataset_version="public",
                batch_kind="battle_axis_team_v1", version=1, ready=True, reason=None,
                basis="frozen_native_team_v1", team_character_ids=[1, 2, 3, 4], selected_character_ids=[1],
                fixed_character_ids=[2, 3, 4], outside_character_ids=[], scopes=["combat"],
                source_input_digest="a" * 64, start_us=None, end_us=None, detail_scope=None)


@pytest.fixture
def client(tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.write_bytes(b"wire fixture")
    return NteAnalysisCoreClient(exe, "public", capabilities=frozenset({"battle_axis_team_v1"}))


def test_ready_subset_and_outside_members_are_retained(client):
    data = response()
    with patch.object(client, "_run", return_value=json.dumps(data).encode()) as run:
        result = client.resolve_battle_axis_team(selector(), [1])
        assert result["fixed_character_ids"] == [2, 3, 4]
        assert json.loads(run.call_args.args[0])["request"]["selected_character_ids"] == [1]
    data.update(ready=False, reason="axis_selected_outside_team", selected_character_ids=[1, 9], outside_character_ids=[9])
    with patch.object(client, "_run", return_value=json.dumps(data).encode()):
        result = client.resolve_battle_axis_team(selector(), [9, 1])
        assert result["outside_character_ids"] == [9] and result["selected_character_ids"] == [1, 9]


def test_source_gap_is_not_a_ready_empty_team(client):
    data = response()
    data.update(ready=False, reason="axis_team_context_incomplete", team_character_ids=[], fixed_character_ids=[], scopes=[])
    with patch.object(client, "_run", return_value=json.dumps(data).encode()):
        assert not client.resolve_battle_axis_team(selector(), [1])["ready"]


def test_fixed_team_requires_complete_partition_and_inventory_ownership(client):
    client.supports_battle_axis_fixed_team = True
    inventory = [dict(kind=kind, item_id=kind, uid_slot=slot, uid_serial=cid)
                 for cid in (2, 3, 4) for kind, slot in (("core", 4), ("module", 3))]
    members = [dict(character_id=cid, equipment_uids=[f"nte-core-4-{cid}", f"nte-module-3-{cid}"])
               for cid in (2, 3, 4)]
    data = {**response(), "batch_kind": "battle_axis_fixed_team_v1", "anchor_mode": "original",
            "fixed_ready": True, "fixed_reason": None, "fixed_build_sha256": "b" * 64,
            "fixed_members": members, "reserved_uids": sorted(u for m in members for u in m["equipment_uids"])}
    with patch.object(client, "_run", return_value=json.dumps(data).encode()):
        result = client.resolve_battle_axis_fixed_team(selector(), [1], "original", inventory)
        assert result["fixed_members"] == members
    empty_member = deepcopy(data)
    empty_member["fixed_members"][0]["equipment_uids"] = []
    empty_member["reserved_uids"] = sorted(u for member in empty_member["fixed_members"] for u in member["equipment_uids"])
    with patch.object(client, "_run", return_value=json.dumps(empty_member).encode()):
        assert client.resolve_battle_axis_fixed_team(selector(), [1], "original", inventory)["fixed_ready"]
    for change in ({"fixed_members": members[:-1]}, {"reserved_uids": data["reserved_uids"][:-1]},
                   {"anchor_mode": "effective"}, {"fixed_build_sha256": None}):
        with patch.object(client, "_run", return_value=json.dumps({**data, **change}).encode()):
            with pytest.raises(NativeAnalysisError):
                client.resolve_battle_axis_fixed_team(selector(), [1], "original", inventory)
    gap = {**data, "fixed_ready": False, "fixed_reason": "axis_fixed_inventory_missing",
           "fixed_members": [], "reserved_uids": [], "fixed_build_sha256": None}
    with patch.object(client, "_run", return_value=json.dumps(gap).encode()):
        assert not client.resolve_battle_axis_fixed_team(selector(), [1], "original", inventory)["fixed_ready"]
    client.supports_battle_axis_fixed_team = False
    with patch.object(client, "_run", side_effect=AssertionError("old component must not dispatch")):
        with pytest.raises(NativeAnalysisError):
            client.resolve_battle_axis_fixed_team(selector(), [1], "original", inventory)


@pytest.mark.parametrize("change", [
    {"team_character_ids": [1, 2]}, {"fixed_character_ids": [1, 2, 3]},
    {"selected_character_ids": [2]}, {"outside_character_ids": [1]},
    {"source_input_digest": "unknown"}, {"ready": 1}, {"start_us": 10},
    {"scopes": ["unknown"]}, {"dataset_version": "other"},
])
def test_malformed_or_stale_result_is_rejected(client, change):
    data = {**response(), **deepcopy(change)}
    with patch.object(client, "_run", return_value=json.dumps(data).encode()):
        with pytest.raises(NativeAnalysisError):
            client.resolve_battle_axis_team(selector(), [1])


def test_invalid_selection_scope_missing_capability_and_cancel_never_dispatch(client):
    with patch.object(client, "_run", side_effect=AssertionError("must not dispatch")):
        for selected, changes in [([], {}), ([True], {}), ([1, 1], {}), ([1], {"start_us": 0}),
                                  ([1], {"detail_scope": "current"})]:
            with pytest.raises(NativeAnalysisError):
                client.resolve_battle_axis_team({**selector(), **changes}, selected)
        client.supports_battle_axis_team = False
        with pytest.raises(NativeAnalysisError):
            client.resolve_battle_axis_team(selector(), [1])
        client.supports_battle_axis_team = True
        def cancel():
            raise NativeAnalysisCancelled("fixture cancellation")
        with pytest.raises(NativeAnalysisCancelled):
            client.resolve_battle_axis_team(selector(), [1], checkpoint=cancel)

