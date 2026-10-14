# 从独立 PresentMon 子进程读取游戏呈现间隔，统计有界窗口并独立收尾。
from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import deque
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from src.integrations.performance.presentmon_lifetime import (
    CollectorJob, reap_abandoned_sessions, session_name, stop_session,
)


class FrameWindow:
    """呈现间隔按采集时钟划窗，是否过期按本机接收时钟判定。"""
    def __init__(self):
        self.frames = deque(maxlen=60000)
        self.last_arrival = 0
        self.last_trace = -1
        self.overflow_until = -1

    def add(self, row, now):
        try:
            # 上游旧版 CSV 使用小写 m；统一的 v1 兼容输出使用大写 M，单位相同。
            milliseconds = float(row.get("msBetweenPresents", row.get("MsBetweenPresents")))
            timestamp = float(row["TimeInSeconds"])
            chain = row["SwapChainAddress"]
        except (KeyError, ValueError, TypeError):
            return
        if (not math.isfinite(milliseconds) or milliseconds <= 0
                or not math.isfinite(timestamp) or timestamp < 0 or not chain):
            return
        if self.frames and now - self.last_arrival > 3:
            self.frames.clear()
        self.last_arrival = now
        self.last_trace = max(timestamp, self.last_trace)
        if len(self.frames) == self.frames.maxlen:
            self.overflow_until = max(self.overflow_until, self.frames[0][0] + 30)
        self.frames.append((timestamp, milliseconds, chain))
        while self.frames and self.frames[0][0] < self.last_trace - 30:
            self.frames.popleft()

    def snapshot(self, now):
        empty = {"fps_milli": None, "frame_us": None, "low_milli": None}
        if not self.frames or now - self.last_arrival > 3:
            return empty
        counts = {}
        for time, _, chain in self.frames:
            if time >= self.last_trace - 1:
                counts[chain] = counts.get(chain, 0) + 1
        if not counts:
            return empty
        # 选取当前最活跃的呈现流，禁止混合视频、UI 与游戏的不同交换链。
        chain = max(counts, key=counts.get)
        recent = [ms for time, ms, key in self.frames if key == chain and time >= self.last_trace - 1]
        window = [ms for time, ms, key in self.frames if key == chain and time >= self.last_trace - 30]
        mean = sum(recent) / len(recent)
        tail = sorted(window, reverse=True)[:math.ceil(len(window) * .01)]
        def wire(value):
            return round(value) if math.isfinite(value) and 1 <= value <= 1_000_000_000 else None
        return {"fps_milli": wire(1_000_000 / mean), "frame_us": wire(mean * 1000),
                "low_milli": wire(1_000_000 * len(tail) / sum(tail))
                if len(window) >= 1000 and self.last_trace > self.overflow_until else None}


