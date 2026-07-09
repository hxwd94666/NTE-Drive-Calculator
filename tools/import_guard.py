# 静态检查拆分模块的导入完整性。
"""Import checks for refactor boundaries."""

from __future__ import annotations

import importlib
from collections.abc import Iterable


def find_import_issues(module_names: Iterable[str]) -> list[str]:
    issues: list[str] = []
    for module_name in module_names:
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            issues.append(f"{module_name}: {type(exc).__name__}: {exc}")
    return issues
