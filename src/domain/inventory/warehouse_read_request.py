# 冻结仓库读取的账号代次、数据库和素材来源，后台不再读取当前窗口状态。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WarehouseReadRequest:
    account_id: str
    generation: int
    database_path: Path
    static_database_path: Path
    asset_root: Path
