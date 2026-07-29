# 提交全量扫描库存与截图文件。
"""Commit captured full-scan screenshots after parsing succeeds."""

from __future__ import annotations

from typing import Any


class ScanInventoryCommitService:
    def __init__(self, processor: Any, scanner: Any) -> None:
        self.processor = processor
        self.scanner = scanner

    def commit(self) -> None:
        self.scanner._commit_temp_output()
