# 为仍在使用的计算服务提供只读配置访问。
"""旧计算入口的只读词条规则适配，仅暴露 load_stats。
不读取旧角色缓存或顺序文件，不保存权重、角色档案或账号状态。
缺省路径为发行配置；显式路径用于调用方指定的规则输入。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.integrations.bundled_resources import bundled_config_dir
from src.storage.json_store import read_json


def load_stats(config_dir: str | Path | None = None) -> dict[str, Any]:
    """读取既有 JSON Store；缺项或非字典保留此兼容接口的空字典回退。"""
    # 兼容旧角色计算入口；缺省值只允许指向随程序发布的只读配置。
    resolved_config_dir = Path(config_dir) if config_dir is not None else bundled_config_dir()
    stats = read_json(resolved_config_dir / "stats.json", default={}) or {}
    return stats if isinstance(stats, dict) else {}
