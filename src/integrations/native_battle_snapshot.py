# 提前准备有界原生战报快照，首击按修订冻结，不提升未知来源覆盖。
from __future__ import annotations

import json
from time import monotonic

from src.integrations.native_raw_snapshot import MAX_BUNDLE_BYTES, read_native_raw_domain
from src.integrations.native_inventory_snapshot import NativeSnapshotPending, read_native_projection
from src.integrations.nte_core_protocol import NteCoreProtocolError, NteCoreRpcError, NteCoreTimeoutError


DOMAINS = ("character", "team", "environment")
FREEZE_TIMEOUT_SECONDS = 60.0


def read_first_hit_snapshot(client, check, references):
    """Read the DLL-owned immutable observations, including after a half transition."""
    bundle = {"schema_version": 1, "binding": "first_hit_revision", "state": "observed",
              "domains": {}, "missing": [], "retention": "dll_pinned"}
    deadline = monotonic() + FREEZE_TIMEOUT_SECONDS

    def call(method, params):
        check()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise NteCoreTimeoutError("native.first_hit_snapshot", FREEZE_TIMEOUT_SECONDS)
        return client.call(method, params, timeout=remaining, check_cancelled=check)

    for domain in DOMAINS:
        reference = references.get(domain)
        if not isinstance(reference, dict) or not reference.get("snapshotId"):
            bundle["missing"].append(f"{domain}_first_hit_unavailable")
            continue
        try:
            header = call("native.snapshot.open", {"domain": domain, "snapshotId": reference["snapshotId"]})
            if any(header.get(key) != reference.get(key) for key in (
                "providerId", "domain", "snapshotId", "generation", "domainKey", "revision",
                "observedUnixUs", "observedMonotonicMs",
            )):
                raise NteCoreProtocolError("首击固定快照身份不匹配。")
            bundle["domains"][domain] = read_native_raw_domain(call, check, domain, header=header)
            if domain == "character":
                bundle[f"{domain}_projection"] = read_native_projection(call, check, domain=domain, header=header)
        except (NativeSnapshotPending, NteCoreRpcError) as error:
            if not _retryable(error):
                raise
            bundle["missing"].append(f"{domain}_first_hit_unavailable")
    if len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")) > MAX_BUNDLE_BYTES:
        raise NteCoreProtocolError("首击快照超过大小限制。")
    check()
    return bundle


class _SnapshotChanged(NativeSnapshotPending):
    """An observation changed after it was read; discard the whole cross-domain binding."""


def _retryable(error):
    return isinstance(error, NativeSnapshotPending) or (
        isinstance(error, NteCoreRpcError) and (error.message in {
            "not_ready", "source_changed", "snapshot_not_found", "disabled",
        } or error.domain_code in {"NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_MAPPING_UNSUPPORTED"}))


def validate_native_snapshot_current(bundle, status, *, require_current=False):
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("战报快照复核状态无效。")
    observations = list(bundle["domains"].values())
    observations.extend(bundle[key] for key in ("character_projection",) if key in bundle)
    for snapshot in observations:
        # 已读完整页是不可变观测，不代表实时缓存仍指向它。
        # 历史使用由首击与半场证据核对；末击后切人或退出不应抹去已读观测。
        matches = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == snapshot["domain"]]
        if len(matches) != 1:
            raise NteCoreProtocolError("战报快照复核缺少唯一数据域。")
        if status.get("providerId") != snapshot["providerId"]:
            raise _SnapshotChanged("战报准备期间采集提供方发生变化。")
        if require_current:
            current = matches[0]
            if (current.get("dirty") is not False or current.get("ready") is not True
                    or current.get("enabled") is not True
                    or any(current.get(key) != snapshot.get(key) for key in ("domainKey", "revision"))):
                # 待发生首击只能固定 DLL 当前观测；后读数据域发现切场时，先读域也可能已失效。
                raise _SnapshotChanged("首击预备期间数据域已失效，重新读取当前配置。")


def freeze_native_battle_snapshot(client, check, *, baseline=None, require_current=False):
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

    # 切场最多重读一次，避免启动操作因连续变化而无限存活。
    for attempt in range(2):
        bundle = {**base, "domains": {}, "missing": [], "state": "observed"}
        try:
            status = call("native.snapshot.status", {}) if baseline is not None else None
            # 当前预备先读较小上下文域，再进行跨回调的角色读取；上方首击固定读取保持原顺序。
            read_order = ("team", "environment", "character") if require_current else DOMAINS
            for domain in read_order:
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
                        projection_cap = {"character": "native_character_profile_v1"}.get(domain)
                        if INLINE_RAW_CAPABILITY in capabilities and projection_cap in capabilities:
                            inline = InlineSnapshotPages(call, domain)
                            try:
                                projection = read_native_projection(inline.call, check, domain=domain)
                            except NteCoreRpcError as error:
                                if error.domain_code != "NATIVE_MAPPING_UNSUPPORTED":
                                    raise
                                # 正式字段映射失败仍保留原始观测证据，不将其提升为正式投影。
                                bundle["domains"][domain] = read_native_raw_domain(call, check, domain)
                                bundle["missing"].append(f"{domain}_projection_unavailable")
                            else:
                                bundle["domains"][domain] = inline.read(projection, check)
                                bundle[f"{domain}_projection"] = projection
                        else:
                            bundle["domains"][domain] = read_native_raw_domain(call, check, domain)
                except (NativeSnapshotPending, NteCoreRpcError) as error:
                    if domain != "environment" and isinstance(error, NteCoreRpcError) and error.message == "source_changed":
                        raise _SnapshotChanged("读取期间数据域已变化。") from error
                    if not _retryable(error):
                        raise
                    bundle["missing"].append(f"{domain}_snapshot_unavailable")
                    # 原始刷新失败时，同域此前的正式投影也须失效，避免混用不同观测。
                    bundle.pop(f"{domain}_projection", None)
            for domain, cap in (("character", "native_character_profile_v1"),):
                if f"{domain}_projection" in bundle:
                    continue
                if f"{domain}_projection_unavailable" in bundle["missing"]:
                    continue
                header = bundle["domains"].get(domain)
                if cap not in capabilities or header is None:
                    bundle["missing"].append(f"{domain}_projection_unavailable")
                    continue
                try:
                    # 正式投影页与原始页必须归属完全相同的冻结快照 ID。
                    bundle[f"{domain}_projection"] = read_native_projection(call, check, domain=domain, header=header)
                except (NativeSnapshotPending, NteCoreRpcError) as error:
                    if not _retryable(error):
                        raise
                    bundle["missing"].append(f"{domain}_projection_unavailable")
            if baseline is not None:
                for domain, snapshot in bundle["domains"].items():
                    projection = bundle.get(f"{domain}_projection")
                    if domain in ("team", "environment") or projection is not None:
                        baseline.put(domain, snapshot, projection)
            validate_native_snapshot_current(bundle, call("native.snapshot.status", {}),
                                             require_current=require_current)
            check()
            current_character = bundle["domains"].get("character") or {}
            if prior_character and prior_character.get("revision") != current_character.get("revision"):
                bundle["prior_character_observation"] = prior_character
            if len(json.dumps(bundle, ensure_ascii=False).encode("utf-8")) > MAX_BUNDLE_BYTES:
                raise NteCoreProtocolError("战报入场快照总大小超限。")
            return bundle
        except (NativeSnapshotPending, NteCoreRpcError) as error:
            if not _retryable(error):
                raise
            if attempt:
                return {**base, "state": "source_changed", "missing": ["capture_start_snapshot_not_stable"]}
    raise AssertionError("unreachable")
