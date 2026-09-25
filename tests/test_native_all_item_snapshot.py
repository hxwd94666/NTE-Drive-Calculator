# 验证全部物品分页、源修订复核和取消，拒绝把装备页冒充全部物品。
from copy import deepcopy

import pytest

from src.integrations.native_all_item_snapshot import read_all_item_snapshot
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.integrations.nte_core_protocol import NteCoreProtocolError
from tests.test_all_item_snapshot_storage import all_items


class AllItemSource:
    def __init__(self, count=65):
        self.raw = all_items(count)
        self.calls = []
        self.mutate_page = lambda page: None
        self.revision = self.raw["revision"]

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "native.snapshot.status":
            assert params == {}
            return {"providerId": self.raw["providerId"], "domains": [dict(domain="inventory", ready=True,
                    dirty=False, revision=self.revision, domainKey=self.raw["domainKey"])]}
        assert params["scope"] == "all_items" and params["domain"] == "inventory"
        result = {key: deepcopy(value) for key, value in self.raw.items() if key != "records"}
        if method == "native.snapshot.page":
            offset, limit = params["offset"], params["limit"]
            result["records"] = deepcopy(self.raw["records"][offset:offset + limit])
            end = offset + len(result["records"])
            result["nextOffset"] = end if end < self.raw["recordCount"] else None
            self.mutate_page(result)
        return result


@pytest.mark.parametrize("count", [0, 1, 65])
def test_reads_all_pages_and_preserves_raw_unknowns(count):
    source = AllItemSource(count)
    assert read_all_item_snapshot(source.call, lambda: None) == source.raw
    assert source.calls[-1][0] == "native.snapshot.status"


@pytest.mark.parametrize("key,value", [("collectionScope", "EQUIP"), ("sourceRecordCount", 66),
                                     ("collectionComplete", False), ("revision", "4")])
def test_page_identity_or_scope_drift_discards_entire_read(key, value):
    source = AllItemSource()
    source.mutate_page = lambda page: page.update({key: value})
    with pytest.raises(NteCoreProtocolError):
        read_all_item_snapshot(source.call, lambda: None)


def test_source_change_after_last_page_discards_read():
    source = AllItemSource()
    source.revision = "4"
    with pytest.raises(NativeSnapshotPending):
        read_all_item_snapshot(source.call, lambda: None)


def test_cancellation_stops_before_next_page():
    source = AllItemSource()
    def check():
        if any(method == "native.snapshot.page" for method, _ in source.calls):
            raise InterruptedError("cancelled")
    with pytest.raises(InterruptedError):
        read_all_item_snapshot(source.call, check)
    assert sum(method == "native.snapshot.page" for method, _ in source.calls) == 1
