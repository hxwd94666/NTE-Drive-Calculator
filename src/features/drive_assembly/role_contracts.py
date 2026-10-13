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
    # 原文仅供本次内存识别与消歧；不得把整个识别对象写入日志或账号导出。
    raw_text: str = ""
