# 解析随程序提供的原版分享面板纹理与正式 ID 图标，不依赖 Bot 或网络。
"""Local, manifest-backed NTEUID presentation assets."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src.integrations.bundled_resources import bundled_root


def share_asset_root() -> Path:
    return bundled_root() / "assets" / "loadout_share"


@lru_cache(maxsize=4)
def _manifest(path: str, identity: tuple[int, int]) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def share_asset(group: str, key: str) -> Path | None:
    root = share_asset_root().resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return None
    stat = manifest_path.stat()
    relative = _manifest(str(manifest_path), (stat.st_size, stat.st_mtime_ns)).get(group, {}).get(str(key))
    if not isinstance(relative, str):
        return None
    path = (root / relative).resolve()
    return path if root in path.parents and path.is_file() else None


def share_template(name: str) -> Path:
    path = share_asset("templates", name)
    if path is None:
        raise FileNotFoundError(f"分享面板缺少模板资源：{name}")
    return path