class PresentMon:
    def __init__(self, executable: Path, pid_reader):
        self.executable, self.pid_reader = executable, pid_reader
        self._process = None
        self._thread = None
        self._stderr_thread = None
        self._stream_error = ""
        self._lock = Lock()
        self._window = FrameWindow()
        self._pid = None
        self._retry_at = 0
        self.error = ""
        self._session = ""
        self._verified = False
        self._job = None
        self._pid_probe_at = 0
        self._started_at = 0
        self._reaped = False

    def sample(self):
        now = monotonic()
        pid = self._pid
        if now >= self._pid_probe_at:
            self._pid_probe_at = now + 1
            try:
                pid = self.pid_reader()
            except Exception:
                self.close()
                return {'fps_milli': None, 'frame_us': None, 'low_milli': None,
                        'frame_error': '无法核对当前游戏进程；帧采样已停止',
                        'frame_source': 'presentmon.present_interval', 'low_window_seconds': 30}
        if pid != self._pid:
            self.close()
            self._pid = pid
            self._retry_at = 0
        if not pid:
            self.error = "等待游戏"
        elif not self.executable.is_file():
            self.error = "缺少配套 PresentMon；帧率暂不可用"
        elif self._process is None or self._process.poll() is not None:
            if monotonic() >= self._retry_at:
                self.close()
                self._pid = pid
                self._retry_at = monotonic() + 10
                self._stream_error = ""
                try:
                    if not self._verified:
                        manifest = json.loads(self.executable.with_name("component.json").read_text(encoding="utf-8"))
                        if (manifest.get("component") != "presentmon-console" or manifest.get("version") != "2.5.1"
                                or hashlib.sha256(self.executable.read_bytes()).hexdigest() != manifest.get("sha256")):
                            raise ValueError("invalid_presentmon_component")
                        self._verified = True
                    if not self._reaped:
                        reap_abandoned_sessions()
                        self._reaped = True
                    self._session = session_name()
                    self._job = CollectorJob()
                    self._process = subprocess.Popen([
                        str(self.executable), "--process_id", str(pid), "--output_stdout",
                        "--v1_metrics", "--no_console_stats", "--no_track_input", "--no_track_gpu",
                        "--terminate_on_proc_exit", "--session_name", self._session,
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                       text=True, encoding="utf-8-sig", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self._job.attach(self._process)
                    self._started_at = monotonic()
                    self._thread = Thread(target=self._consume, args=(self._process, pid), daemon=True)
                    self._stderr_thread = Thread(target=self._consume_errors, args=(self._process,), daemon=True)
                    self.error = ""
                    self._thread.start()
                    self._stderr_thread.start()
                except (OSError, ValueError):
                    self.close()
                    self._pid = pid
                    self.error = "PresentMon 身份核对或启动失败"
            else:
                self.error = "帧采集已退出；等待重试（检查采集权限）"
        with self._lock:
            values = self._window.snapshot(monotonic())
            error = self._stream_error or self.error
            if values["fps_milli"] is None and not error:
                error = ('帧采样进程存活但持续无有效数据；请检查 ETW 采样状态'
                         if self._started_at and now - self._started_at >= 10 and not self._window.frames
                         else '等待游戏呈现帧；尚无有效样本或样本已过期')
            return {**values, "frame_error": error,
                    "frame_source": "presentmon.present_interval", "low_window_seconds": 30}

    def _consume(self, process, pid):
        try:
            reader = csv.DictReader(process.stdout)
            columns = set(reader.fieldnames or ())
            if not columns:
                return  # 空输出由启动错误或退出状态解释，不误报 CSV 字段不匹配。
            if (not {"ProcessID", "TimeInSeconds", "SwapChainAddress"} <= columns
                    or not {"msBetweenPresents", "MsBetweenPresents"} & columns):
                with self._lock:
                    if process is self._process:
                        self._stream_error = "PresentMon 输出缺少所需帧字段；请核对配套组件"
                return
            for row in reader:
                if row.get("ProcessID") != str(pid):
                    continue
                with self._lock:
                    if process is self._process:
                        self._window.add(row, monotonic())
        except (OSError, ValueError, csv.Error):
            with self._lock:
                if process is self._process:
                    self._stream_error = "PresentMon 帧数据读取失败"

    def _consume_errors(self, process):
        try:
            for line in process.stderr:
                lower = line.lower()
                message = ""
                if "access denied" in lower:
                    message = "帧采集权限不足；请以管理员身份重新打开 Calc 后再开启性能显示"
                elif lower.startswith("error:"):
                    message = "PresentMon 启动或采集失败；请核对权限与配套组件"
                elif 'etw' in lower and 'lost' in lower:
                    message = 'ETW 帧事件丢失；当前帧统计可能不完整'
                if message:
                    with self._lock:
                        if process is self._process:
                            self._stream_error = message
        except (OSError, ValueError):
            pass

    def close(self):
        process, self._process = self._process, None
        session, self._session = self._session, ''
        job, self._job = self._job, None
        self._started_at = 0
        # 子进程已退出也要结束本次 ETW 会话；不启动第二个采集进程代为停止。
        if session:
            try:
                stop_session(session)
            except OSError:
                self.error = '帧采样会话停止未确认；将在下次启动时核对失主会话'
        if process is not None:
            try:
                if process.poll() is None:
                    process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                except OSError:
                    pass
            finally:
                if job:
                    job.close()
                    job = None
            try:
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                self.error = '帧采样进程退出未确认'
            # 首次停止可能与子进程创建会话交错；确认退出后再收尾，避免迟到的新会话。
            if session and process.poll() is not None:
                try:
                    stop_session(session)
                except OSError:
                    self.error = '帧采样会话停止未确认；将在下次启动时核对失主会话'
            if self._thread:
                self._thread.join(timeout=1)
            if self._stderr_thread:
                self._stderr_thread.join(timeout=1)
            if process.stdout and not (self._thread and self._thread.is_alive()):
                process.stdout.close()
            if process.stderr and not (self._stderr_thread and self._stderr_thread.is_alive()):
                process.stderr.close()
        if job:
            job.close()
        with self._lock:
            self._window = FrameWindow()
        self._thread = None
        self._stderr_thread = None
        self._pid = None
