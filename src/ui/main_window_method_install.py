# 将拆分模块的方法挂载到主窗口类。
"""Helpers for installing extracted MainWindow methods."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def install_methods(
    app_module,
    window_cls: type,
    method_names: Iterable[str],
    namespace: dict[str, Any],
) -> None:
    """Bind extracted methods onto MainWindow.

    `app_module` is accepted for the existing installation call signature; feature
    modules must import their runtime dependencies explicitly.
    """
    _ = app_module
    for name in method_names:
        setattr(window_cls, name, namespace[name])
