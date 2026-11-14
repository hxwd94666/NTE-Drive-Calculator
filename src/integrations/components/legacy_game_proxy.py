# 仅供明确手动清理的调用方核对并移除游戏目录旧代理入口。
from __future__ import annotations

from pathlib import Path
import stat
from typing import Callable


LEGACY_GAME_PROXY = 'dwmapi.dll'


def legacy_game_proxy_present(game_directory: Path) -> bool:
    """Include broken links and directories so management cannot skip a conflict."""
    target = game_directory / LEGACY_GAME_PROXY
    try:
        target.lstat()
    except FileNotFoundError:
        return False
    return True


def _identity(path: Path) -> tuple[int, int, int, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise OSError('游戏目录中的 dwmapi.dll 不是可安全管理的普通文件，已停止原生组件管理。')
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def remove_legacy_game_proxy(
    *, game_directory: Path, require_idle: Callable[[], None],
) -> None:
    """Delete only the old game-local deployment copy, without keeping a backup."""
    require_idle()
    directory = Path(game_directory).resolve()
    target = directory / LEGACY_GAME_PROXY
    if not legacy_game_proxy_present(directory):
        return
    original = _identity(target)
    require_idle()
    if _identity(target) != original:
        raise OSError('旧代理在移除前发生变化，未删除游戏目录中的 dwmapi.dll。')
    require_idle()
    if _identity(target) != original:
        raise OSError('旧代理在移除前发生变化，未删除游戏目录中的 dwmapi.dll。')
    target.unlink()
