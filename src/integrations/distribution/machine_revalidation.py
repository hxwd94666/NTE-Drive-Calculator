# 迁入只保留本机路径事实，显式重检确认前不继承外部副作用许可。
from pathlib import Path

from .data_roots import _read, write_record


class MigratedMachineGate:
    def __init__(self, root: Path):
        self.path = root / "maintenance" / "last-import.json"
        self._record = _read(self.path) if self.path.exists() else None

    @property
    def pending(self) -> bool:
        return self._record is not None and self._record.get("machine_paths_require_revalidation") is not False

    def confirm(self) -> None:
        if self.pending:
            record = {**self._record, "machine_paths_require_revalidation": False}
            write_record(self.path, record)
            self._record = record
