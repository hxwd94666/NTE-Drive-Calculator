# 收集 Windows、管理员权限、目标版本和关键文件哈希等只读预检证据。
"""Read-only environment and artifact probes."""

from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def is_administrator() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_evidence(paths: Iterable[Path]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for path in paths:
        resolved = path.resolve()
        row: dict[str, object] = {"exists": resolved.exists()}
        if resolved.is_file():
            stat = resolved.stat()
            row.update(
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
                sha256=sha256_file(resolved),
            )
        evidence[str(resolved)] = row
    return evidence


def environment_evidence(target: Path | None = None) -> dict[str, object]:
    try:
        from src.app.version import __version__ as app_version
    except ImportError:
        app_version = "unknown"
    return {
        "platform": platform.platform(),
        "windows_release": platform.release(),
        "python": sys.version.split()[0],
        "architecture": platform.machine(),
        "administrator": is_administrator(),
        "app_version": app_version,
        "npcap_service": windows_service_state("npcap"),
        "target": str(target.resolve()) if target is not None else "",
    }


def windows_service_state(service_name: str) -> str:
    if os.name != "nt":
        return "not-windows"
    completed = subprocess.run(
        ["sc.exe", "query", service_name],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return "not-found"
    for state in ("RUNNING", "STOPPED", "PAUSED", "START_PENDING", "STOP_PENDING"):
        if state in completed.stdout:
            return state.casefold().replace("_", "-")
    return "unknown"


def default_artifact_paths(target: Path | None) -> tuple[Path, ...]:
    roots = [Path.cwd()]
    if target is not None:
        roots.insert(0, target if target.is_dir() else target.parent)
    relative_candidates = (
        Path("_internal/nte-core.exe"),
        Path("_internal/dwmapi.dll"),
        Path("_internal/nte-mod-loader.exe"),
        Path("_internal/plugins/nte-mods/equipment.nte"),
        Path("third_party/nte-core/bin/nte-core.exe"),
        Path("third_party/mods-plugin/bin/dwmapi.dll"),
        Path("third_party/mod-loader/bin/nte-mod-loader.exe"),
        Path("third_party/mods-plugin/workspace/nte-mods/equipment.nte"),
    )
    found: list[Path] = []
    for root in roots:
        for relative in relative_candidates:
            candidate = root / relative
            if candidate.is_file() and candidate.resolve() not in {
                path.resolve() for path in found
            }:
                found.append(candidate)
    return tuple(found)
