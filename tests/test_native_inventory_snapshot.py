# 验证原生背包分页冻结、有界读取、取消以及不提升未知完整性。
from copy import deepcopy

import pytest

from src.integrations.native_inventory_snapshot import read_native_projection, NativeSnapshotPending
from src.integrations.nte_core_protocol import NteCoreProtocolError


def fixture(count=130):
    header = dict(providerId="test", domain="inventory", snapshotId="9", generation="2",
                  domainKey="test-domain", observedUnixUs="1000", observedMonotonicMs="50",
                  enabled=True, ready=True, state="ready", recordCount=count, sourceRecordCount=count,
                  enumerationComplete=True, complete=False, sourceCoverage="unknown",
                  changeCoverage="unknown", missing=["mapping_unverified"], failed=False, truncated=False,
                  collectionComplete=True, collectionScope="EQUIP", characterRefsComplete=True)
    calls = []
    def call(method, params):
        calls.append((method, params))
        if method.endswith("refresh"):
            return deepcopy(header)
        offset = params["offset"]
        end = min(offset + params["limit"], count)
        return dict(deepcopy(header), items=[{"uid": {"slot": n + 1, "serial": 1}} for n in range(offset, end)], characters=[], projectionComplete=True, referencedItemUids=[],
                    nextOffset=end if end < count else None)
    return header, calls, call


@pytest.mark.parametrize("count", [0, 1, 64, 65, 1878])
def test_reads_declared_collection_without_inventing_coverage(count):
    header, calls, call = fixture(count)
    result = read_native_projection(call, lambda: None, domain="inventory")
    assert len(result["items"]) == count
    assert result["complete"] is False and result["changeCoverage"] == "unknown"
    assert result["missing"] == header["missing"]
    assert len(calls) == 1 + max(1, (count + 63) // 64)


@pytest.mark.parametrize("field,value", [("snapshotId", "10"), ("providerId", "other"),
    ("generation", "3"), ("domainKey", "other"), ("observedUnixUs", "1001"),
    ("complete", True), ("missing", []), ("changeCoverage", "complete")])
def test_rejects_mixed_pages(field, value):
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        if method.endswith("page") and params["offset"] > 0:
            result[field] = value
        return result
    with pytest.raises(NteCoreProtocolError):
        read_native_projection(changed, lambda: None, domain="inventory")


@pytest.mark.parametrize("next_offset", [0, 63, True, None, "64"])
def test_rejects_invalid_pagination(next_offset):
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        if method.endswith("page"):
            result["nextOffset"] = next_offset
        return result
    with pytest.raises(NteCoreProtocolError):
        read_native_projection(changed, lambda: None, domain="inventory")


@pytest.mark.parametrize("field,value", [("ready", False), ("enabled", False),
    ("truncated", True), ("failed", True), ("enumerationComplete", False)])
def test_pending_does_not_read_pages(field, value):
    header, calls, call = fixture()
    header[field] = value
    with pytest.raises(NativeSnapshotPending):
        read_native_projection(call, lambda: None, domain="inventory")
    assert len(calls) == 1


def test_cancellation_after_page_discards_result():
    _, calls, call = fixture()
    def check():
        if len(calls) >= 2:
            raise PermissionError("revoked")
    with pytest.raises(PermissionError):
        read_native_projection(call, check, domain="inventory")
    assert len(calls) == 2


def test_provider_failure_is_not_empty_success():
    _, _, call = fixture()
    def fail(method, params):
        if method.endswith("page"):
            raise RuntimeError("source_changed")
        return call(method, params)
    with pytest.raises(RuntimeError, match="source_changed"):
        read_native_projection(fail, lambda: None, domain="inventory")


@pytest.mark.parametrize("field,value", [("projectionComplete", False), ("projectionComplete", None)])
def test_unproven_inventory_projection_is_pending(field, value):
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        result[field] = value
        return result
    with pytest.raises(NativeSnapshotPending):
        read_native_projection(changed, lambda: None, domain="inventory")


def test_character_pages_can_omit_unresolved_rows_without_filling_defaults():
    header, calls, call = fixture(65)
    header["domain"] = "character"
    def character_call(method, params):
        result = call(method, params)
        if method.endswith("page"):
            result.pop("items")
            result["profiles"] = [{"character_id": 1020, "character_level": 20}] if params["offset"] == 0 else []
        return result
    result = read_native_projection(character_call, lambda: None, domain="character")
    assert result["profiles"] == [{"character_id": 1020, "character_level": 20}]
    assert calls[-1][1]["offset"] == 64


def test_changed_same_observation_character_bindings_reject_entire_inventory():
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        if method.endswith("page") and params["offset"]:
            result["characters"] = [{"character_id": 1020, "uid": {"slot": 1, "serial": 2}}]
        return result
    with pytest.raises(NteCoreProtocolError):
        read_native_projection(changed, lambda: None, domain="inventory")


@pytest.mark.parametrize("references", [[{"slot": 999, "serial": 1}], [{"slot": 1, "serial": 1}] * 2])
def test_reference_proof_must_be_unique_and_covered_by_full_inventory(references):
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        result["referencedItemUids"] = references
        return result
    with pytest.raises(NteCoreProtocolError):
        read_native_projection(changed, lambda: None, domain="inventory")


def test_duplicate_uid_across_pages_is_rejected_before_event():
    _, _, call = fixture()
    def changed(method, params):
        result = call(method, params)
        if method.endswith("page") and params["offset"]:
            result["items"][0]["uid"] = {"slot": 1, "serial": 1}
        return result
    with pytest.raises(NteCoreProtocolError):
        read_native_projection(changed, lambda: None, domain="inventory")


@pytest.mark.parametrize("domain", ["inventory", "character"])
def test_core_serializer_business_fixture_is_consumable(domain):
    import json
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent / "fixtures/native_business_222.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    rows = fixture[domain]
    def call(method, params):
        if method == "native.snapshot.refresh":
            assert params == {"domain": domain}
            return deepcopy(rows["refresh"]["result"])
        assert method == rows["request"]["method"] and params == rows["request"]["params"]
        return deepcopy(rows["response"]["result"])
    result = read_native_projection(call, lambda: None, domain=domain)
    if domain == "character":
        assert result["profiles"] == [{"character_id": 1020, "character_level": 20}]
        assert "breakthrough_stage_unknown" in result["projectionMissing"]
    else:
        item = result["items"][0]
        assert item["kind"] == "core" and item["equipped_character_id"] == 1020
        assert item["sub_stats"][1]["value"] == rows["response"]["result"]["items"][0]["sub_stats"][1]["value"]
        assert result["statProvenance"]["main"] == "static_catalog_curve"
        assert result["projectionComplete"] is True and result["complete"] is False


def test_conflicting_character_across_pages_is_not_a_partial_update():
    header, _, call = fixture(65)
    header["domain"] = "character"
    def character_call(method, params):
        result = call(method, params)
        if method.endswith("page"):
            result.pop("items")
            result["profiles"] = [{"character_id": 1020, "character_level": 20 if params["offset"] == 0 else 40}]
        return result
    with pytest.raises(NteCoreProtocolError, match="重复角色"):
        read_native_projection(character_call, lambda: None, domain="character")
