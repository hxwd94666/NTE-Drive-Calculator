# 按固定原生快照身份读取有界分页，不推测库存字段或提升来源完整性。
from __future__ import annotations

from copy import deepcopy
import json
from typing import Callable, Any

from src.integrations.nte_core_protocol import NteCoreProtocolError


_METADATA = (
    "providerId", "domain", "snapshotId", "generation", "domainKey",
    "observedUnixUs", "observedMonotonicMs", "enabled", "ready", "state",
    "recordCount", "sourceRecordCount", "enumerationComplete", "complete",
    "sourceCoverage", "changeCoverage", "missing", "failed", "truncated",
)
_MAX_BYTES = 64 * 1024 * 1024
_MAX_RECORDS = 32768


class NativeSnapshotPending(RuntimeError):
    """A domain cannot yet supply a complete observation; keep the saved inventory."""


def read_native_projection(call: Callable[..., dict[str, Any]], check: Callable[[], None], *, domain: str, header: dict[str, Any] | None = None) -> dict[str, Any]:
    """Consume versioned Core business DTOs; raw game rows never reach Calc storage."""
    if domain not in {"inventory", "character"}:
        raise ValueError("不支持的同步数据域。")
    field = "items" if domain == "inventory" else "profiles"
    check()
    if header is None:
        header = call("native.snapshot.refresh", {"domain": domain})
    check()
    if not isinstance(header, dict) or any(key not in header for key in _METADATA):
        raise NteCoreProtocolError("原生同步快照元数据不完整。")
    if header["domain"] != domain:
        raise NteCoreProtocolError("原生同步返回了其他数据域。")
    if (header["ready"] is not True or header["enabled"] is not True
            or header["enumerationComplete"] is not True or header["failed"] is not False
            or header["truncated"] is not False):
        raise NativeSnapshotPending("正在等待游戏提供完整的同步数据。")
    count = header["recordCount"]
    if type(count) is not int or not 0 <= count <= _MAX_RECORDS:
        raise NteCoreProtocolError("原生同步条目数量超出分页协议范围。")
    for key in ("providerId", "snapshotId", "generation", "domainKey", "observedUnixUs", "observedMonotonicMs"):
        if not isinstance(header[key], str) or not header[key]:
            raise NteCoreProtocolError("原生同步缺少有效的快照身份。")
    metadata = {key: deepcopy(header[key]) for key in _METADATA}
    if "revision" in header:
        revision = header["revision"]
        if (not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal()
                or len(revision) > 20 or (len(revision) > 1 and revision.startswith("0"))
                or int(revision) > 18446744073709551615 or header.get("dirty") is not False):
            raise NteCoreProtocolError("原生同步变化修订格式无效。")
        metadata.update(revision=revision, dirty=False)
    if domain == "inventory":
        for key in ("collectionComplete", "collectionScope", "characterRefsComplete"):
            if key not in header:
                raise NteCoreProtocolError("原生背包缺少本次集合或角色关联证明。")
            metadata[key] = deepcopy(header[key])
        if (header["collectionComplete"] is not True or header["collectionScope"] != "EQUIP"
                or header["characterRefsComplete"] is not True):
            raise NativeSnapshotPending("本次完整背包或角色装备关联尚未读取完成。")
    projection_missing = set()
    stat_provenance = None
    result_rows, characters, references = [], None, None
    offset = total_bytes = 0
    while True:
        check()
        page = call(f"native.{domain}.page", {"snapshotId": header["snapshotId"], "offset": offset, "limit": 64})
        check()
        if not isinstance(page, dict) or any(type(page.get(key)) is not type(value) or page.get(key) != value for key, value in metadata.items()):
            raise NteCoreProtocolError("原生同步分页的身份或完整性发生变化，已丢弃本次结果。")
        total_bytes += len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        if total_bytes > _MAX_BYTES:
            raise NteCoreProtocolError("原生同步分页响应超过大小限制。")
        diagnostics = page.get("projectionMissing", [])
        if not isinstance(diagnostics, list) or len(diagnostics) > 64 or any(not isinstance(item, str) or len(item) > 128 for item in diagnostics):
            raise NteCoreProtocolError("原生同步字段诊断格式无效。")
        projection_missing.update(diagnostics)
        if len(projection_missing) > 64:
            raise NteCoreProtocolError("原生同步字段诊断数量超限。")
        rows = page.get(field)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise NteCoreProtocolError("原生同步业务条目格式无效。")
        if "nextOffset" not in page:
            raise NteCoreProtocolError("原生同步缺少分页结束游标。")
        next_offset = page["nextOffset"]
        end = count if next_offset is None else next_offset
        if type(end) is not int or not offset <= end <= min(offset + 64, count):
            raise NteCoreProtocolError("原生同步分页游标无效。")
        if next_offset is not None and (end == offset or end >= count):
            raise NteCoreProtocolError("原生同步分页游标没有正确前进。")
        if len(rows) > end - offset or (domain == "inventory" and len(rows) != end - offset):
            raise NteCoreProtocolError("原生同步条目未覆盖本页声明的范围。")
        if domain == "inventory":
            provenance = page.get("statProvenance")
            if offset and provenance != stat_provenance:
                raise NteCoreProtocolError("原生背包分页的词条来源发生变化。")
            stat_provenance = deepcopy(provenance)
            if page.get("projectionComplete") is not True:
                raise NativeSnapshotPending("当前组件尚未确认本次完整背包的字段映射，保留已保存背包。")
            page_characters = page.get("characters")
            if not isinstance(page_characters, list):
                raise NteCoreProtocolError("原生背包缺少同次观测的角色实例。")
            if characters is not None and page_characters != characters:
                raise NteCoreProtocolError("原生背包分页的角色实例发生变化。")
            characters = deepcopy(page_characters)
            page_references = page.get("referencedItemUids")
            if not isinstance(page_references, list):
                raise NteCoreProtocolError("原生背包缺少同次角色装备引用证明。")
            if references is not None and references != page_references:
                raise NteCoreProtocolError("原生背包分页的角色装备引用发生变化。")
            references = deepcopy(page_references)
        result_rows.extend(deepcopy(rows))
        offset = end
        if next_offset is None:
            break
    check()
    if domain == "inventory":
        if not result_rows and not characters:
            raise NativeSnapshotPending("尚未观测到背包或角色，等待登录数据就绪；保留已保存背包。")
        item_uids = [_formal_uid(item.get("uid")) for item in result_rows]
        referenced_uids = [_formal_uid(uid) for uid in references]
        if len(item_uids) != len(set(item_uids)):
            raise NteCoreProtocolError("原生背包包含重复装备实例。")
        if referenced_uids != sorted(set(referenced_uids)) or not set(referenced_uids).issubset(item_uids):
            raise NteCoreProtocolError("角色装备引用未被本次完整背包覆盖。")
    else:
        character_ids = [profile.get("character_id") for profile in result_rows]
        if any(type(value) is not int or value <= 0 for value in character_ids):
            raise NteCoreProtocolError("原生角色状态缺少正式角色身份。")
        if len(character_ids) != len(set(character_ids)):
            raise NteCoreProtocolError("原生角色状态分页包含重复角色，已丢弃本次更新。")
    return {**metadata, field: result_rows, "projectionMissing": sorted(projection_missing),
            **({"characters": characters, "projectionComplete": True, "statProvenance": stat_provenance,
                                              "referencedItemUids": references} if domain == "inventory" else {})}


def _formal_uid(value):
    if not isinstance(value, dict):
        raise NteCoreProtocolError("原生装备实例身份格式无效。")
    parts = value.get("slot"), value.get("serial")
    if any(type(part) is not int or not 0 < part < 4294967295 for part in parts):
        raise NteCoreProtocolError("原生装备实例身份不在正式接口范围内。")
    return parts
