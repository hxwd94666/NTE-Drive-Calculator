# 探测已冻结角色读取输入的文件身份，并解析随附图片根目录。
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from src.integrations.bundled_resources import bundled_game_ui_asset_root


def _asset_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value)
    # 兼容调用也只能读取随程序发布的资源，不能受进程当前目录影响。
    return bundled_game_ui_asset_root()


def probe_role_read_inputs(
    database_paths: Sequence[Path], asset_root: Path | None, *,
    template_path: Path | None, semantics_path: Path,
) -> tuple:
    """仅探测调用方给定的输入；不寻找或替换账号与静态来源。"""
    def stamp(path: Path):
        try:
            info = path.stat()
            return info.st_ino, info.st_size, info.st_mtime_ns
        except OSError:
            return None

    return (
        tuple((stamp(path), stamp(path.with_name(path.name + "-wal"))) for path in database_paths),
        stamp(asset_root / "manifest.json") if asset_root else None,
        stamp(template_path) if template_path is not None else None,
        stamp(semantics_path),
    )
