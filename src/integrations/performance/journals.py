# 校验性能计数并在账号日志旁保存有界诊断记录，不保留业务正文。
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from src.integrations.native_snapshot_timing import STAGES
from src.observability import OperationContext, log_event


def _write_row(stream, row: dict, count: int, limit: int, budget_error: str) -> int:
    """仅在完整写入并刷新后推进字节计数；两种日志保持各自容量和错误语义。"""
    text = json.dumps({**row, "utc": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n"
    size = len(text.encode("utf-8"))
    if count + size > limit:
        raise OSError(budget_error)
    stream.write(text)
    stream.flush()
    return count + size


def performance_counters(status: dict) -> dict[str, dict[str, int]]:
    native = status.get("native_status")
    costs = native.get("runtimePerformance") if isinstance(native, dict) else None
    if not isinstance(costs, dict) or type(costs.get("version")) is not int or costs["version"] != 1:
        return {}
    candidates = {name: costs.get(name) for name in ("snapshot_pulse", "snapshot_read")}
    for field, prefix in (("snapshot_diagnostics", "snapshot."),
                          ("user_snapshot_diagnostics", "user.snapshot.")):
        detail = costs.get(field)
        if isinstance(detail, dict) and type(detail.get("version")) is int and detail["version"] == 1:
            stages = detail.get("stages")
            if isinstance(stages, dict):
                candidates.update({prefix + name: stages.get(name) for name in STAGES})
    result = {}
    for name, row in candidates.items():
        keys = ("calls", "total_us", "max_us")
        if not isinstance(row, dict) or not all(type(row.get(k)) is int and 0 <= row[k] < 2**63 for k in keys):
            continue
        if row["max_us"] > row["total_us"] or (row["calls"] == 0 and row["total_us"] != 0):
            continue
        result[name] = {k: row[k] for k in keys}
    return result


class PerformanceJournal:
    """后台任务独占的有界摘要日志；不触碰原始采集文件。"""

    def __init__(self, log_dir: Path, *, byte_limit: int = 16 * 1024 * 1024):
        self.session = uuid4().hex
        directory = log_dir / "performance"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = directory / f"performance_{stamp}_{self.session[:8]}.jsonl"
        self._stream = self.path.open("x", encoding="utf-8")
        self._bytes, self._limit = 0, byte_limit
        self.context = OperationContext.create("performance")
        try:
            self.write({"kind": "session_start", "schema": "calc.performance/2", "session": self.session,
                        "source": "native.runtimePerformance/1", "metric_contract": "calc.performance.metrics/1",
                        "frame_source": "presentmon.present_interval", "cost_complete": False,
                        "raw_capture_directory": str(log_dir / "nte_core" / "raw_capture")})
        except Exception:
            self._stream.close()
            raise
        log_event("INFO", "performance.started", "性能排错记录已开始", self.context,
                  session=self.session, path=str(self.path))

    def write(self, row: dict):
        self._bytes = _write_row(self._stream, row, self._bytes, self._limit,
                                 "performance_budget_reached")

    def close(self):
        try:
            self.write({"kind": "session_end", "session": self.session})
        finally:
            self._stream.close()
            log_event("INFO", "performance.stopped", "性能排错记录已结束", self.context,
                      session=self.session, path=str(self.path))


SERVICES = ('hud_frame', 'hud_event', 'hud_value_event', 'snapshot_pulse',
            'snapshot_read', 'combat_clock', 'combat_observer_pulse')
PHASES = ('original', 'gate', 'inbox', 'discovery', 'setup', 'boss', 'team',
          'nameplate', 'infoLog', 'draw', 'body', 'outside', 'gap', 'teamCooldowns',
          'teamTextures', 'teamMirror', 'teamCanvas')


def validate_trace(value, trace_id):
    if (not isinstance(value, dict) or value.get('trace_id') != trace_id
            or any(type(value.get(k)) is not bool for k in ('active', 'stopping', 'failed', 'complete'))
            or any(type(value.get(k)) is not int or not 0 <= value[k] < 2**63 for k in ('written', 'dropped'))
            or not isinstance(value.get('path'), str) or len(value['path']) > 32768
            or not isinstance(value.get('stop_reason'), str)):
        raise ValueError('细分性能回包身份或状态无效')
    summaries = value.get('summaries')
    if not isinstance(summaries, list) or len(summaries) > len(SERVICES):
        raise ValueError('细分性能摘要格式无效')
    seen = set()
    for summary in summaries:
        if not isinstance(summary, dict) or summary.get('source') not in SERVICES or summary['source'] in seen:
            raise ValueError('细分性能来源无效')
        seen.add(summary['source'])
        phases = summary.get('phases_us')
        if not isinstance(phases, dict) or set(phases) != set(PHASES):
            raise ValueError('细分性能阶段不匹配')
        for row in phases.values():
            if not isinstance(row, dict) or type(row.get('n')) is not int or not 0 <= row['n'] <= 600:
                raise ValueError('细分性能样本数无效')
            for key in ('p95', 'p99', 'max'):
                metric = row.get(key)
                if (row['n'] == 0 and metric is not None) or (row['n'] > 0 and
                        (type(metric) is not int or not 0 <= metric < 2**63)):
                    raise ValueError('细分性能缺失值或耗时无效')
    return value


class TraceJournal:
    def __init__(self, directory, trace_id, services):
        directory = directory / 'performance'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f'detail_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}.jsonl'
        self.stream = self.path.open('x', encoding='utf-8')
        self.bytes = 0
        self.previous = None
        try:
            self.write({'kind': 'session_start', 'schema': 'calc.performance.trace/1',
                        'trace_id': trace_id, 'services': services, 'source': 'nte.performance/2'})
        except Exception:
            # 构造失败时调用方尚未取得对象，流的所有权仍在这里。
            self.stream.close()
            raise

    def write(self, row):
        self.bytes = _write_row(self.stream, row, self.bytes, 64 * 1024 * 1024,
                                '细分性能日志达到容量上限')

    def sample(self, value):
        # 仅持久化发生变化的摘要窗口及已确认终态，不把重复轮询累积成日志。
        identity = (value['summaries'], value['active'], value['failed'], value['complete'])
        if identity != self.previous:
            self.write({'kind': 'status', **value})
            self.previous = identity

    def close(self):
        self.stream.close()
