# 验证随页原始证据的多页等价、残缺拒绝和共享基线生命周期。
from copy import deepcopy

import pytest

from src.integrations.native_inline_snapshot import INLINE_RAW_CAPABILITY
from src.integrations.nte_core_protocol import NteCoreProtocolError
from src.services.native_game_session import NativeGameSession
from tests.test_native_shared_baseline import BaselineCore


class PagedCore(BaselineCore):
    def __init__(self, inline, count=129, corrupt=None):
        super().__init__()
        self.count, self.corrupt = count, corrupt
        if inline:
            self.hello_result["capabilities"].append(INLINE_RAW_CAPABILITY)

    def header(self, domain):
        return {**super().header(domain), "recordCount": self.count, "sourceRecordCount": self.count}

    def call(self, method, params, **kwargs):
        if not method.endswith(".page"):
            return super().call(method, params, **kwargs)
        self.calls.append((method, deepcopy(params)))
        raw = method == "native.snapshot.page"
        domain = params["domain"] if raw else method.split(".")[1]
        offset = params["offset"]
        assert params["snapshotId"] == self.frozen[domain]["snapshotId"]
        assert self.cursors[domain][not raw] == offset
        end = min(offset + params["limit"], self.count)
        next_offset = end if end < self.count else None
        self.cursors[domain][not raw] = next_offset
        row = {**deepcopy(self.frozen[domain]), "nextOffset": next_offset}
        records = [{"ItemID": str(1004 + i), "nested": {"value": i}} for i in range(offset, end)]
        if raw:
            return {**row, "records": records}
        if domain == "inventory":
            row.update(items=[{"uid": {"slot": i + 1, "serial": 2}} for i in range(offset, end)],
                       characters=[{"character_id": 1004}], referencedItemUids=[], projectionComplete=True)
        else:
            row["profiles"] = [{"character_id": 1004 + i, "character_level": 70} for i in range(offset, end)]
        if INLINE_RAW_CAPABILITY in self.hello_result["capabilities"]:
            row["rawRecords"] = records
            if offset == 64 and self.corrupt:
                if self.corrupt == "missing":
                    del row["rawRecords"]
                elif self.corrupt == "short":
                    row["rawRecords"].pop()
                else:
                    row["rawRecords"][0] = None
        return row


@pytest.mark.parametrize("domain", ["inventory", "character"])
@pytest.mark.parametrize("count", [0, 1, 64, 65, 129])
def test_inline_and_separate_pages_build_identical_owned_baselines(domain, count):
    results = []
    for inline in (False, True):
        core = PagedCore(inline, count)
        session = NativeGameSession(lambda: core, lambda _: None)
        try:
            connected = session._connect()
            projection = session._read_projection(connected, domain)
            cached = session._baseline.get(domain, core.call("native.snapshot.status", {}))
            results.append((projection, cached))
            page_count = max(1, (count + 63) // 64)
            assert sum(method == "native.snapshot.page" for method, _ in core.calls) == (0 if inline else page_count)
            assert sum(method == f"native.{domain}.page" for method, _ in core.calls) == page_count
            assert "rawRecords" not in projection
            assert len(cached["snapshot"]["records"]) == count
            session._read_projection(connected, domain)
            assert core.refresh_counts() == {domain: 1}
            if count:
                cached["snapshot"]["records"][0]["nested"]["value"] = -1
                assert session._baseline.get(domain, core.call("native.snapshot.status", {}))["snapshot"]["records"][0]["nested"]["value"] == 0
        finally:
            session.close()
    assert results[0] == results[1]


@pytest.mark.parametrize("corrupt", ["missing", "short", "invalid"])
def test_advertised_inline_failure_does_not_retry_raw_or_publish(corrupt):
    core = PagedCore(True, corrupt=corrupt)
    session = NativeGameSession(lambda: core, lambda _: None)
    baseline = session._baseline
    try:
        with pytest.raises(NteCoreProtocolError):
            session.read_character_profiles()
        assert baseline.get("character", core.call("native.snapshot.status", {})) is None
        assert not any(method == "native.snapshot.page" for method, _ in core.calls)
    finally:
        session.close()


def test_live_revision_change_discards_result_and_can_retry():
    core = PagedCore(True)
    original = core.call

    def call(method, params, **kwargs):
        row = original(method, params, **kwargs)
        if method == "native.character.page" and row["nextOffset"] is None:
            core.revisions["character"] = "2"
        return row

    core.call = call
    session = NativeGameSession(lambda: core, lambda _: None)
    try:
        from src.integrations.native_inventory_snapshot import NativeSnapshotPending
        with pytest.raises(NativeSnapshotPending):
            session.read_character_profiles()
        assert session._baseline.get("character", core.call("native.snapshot.status", {})) is None
        assert session.read_character_profiles()["revision"] == "2"
    finally:
        session.close()


def test_cold_battle_freeze_uses_inline_pages_without_second_inventory_read():
    from src.integrations.native_battle_snapshot import freeze_native_battle_snapshot
    old, new = PagedCore(False), PagedCore(True)
    results = [freeze_native_battle_snapshot(core, lambda: None) for core in (old, new)]
    assert results[0] == results[1]
    for domain in ("inventory", "character"):
        assert not any(method == "native.snapshot.page" and params["domain"] == domain
                       for method, params in new.calls)
    assert new.refresh_counts() == dict.fromkeys(new.revisions, 1)


def test_mapping_failure_still_retains_raw_evidence():
    from src.integrations.native_battle_snapshot import freeze_native_battle_snapshot
    from src.integrations.nte_core_protocol import NteCoreRpcError
    core = PagedCore(True)
    original = core.call

    def call(method, params, **kwargs):
        if method == "native.inventory.page":
            raise NteCoreRpcError({"code": -32000, "message": "Core error",
                                   "data": {"domain_code": "NATIVE_MAPPING_UNSUPPORTED"}})
        return original(method, params, **kwargs)

    core.call = call
    result = freeze_native_battle_snapshot(core, lambda: None)
    assert len(result["domains"]["inventory"]["records"]) == 129
    assert "inventory_projection_unavailable" in result["missing"]
    assert "inventory_projection" not in result
