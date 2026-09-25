# 读取独立 all_items 游标，逐页验证范围并在结束时复核源修订。
from src.domain.all_item_snapshot import validate_all_item_snapshot
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.integrations.native_raw_snapshot import IDENTITY_FIELDS, read_native_raw_domain
from src.integrations.nte_core_protocol import NteCoreProtocolError


def read_all_item_snapshot(call, check):
    def scoped(method, params):
        check()
        return call(method, {**params, "scope": "all_items"})

    header = scoped("native.snapshot.refresh", {"domain": "inventory"})
    try:
        validate_all_item_snapshot(header, with_records=False)
    except ValueError as error:
        raise NativeSnapshotPending("全部物品集合尚未完成，保留上次归档。") from error
    snapshot = read_native_raw_domain(
        scoped, check, "inventory", header=header,
        identity_fields=(*IDENTITY_FIELDS, "sourceRecordCount", "collectionScope", "collectionComplete"),
    )
    check()
    status = call("native.snapshot.status", {})
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("全部物品快照复核状态无效。")
    rows = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == "inventory"]
    if len(rows) != 1:
        raise NteCoreProtocolError("全部物品快照复核缺少唯一数据域。")
    current = rows[0]
    if (status.get("providerId") != snapshot["providerId"]
            or current.get("ready") is not True or current.get("dirty") is not False
            or any(current.get(key) != snapshot[key] for key in ("domainKey", "revision"))):
        raise NativeSnapshotPending("物品读取期间来源发生变化，等待重新采集。")
    check()
    return snapshot
