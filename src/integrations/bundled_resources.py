# 通过 Python 包装载位置解析只读发行资源，不依赖当前目录或 runtime 全局值。
"""Locate immutable resources shipped beside the ``src`` package."""

from __future__ import annotations

from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path


@lru_cache(maxsize=1)
def bundled_root() -> Path:
    spec = find_spec("src")
    locations = tuple(spec.submodule_search_locations or ()) if spec is not None else ()
    if not locations:
        raise RuntimeError("无法从 src 包装载位置解析发行资源目录")
    return Path(locations[0]).resolve().parent


def bundled_config_dir() -> Path:
    return bundled_root() / "config"


def bundled_game_ui_asset_root() -> Path:
    return bundled_root() / "assets" / "game_ui"

