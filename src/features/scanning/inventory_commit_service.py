# 提交全量扫描库存与截图文件。
"""Persist full-scan inventory results and commit captured screenshots."""

from __future__ import annotations


class InventoryCommitService:
    def __init__(self, processor, scanner):
        self.processor = processor
        self.scanner = scanner

    def commit(self) -> None:
        self.scanner._commit_temp_output()
