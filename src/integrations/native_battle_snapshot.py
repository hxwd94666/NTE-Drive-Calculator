# 提前准备有界原生战报快照，首击按修订冻结，不提升未知来源覆盖。
from __future__ import annotations

from copy import deepcopy
import json
from time import monotonic

from src.integrations.native_inventory_snapshot import NativeSnapshotPending, read_native_projection
from src.integrations.nte_core_protocol import NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError


DOMAINS = ("character", "inventory", "team", "environment")
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 32768
FREEZE_TIMEOUT_SECONDS = 60.0
IDENTITY_FIELDS = ("providerId", "domain", "snapshotId", "generation", "domainKey", "revision",
                   "observedUnixUs", "observedMonotonicMs", "ready", "enabled", "dirty",
                   "recordCount", "enumerationComplete", "complete", "sourceCoverage", "changeCoverage",
                   "failed", "truncated", "missing")


class _SnapshotChanged(NativeSnapshotPending):
    """An observation changed after it was read; discard the whole cross-domain binding."""


def _retryable(error):
    return isinstance(error, NativeSnapshotPending) or (
        isinstance(error, NteCoreRpcError) and (error.message in {
            "not_ready", "source_changed", "snapshot_not_found", "disabled",
        } or error.domain_code in {"NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_MAPPING_UNSUPPORTED"}))


def read_native_raw_domain(call, check, domain, *, header=None):
    if header is None:
        header = call("native.snapshot.refresh", {"domain": domain})
    if not isinstance(header, dict) or any(k not in header for k in IDENTITY_FIELDS):
        raise NteCoreProtocolError("战报角色快照缺少来源身份或变化修订。")
    if header["domain"] != domain:
        raise NteCoreProtocolError("战报快照返回了错误的数据域。")
    if (header["ready"] is not True or header["enabled"] is not True or header["dirty"] is not False
            or header["failed"] is not False or header["truncated"] is not False
            or header["enumerationComplete"] is not True):
        raise NativeSnapshotPending("战报入场快照尚未完成。")
    count = header["recordCount"]
    if type(count) is not int or not 0 <= count <= MAX_RECORDS:
        raise NteCoreProtocolError("战报快照条目数量超限。")
    records, offset, size = [], 0, 0
    while True:
        check()
        page = call("native.snapshot.page", {"domain": domain, "snapshotId": header["snapshotId"],
                                             "offset": offset, "limit": 64})
        if not isinstance(page, dict) or any(type(page.get(k)) is not type(header[k]) or page.get(k) != header[k]
                                            for k in IDENTITY_FIELDS):
            raise NteCoreProtocolError("战报原生快照分页身份发生变化。")
        rows, next_offset = page.get("records"), page.get("nextOffset")
        end = count if next_offset is None else next_offset
        if ("nextOffset" not in page or not isinstance(rows, list)
                or type(end) is not int or not offset <= end <= min(offset + 64, count)
                or any(not isinstance(row, dict) for row in rows) or len(rows) != end - offset
                or (next_offset is not None and (end == offset or end >= count))):
            raise NteCoreProtocolError("战报原生快照分页范围不完整。")
        size += len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
        if size > MAX_BUNDLE_BYTES:
            raise NteCoreProtocolError("战报原生快照超过大小限制。")
        records.extend(deepcopy(rows))
        offset = end
        if next_offset is None:
            break
    return {**deepcopy(header), "records": records}


def validate_native_snapshot_current(bundle, status):
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("战报快照复核状态无效。")
    observations = list(bundle["domains"].values())
    observations.extend(bundle[key] for key in ("inventory_projection", "character_projection") if key in bundle)
    for snapshot in observations:
        # The equipped-item observation is already frozen and internally bound
        # to its raw page. Later backpack refreshes must not discard that panel.
        if snapshot["domain"] == "inventory":
            continue
        matches = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == snapshot["domain"]]
        if len(matches) != 1:
            raise NteCoreProtocolError("战报快照复核缺少唯一数据域。")
        current = matches[0]
        if (status.get("providerId") != snapshot["providerId"] or current.get("dirty") is not False
                or current.get("ready") is not True or current.get("enabled") is not True
                or any(current.get(key) != snapshot[key] for key in ("domainKey", "revision"))):
            raise _SnapshotChanged("战报准备期间场景、队伍或配置发生变化。")


