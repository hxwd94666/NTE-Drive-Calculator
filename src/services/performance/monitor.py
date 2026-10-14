# 管理手动监控与排错联动需求，后台读取既有原生连接并保存服务耗时。
from __future__ import annotations

from collections import deque
from pathlib import Path
from threading import Event, RLock, Thread
from time import monotonic

from src.integrations.performance.journals import PerformanceJournal, performance_counters


class PerformanceMonitor:
    def __init__(self, read_status, *, control=None, frames=None):
        self._read = read_status
        self._control, self._frames = control, frames
        self._applied = False
        self._sampling_key = None
        self._unsupported_revision = None
        self._overlay_status = "off"
        self._lock = RLock()
        self._wake, self._quit = Event(), Event()
        self._maintenance = False
        self._idle = Event()
        self._revision = 0
        self._key = None
        self._directory = None
        self._manual = self._raw = False
        self.linked = True
        self._allowed = False
        self._previous = {}
        self._source = None
        self._history = deque(maxlen=120)
        self._latest = {"state": "off", "rows": {}}
        self._journal = None
        self._journal_revision = None
        self._record_failed = None
        self._thread = Thread(target=self._run, name="performance-monitor", daemon=True)
        self._thread.start()

    def configure(self, *, key, log_dir: Path, raw: bool, allowed: bool):
        with self._lock:
            changed = key != self._key
            if changed:
                self._manual = False
                self._history.clear()
                self._previous = {}
                self._latest = {"state": "off", "rows": {}}
            if changed or raw != self._raw or allowed != self._allowed:
                self._revision += 1
                if allowed != self._allowed:
                    self._previous = {}
                    self._history.clear()
                    self._latest = {"state": "waiting" if allowed else "unavailable", "rows": {}}
            self._key, self._directory = key, log_dir
            self._raw, self._allowed = raw, allowed
        self._wake.set()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._manual = enabled
            self._overlay_status = "waiting" if enabled else "closing"
            self._revision += 1
            if enabled:
                self._latest = {"state": "waiting", "rows": {}}
            if not enabled:
                self._latest = {"state": "off", "rows": {}}
                self._history.clear()
                self._previous = {}
        self._wake.set()

    def set_linked(self, enabled: bool):
        with self._lock:
            self.linked = enabled
            self._revision += 1
        self._wake.set()

    def stop(self):
        with self._lock:
            self._manual = self._raw = False
            self._allowed = False
            self._revision += 1
            self._history.clear()
            self._previous = {}
            self._latest = {"state": "off", "rows": {}}
        self._wake.set()

    def set_maintenance(self, active):
        with self._lock:
            self._maintenance = active
            self._idle.clear()
            self._revision += 1
            self._previous = {}
            self._history.clear()
            self._latest = {'state': 'waiting', 'rows': {}}
        self._wake.set()

    def wait_maintenance_idle(self, timeout=5):
        return self._idle.wait(timeout)

    def _demand(self):
        linked = self._raw and self.linked
        return self._manual or linked, linked

    def snapshot(self):
        with self._lock:
            enabled, recording = self._demand()
            journal = self._journal
            result = {**self._latest, "enabled": enabled, "linked": self.linked, "overlay": self._manual, "overlay_state": self._overlay_status,
                      "automatic": enabled and not self._manual, "recording_requested": recording,
                      "history": list(self._history), "log_error": self._record_failed,
                      "log_path": str(journal.path) if journal else ""}
            if not enabled:
                result.update(state="off", rows={}, frames={})
            elif not self._allowed:
                result.update(state="unavailable", rows={}, frames={})
            elif result["state"] == "off" or monotonic() - result.get("sampled_at", 0) > 3:
                result.update(state="waiting", rows={}, frames={})
            return result

    def close(self):
        self.stop()
        self._quit.set()
        self._wake.set()
        self._thread.join(timeout=6)

    def _end_journal(self):
        journal, self._journal = self._journal, None
        if journal:
            try:
                journal.close()
            except OSError:
                self._record_failed = "性能日志未完整收尾"

    def _sample(self, revision):
        try:
            source, status = self._read()
            counters = performance_counters(status)
            state = "collecting" if counters else "unsupported"
        except PermissionError:
            source, counters, state = None, {}, "unavailable"
        except LookupError:
            source, counters, state = None, {}, "waiting"
        except Exception:
            source, counters, state = None, {}, "fault"
        now = monotonic()
        with self._lock:
            if revision != self._revision or self._quit.is_set():
                return None
            if source != self._source:
                self._previous = {}
                self._history.clear()
                self._source = source
            rows = {}
            for name, row in counters.items():
                old = self._previous.get(name)
                calls = row["calls"] - old["calls"] if old else 0
                total = row["total_us"] - old["total_us"] if old else 0
                valid = old is not None and calls > 0 and total >= 0
                rows[name] = {**row, "interval_calls": calls if valid else None,
                              "mean_ms": total / calls / 1000 if valid else None}
            self._previous = counters
            self._latest = {"state": state, "rows": rows, "sampled_at": now}
            self._history.append((now, {name: row["mean_ms"] for name, row in rows.items()}))
            return {"kind": "sample", "monotonic_seconds": now, "state": state, "rows": rows}

    def _run(self):
        try:
            while not self._quit.is_set():
                self._wake.wait(.5)
                self._wake.clear()
                with self._lock:
                    revision = self._revision
                    enabled, recording = self._demand()
                    allowed, directory, overlay, key = self._allowed, self._directory, self._manual, self._key
                    maintenance = self._maintenance
                if key != self._sampling_key:
                    self._disable_overlay()
                    self._sampling_key = key
                if self._journal_revision != revision:
                    self._end_journal()
                    self._journal_revision = revision
                    self._record_failed = None
                if maintenance or not enabled or not allowed:
                    self._disable_overlay()
                    if maintenance:
                        self._idle.set()
                    continue
                metrics = {}
                if self._control and self._unsupported_revision != revision:
                    try:
                        metrics = self._frames.sample() if self._frames else {}
                        payload = {"enabled": True, "overlay": overlay,
                                   **{key: metrics.get(key) for key in ("fps_milli", "frame_us", "low_milli")}}
                        # 帧源启动可能等待；派发沿本轮冻结 key，后台 owner 再复核权限与连接。
                        with self._lock:
                            if revision != self._revision or self._quit.is_set():
                                continue
                        self._applied = True  # 超时仍未知；收尾只清理此 key 曾启用的原连接。
                        result = self._control(payload, key)
                        metrics.update({"cost_us": result.get("cost_us"),
                                        "cost_coverage": result.get("cost_coverage", "unknown"),
                                        "cost_complete": result.get("cost_complete") is True})
                        self._overlay_status = ("rejected" if result["rejected"] else
                                                "visible" if overlay and result.get("rendered") else
                                                "waiting" if overlay else "off")
                    except NotImplementedError:
                        self._applied = False  # 能力门禁在派发前拒绝，此次未取得外部显示所有权。
                        self._unsupported_revision = revision
                        if self._frames:
                            self._frames.close()
                        self._overlay_status = "unsupported"
                    except Exception:
                        self._overlay_status = "unconfirmed"
                sample = self._sample(revision)
                if sample is not None:
                    sample["frames"] = metrics
                    with self._lock:
                        if revision == self._revision:
                            self._latest["frames"] = metrics
                # 撤销后的读取不写任何账号，包括原账号；保留代次复核。
                with self._lock:
                    current = revision == self._revision and not self._quit.is_set()
                if sample is None or not current or not recording or self._record_failed:
                    continue
                try:
                    if self._journal is None:
                        self._journal = PerformanceJournal(directory)
                    self._journal.write(sample)
                except OSError:
                    self._end_journal()
                    self._record_failed = "性能日志写入失败或已达 16 MiB 上限；实时监控继续"
        finally:
            self._disable_overlay()
            self._end_journal()

    def _disable_overlay(self):
        # 原生连接过期或忙碌不得延迟帧采样收尾。
        if self._frames:
            self._frames.close()
        if self._control and self._applied:
            try:
                self._control({"enabled": False, "overlay": False,
                               "fps_milli": None, "frame_us": None, "low_milli": None}, self._sampling_key)
                self._applied = False
                self._overlay_status = "off"
            except Exception:
                self._overlay_status = "unconfirmed"  # 未续期时组件租约也会失效，但不把请求结果未知当成功。
