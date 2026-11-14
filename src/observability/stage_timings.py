# 以单次冻结请求聚合分段耗时；只收数值摘要，不接触业务输入或改变异常传播。
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager, nullcontext
from functools import wraps
from math import isfinite
from threading import Lock
from time import perf_counter

from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.utils.logger import is_session_log_enabled


class StageTimings:
    """Controller 创建、显式传递，后台只聚合，当前 owner 才向日志提交。

    同阶段重复调用只保留次数、总耗时和最大耗时；不会逐装备或逐 RPC 打日志。
    父子阶段是包含关系，不能直接相加。关闭使晚到记录失效，不影响业务任务。
    """

    def __init__(self, context: OperationContext, *, clock=perf_counter, sink=log_event,
                 details_enabled=None, foreground=True, slow_ms=10_000):
        self.context = context
        self._clock, self._sink = clock, sink
        self._lock = Lock()
        self._started = clock()
        self._marks = {"requested": self._started}
        self._rows = OrderedDict()
        self._totals = OrderedDict()
        self._details_enabled = details_enabled if details_enabled is not None else is_session_log_enabled
        self._foreground = foreground
        self._slow_ms = slow_ms
        self._closed = False

    def mark(self, name):
        with self._lock:
            if not self._closed and (name in self._marks or len(self._marks) < 32):
                self._marks[name] = self._clock()

    def since(self, mark, phase):
        with self._lock:
            started = self._marks.pop(mark, None)
        if started is not None:
            self.record(phase, (self._clock() - started) * 1000)

    def record(self, phase, elapsed_ms, *, outcome="ok", **numbers):
        if not isfinite(elapsed_ms) or elapsed_ms < 0:
            return
        with self._lock:
            row = self._row(phase)
            if row is None:
                return
            row["sample_count"] += 1
            row["duration_ms"] = row.get("duration_ms", 0.0) + elapsed_ms
            row["max_duration_ms"] = max(row.get("max_duration_ms", 0.0), elapsed_ms)
            if outcome in {"error", "cancelled"}:
                key = outcome + "_count"
                row[key] = row.get(key, 0) + 1
            self._numbers(row, numbers)

    def note(self, phase, **numbers):
        with self._lock:
            row = self._row(phase)
            if row is not None:
                self._numbers(row, numbers)

    def _row(self, phase):
        # 阶段只能来自源码常量，不接角色名、规则名、路径或异常正文。
        if self._closed or not isinstance(phase, str) or len(phase) > 80:
            return None
        if not phase or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_." for c in phase):
            return None
        if phase not in self._rows:
            if len(self._rows) >= 96:
                return None
            self._rows[phase] = {"sample_count": 0}
        return self._rows[phase]

    @staticmethod
    def _numbers(row, numbers):
        for key, value in numbers.items():
            if len(row) >= 16 or not key.endswith(("_count", "_bytes", "_ms", "_enabled", "_hit")):
                continue
            if not key.replace("_", "").isascii() or not key.replace("_", "").isalnum():
                continue
            if type(value) not in (int, float, bool) or not isfinite(value) or value < 0:
                continue
            if key in {"duration_ms", "max_duration_ms", "sample_count", "error_count", "cancelled_count"}:
                continue
            # 计数累加表示处理次数，不宣称去重；布尔是该阶段实际采用的开关。
            if type(value) is bool:
                row[key] = value
            elif key.startswith("max_") or key == "peak_lag_ms":
                row[key] = max(row.get(key, 0), value)
            else:
                row[key] = row.get(key, 0) + value

    @contextmanager
    def measure(self, phase, **numbers):
        started = self._clock()
        outcome = "ok"
        try:
            yield
        except BaseException as error:
            outcome = "cancelled" if isinstance(error, CancelledError) else "error"
            raise
        finally:
            self.record(phase, (self._clock() - started) * 1000, outcome=outcome, **numbers)

    def call(self, phase, function, /, *args, **kwargs):
        with self.measure(phase):
            return function(*args, **kwargs)

    def _collect(self, *, final=False):
        with self._lock:
            if self._closed:
                return
            rows, self._rows = self._rows, OrderedDict()
            for phase, row in rows.items():
                if phase not in self._totals and len(self._totals) >= 96:
                    continue
                total = self._totals.setdefault(phase, {})
                for name, value in row.items():
                    if type(value) is bool:
                        total[name] = value
                    elif name.startswith('max_') or name == 'peak_lag_ms':
                        total[name] = max(total.get(name, 0), value)
                    else:
                        total[name] = total.get(name, 0) + value
            if final:
                self._closed = True
                self._marks.clear()
                totals, self._totals = self._totals, OrderedDict()
            else:
                totals = None
            return rows, totals

    def _details(self, boundary, rows):
        # 未开启详细日志时不格式化几十条最终会被过滤的 DEBUG 记录。
        if not self._details_enabled():
            return
        for phase, row in rows.items():
            fields = {k: round(v, 3) if type(v) is float else v for k, v in row.items()}
            self._sink("DEBUG", self.context.feature + ".timing_stage", "请求分段计时",
                       self.context, stage=phase, boundary=boundary, **fields)
        self._sink("DEBUG", self.context.feature + ".timing_boundary", "请求计时边界",
                   self.context, boundary=boundary,
                   elapsed_ms=round((self._clock() - self._started) * 1000, 3))

    def flush(self, boundary):
        """中间边界仅进入详细日志，仍保留有界聚合供最终摘要使用。"""
        try:
            collected = self._collect()
            if collected is not None:
                self._details(boundary, collected[0])
        except Exception:
            # 遥测是旁观者，sink 故障不重试业务或覆盖原始异常。
            pass

    def finish(self, boundary, *, outcome="returned"):
        """有效 owner 只提交一次终态；returned 仅表示任务返回，不冒称外部写入成功。"""
        try:
            collected = self._collect(final=True)
            if collected is None:
                return
            rows, totals = collected
            elapsed = round((self._clock() - self._started) * 1000, 3)
            ranked = sorted(totals.items(), key=lambda pair: pair[1].get('duration_ms', 0), reverse=True)
            maximum = max((row.get('duration_ms', 0) for row in totals.values()), default=0)
            slow = outcome != 'cancelled' and maximum >= self._slow_ms
            level = 'WARNING' if outcome == 'failed' or slow else 'INFO' if self._foreground else 'DEBUG'
            # 摘要独立于详情 sink，详情写入失败也不吞掉终态证据。
            if level != 'DEBUG' or self._details_enabled():
                self._sink(level, self.context.feature + '.timing_summary', '任务耗时摘要', self.context,
                    boundary=boundary, result=outcome, elapsed_ms=elapsed, stage_count=len(totals),
                    slow=slow, slowest_stages=[{'stage': phase, 'duration_ms': round(row['duration_ms'], 3)}
                        for phase, row in ranked[:3] if 'duration_ms' in row],
                    failed_stages=[phase for phase, row in totals.items() if row.get('error_count')][:3])
            self._details(boundary, rows)
        except Exception:
            pass

    def close(self):
        with self._lock:
            self._closed = True
            self._rows.clear()
            self._totals.clear()
            self._marks.clear()


def measure(timings, phase, **numbers):
    return nullcontext() if timings is None else timings.measure(phase, **numbers)


def timed_call(timings, phase, function, /, *args, **kwargs):
    with measure(timings, phase):
        return function(*args, **kwargs)


@contextmanager
def timed_context(timings, phase, context):
    """只测 __enter__ 的实际准备时间，不把持有读取租约的整段业务误报成读取。"""
    with ExitStack() as stack:
        with measure(timings, phase):
            value = stack.enter_context(context)
        yield value


def timed(phase):
    """只识别显式的 timings 关键字参数；未传入时保持原调用与返回对象。"""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with measure(kwargs.get("timings"), phase):
                return function(*args, **kwargs)
        return wrapped
    return decorate
