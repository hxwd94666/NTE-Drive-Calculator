# 汇总外部本地服务的稳定调用入口。
"""Integration clients for local companion processes."""

from src.integrations.nte_core import (
    NteCoreClient,
    NteCoreError,
    NteCoreNotFoundError,
    NteCoreProcessError,
    NteCoreProtocolError,
    NteCoreRpcError,
    NteCoreTimeoutError,
    resolve_nte_core_executable,
)

__all__ = [
    "NteCoreClient",
    "NteCoreError",
    "NteCoreNotFoundError",
    "NteCoreProcessError",
    "NteCoreProtocolError",
    "NteCoreRpcError",
    "NteCoreTimeoutError",
    "resolve_nte_core_executable",
]
