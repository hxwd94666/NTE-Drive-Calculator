# 定义角色识别与自动装配遍历共享的不可变识别结果。
"""Shared role-recognition contract for navigation and planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleRecognition:
    """A normalized role recognition result."""

    role_name: str | None
    method: str
    confidence: float
    raw_text: str = ""
