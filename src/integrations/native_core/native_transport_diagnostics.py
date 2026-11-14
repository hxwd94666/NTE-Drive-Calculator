# 仅记录原生通信结构、耗时与固定错误码，不保留响应正文。
import json
from time import monotonic

from src.observability import OperationContext, log_event
from src.integrations.native_core.native_snapshot_timing import SnapshotTimingLog
from src.integrations.native_core.native_hud_interaction import HudInteractionLog


def archive_failed(_event=None):
    from src.utils.logger import logger
    logger.warning("DLL 原始快照未能保存，已停止本次诊断归档；请检查账号日志目录可写性与磁盘空间。")


def archive_unavailable():
    from src.utils.logger import logger
    logger.warning("当前 Core 不支持 DLL 原始快照排错保存，本次不保存排错数据；正常采集与同步继续。如需排错文件，请更新随附组件。")


def invalid_json(line, error, *, executable_sha256, exit_code, core_pid=None):
    log_event("ERROR", "native_core.invalid_json", "采集 Core 响应无法解析，已停止本次连接",
              OperationContext.create("native_core"), line_chars=len(line),
              newline_complete=line.endswith("\n"), error_position=error.pos,
              error_line=error.lineno, error_column=error.colno,
              executable_sha256=executable_sha256, exit_code=exit_code, core_pid=core_pid)


def output_diagnostic(line, *, executable_sha256=None, core_pid=None):
    prefix = "nte_core_output "
    if not line.startswith(prefix) or len(line) > 1024:
        return
    try:
        value = json.loads(line[len(prefix):])
    except (ValueError, TypeError):
        return
    if (not isinstance(value, dict) or not isinstance(value.get("outcome"), str)
            or value["outcome"] not in {"slow", "timeout", "failed"}):
        return
    fields = {key: value[key] for key in ("elapsed_ms", "bytes_total", "bytes_written")
              if type(value.get(key)) is int and 0 <= value[key] <= 2**63 - 1}
    if len(fields) != 3:
        return
    log_event("WARNING", "native_core.output", "采集 Core 输出耗时或故障诊断",
              OperationContext.create("native_core"), outcome=value["outcome"],
              executable_sha256=executable_sha256, core_pid=core_pid, **fields)


class RuntimeCostLog:
    def __init__(self):
        self._last = float("-inf")
        self._previous = {}
        self._timing = SnapshotTimingLog()
        self._hud_interaction = HudInteractionLog()

    def observe(self, status):
        native = status.get("native_status") if isinstance(status, dict) else None
        costs = native.get("runtimePerformance") if isinstance(native, dict) else None
        if (not isinstance(costs, dict) or type(costs.get("version")) is not int
                or costs["version"] != 1):
            return
        self._timing.observe(costs.get("snapshot_diagnostics"))
        self._hud_interaction.observe(costs.get("hud_interaction"))
        if monotonic() - self._last < 30:
            return
        safe = {}
        for name in ("snapshot_pulse", "snapshot_read"):
            row = costs.get(name)
            if not isinstance(row, dict) or any(type(row.get(k)) is not int or not 0 <= row[k] < 2**63
                                               for k in ("calls", "total_us", "max_us")):
                return
            safe[name] = {k: row[k] for k in ("calls", "total_us", "max_us")}
        notifications = costs.get("inventory_notifications")
        if isinstance(notifications, list) and len(notifications) <= 256:
            fields = ("type", "calls", "items", "empty", "invalid", "last_monotonic_ms")
            safe["inventory_notifications"] = [
                {key: row[key] for key in fields}
                for row in notifications
                if isinstance(row, dict)
                and all(type(row.get(key)) is int and 0 <= row[key] < 2**63 for key in fields)
                and row["type"] <= 255
            ]
        if safe == self._previous:
            return
        self._last = monotonic()
        self._previous = safe
        log_event("DEBUG", "native_sync.runtime_cost", "DLL 游戏线程采集累计耗时（非帧率）",
                  OperationContext.create("native_sync"), counters=safe)


