# 对预审过的显式模块清单执行导入冒烟；导入可能带副作用，不是静态检查。
"""显式导入诊断只汇总异常，不证明导入无副作用或业务就绪。"""

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
