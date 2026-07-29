# 观察被测应用进程，并仅管理由验证器显式启动的子进程。
"""Process observation with an opt-in managed child boundary."""

from __future__ import annotations

import csv
import io
import subprocess
from dataclasses import dataclass
from pathlib import Path


def list_processes(image_name: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0 or "没有运行" in completed.stdout:
        return []
    rows = []
    for row in csv.reader(io.StringIO(completed.stdout)):
        if len(row) >= 2:
            rows.append({"image_name": row[0], "pid": row[1]})
    return rows


@dataclass
class ManagedApplication:
    executable: Path
    arguments: tuple[str, ...] = ()
    process: subprocess.Popen[str] | None = None

    def start(self) -> int:
        if self.process is not None:
            raise RuntimeError("验证器已经启动了一个被测进程")
        self.process = subprocess.Popen(
            [str(self.executable), *self.arguments],
            cwd=str(self.executable.parent),
            text=True,
        )
        return int(self.process.pid)

    def stop(self, timeout: float = 10.0) -> int | None:
        if self.process is None:
            return None
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        return self.process.returncode