def _snapshot_refresh_fields(result):
    """仅提取有界计数与固定原因码，不保留原始元数据。"""
    if not isinstance(result, dict):
        return None
    fields = {key: result[key] for key in ("ready", "dirty", "enumerationComplete", "failed",
                                         "truncated", "collectionComplete", "characterRefsComplete")
              if type(result.get(key)) is bool}
    fields.update({key: result[key] for key in ("recordCount", "sourceRecordCount")
                   if type(result.get(key)) is int and 0 <= result[key] < 2**63})
    for source, target in (("diagnosticJob", "diagnostic_job"), ("diagnosticStep", "diagnostic_step")):
        if type(result.get(source)) is int and 0 <= result[source] < 2**63:
            fields[target] = result[source]
    known = {"object_domain_not_ready", "source_changed", "collection_not_finished",
             "collection_coverage_unverified", "change_coverage_unverified",
             "collection_spans_multiple_pulses", "equip_container_unavailable", "card_container_unavailable",
             "fork_container_unavailable", "invalid_inventory_item_or_uid", "inventory_item_class_unverified",
             "duplicate_inventory_map_uid", "equipped_item_uid_not_in_inventory", "total_scan_limit",
             "collection_record_limit", "collection_byte_limit", "character_ownership_unknown"}
    missing = result.get("missing")
    if isinstance(missing, list):
        fields["missing_count"] = len(missing)
        fields["missing_codes"] = sorted({v for v in missing[:64] if isinstance(v, str) and v in known})
    work = result.get("collectionWork")
    if isinstance(work, dict) and type(work.get("version")) is int and work["version"] == 1:
        fields["collection_work"] = {key: work[key] for key in
            ("steps", "scanned", "verified", "elapsed_us", "active_us", "max_step_us", "related_equipment_reads", "rows_read", "rows_reused", "rows_removed")
            if type(work.get(key)) is int and 0 <= work[key] < 2**63}
        if type(work.get("incremental")) is bool:
            fields["collection_work"]["incremental"] = work["incremental"]
    return fields


class SnapshotReadLog:
    """诊断状态归当前连接；只读 scope 串行访问，不参与快照门禁或重试决策。"""

    _DOMAINS = {"inventory", "character", "team", "environment"}
    _FAULTS = {"invalid_inventory_item_or_uid", "inventory_item_class_unverified",
               "duplicate_inventory_map_uid", "equipped_item_uid_not_in_inventory",
               "total_scan_limit", "collection_record_limit", "collection_byte_limit"}
    _WAITING = {"snapshot_pending", "cancelled", "NATIVE_SNAPSHOT_INCOMPLETE",
                "not_ready", "source_changed", "snapshot_not_found", "disabled", "control_timeout"}

    def __init__(self):
        # 键只取固定域、两种 scope 和两个阶段，不随修订、任务号或等待次数增长。
        self._issues = {}

    def _warn_once(self, key, signature, **fields):
        if self._issues.get(key) == signature:
            return
        self._issues[key] = signature
        domain, scope, stage = key
        log_event("WARNING", "native_sync.read_issue", "原生读取出现需排查的问题",
                  OperationContext.create("native_sync"), domain=domain, scope=scope,
                  stage=stage, **fields)

    def refresh(self, result, params, duration_ms):
        domain = params.get("domain")
        if domain not in self._DOMAINS:
            return
        fields = _snapshot_refresh_fields(result)
        if fields is None:
            return
        scope = "all_items" if params.get("scope") == "all_items" else "default"
        log_event("DEBUG", "native_sync.refresh_finished", "原生快照刷新结果与采集工作量",
                  OperationContext.create("native_sync"), domain=domain, scope=scope,
                  duration_ms=round(duration_ms, 2), **fields)
        faults = sorted(self._FAULTS.intersection(fields.get("missing_codes", ())))
        if fields.get("truncated") is True:
            faults.append("snapshot_truncated")
        key = domain, scope, "refresh"
        if faults:
            self._warn_once(key, tuple(faults), reason_codes=faults)
        elif (result.get("complete") is True and result.get("ready") is True
              and result.get("enumerationComplete") is True and result.get("failed") is False
              and result.get("truncated") is False):
            self._issues.pop(key, None)
        # failed=true 也可能只是未登录、容器未就绪；等待不告警，也不清除已有故障去重。

    def projection(self, domain, **fields):
        if domain not in self._DOMAINS:
            return
        log_event("DEBUG", "native_sync.projection_read", "原生同步读取分段耗时",
                  OperationContext.create("native_sync"), domain=domain, **fields)
        key = domain, "default", "projection"
        code = fields.get("failure_code")
        if code is not None and code not in self._WAITING:
            self._warn_once(key, (code, fields.get("failure_stage")), failure_code=code,
                            failure_stage=fields.get("failure_stage"))
        elif fields.get("completed") is True:
            self._issues.pop(key, None)
            self._issues.pop((domain, "default", "refresh"), None)
