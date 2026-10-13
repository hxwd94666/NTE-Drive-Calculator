# 描述基础权重业务一次冻结的账号身份与输入路径，不读取应用状态或文件。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BasicWeightDependencies:
    """页面任务的身份边界；本对象不随账号切换更新。"""

    account_id: str
    generation: int
    user_database_path: Path
    config_dir: Path
    static_database_path: Path
    shared_database_path: Path


@dataclass(frozen=True, slots=True)
class AccountWeightChange:
    """已校验的基础权重写入计划；属性顺序与主角权重来源身份均冻结。"""

    character_id: int
    source_dataset_id: str
    properties: tuple[tuple[str, float, float], ...]
    changed: bool
