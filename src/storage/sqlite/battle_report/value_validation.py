# 统一战报原始证据与冻结养成的相同输入校验，保留缺失值而不补零。
from __future__ import annotations

from typing import Any

from ..user_data_support import UserDataValidationError, _integer


def _optional_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=0)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _required_text(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise UserDataValidationError(f"{label} 不能为空")
    return normalized