def freeze_native_battle_snapshot(client, check, *, baseline=None):
    capabilities = (client.hello_result or {}).get("capabilities", ())
    base = {"schema_version": 1, "binding": "first_hit_revision", "domains": {}}
    if "snapshot.changes.v1" not in capabilities:
        return {**base, "state": "unavailable", "missing": ["snapshot_changes_capability_missing"]}
    deadline = monotonic() + FREEZE_TIMEOUT_SECONDS
    prior_character = baseline.previous_observation("character") if baseline is not None else None

    def call(method, params):
        check()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NteCoreTimeoutError("native.first_hit_snapshot", FREEZE_TIMEOUT_SECONDS)
        result = client.call(method, params, timeout=remaining, check_cancelled=check)
        check()
        return result

    # At most one restart: a transition cannot keep the start operation alive indefinitely.
    for attempt in range(2):
        bundle = {**base, "domains": {}, "missing": [], "state": "observed"}
        try:
            status = call("native.snapshot.status", {}) if baseline is not None else None
            for domain in DOMAINS:
                if f"{domain}.snapshot.v1" not in capabilities:
                    bundle["missing"].append(f"{domain}_snapshot_unavailable")
                    continue
                try:
                    cached = baseline.get(domain, status) if baseline is not None else None
                    if cached is not None:
                        bundle["domains"][domain] = cached["snapshot"]
                        if cached["projection"] is not None:
                            bundle[f"{domain}_projection"] = cached["projection"]
                    else:
                        from src.integrations.native_inline_snapshot import INLINE_RAW_CAPABILITY, InlineSnapshotPages
                        projection_cap = {"inventory": "native_inventory_dto_v1",
                                          "character": "native_character_profile_v1"}.get(domain)
                        if INLINE_RAW_CAPABILITY in capabilities and projection_cap in capabilities:
                            inline = InlineSnapshotPages(call, domain)
                            try:
                                projection = read_native_projection(inline.call, check, domain=domain)
                            except NteCoreRpcError as error:
                                if error.domain_code != "NATIVE_MAPPING_UNSUPPORTED":
                                    raise
                                # Preserve diagnostic evidence even when formal mapping fails.
                                bundle["domains"][domain] = read_native_raw_domain(call, check, domain)
                                bundle["missing"].append(f"{domain}_projection_unavailable")
                            else:
                                bundle["domains"][domain] = inline.read(projection, check)
                                bundle[f"{domain}_projection"] = projection
                        else:
                            bundle["domains"][domain] = read_native_raw_domain(call, check, domain)
                except (NativeSnapshotPending, NteCoreRpcError) as error:
                    if isinstance(error, NteCoreRpcError) and error.message == "source_changed":
                        raise _SnapshotChanged("读取期间数据域已变化。") from error
                    if not _retryable(error):
                        raise
                    bundle["missing"].append(f"{domain}_snapshot_unavailable")
                    # A failed raw refresh invalidates this domain's earlier formal projection too.
                    bundle.pop(f"{domain}_projection", None)
            for domain, cap in (("inventory", "native_inventory_dto_v1"), ("character", "native_character_profile_v1")):
                if f"{domain}_projection" in bundle:
                    continue
                if f"{domain}_projection_unavailable" in bundle["missing"]:
                    continue
                header = bundle["domains"].get(domain)
                if cap not in capabilities or header is None:
                    bundle["missing"].append(f"{domain}_projection_unavailable")
                    continue
                try:
                    # Formal and raw pages must belong to the exact same frozen ID.
                    bundle[f"{domain}_projection"] = read_native_projection(call, check, domain=domain, header=header)
                except (NativeSnapshotPending, NteCoreRpcError) as error:
                    if not _retryable(error):
                        raise
                    bundle["missing"].append(f"{domain}_projection_unavailable")
            validate_native_snapshot_current(bundle, call("native.snapshot.status", {}))
            check()
            current_character = bundle["domains"].get("character") or {}
            if prior_character and prior_character.get("revision") != current_character.get("revision"):
                bundle["prior_character_observation"] = prior_character
            if len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")) > MAX_BUNDLE_BYTES:
                raise NteCoreProtocolError("战报入场快照总大小超限。")
            if baseline is not None:
                for domain, snapshot in bundle["domains"].items():
                    projection = bundle.get(f"{domain}_projection")
                    if domain in ("team", "environment") or projection is not None:
                        baseline.put(domain, snapshot, projection)
            return bundle
        except (NativeSnapshotPending, NteCoreRpcError) as error:
            if not _retryable(error):
                raise
            if attempt:
                return {**base, "state": "source_changed", "missing": ["capture_start_snapshot_not_stable"]}
    raise AssertionError("unreachable")
