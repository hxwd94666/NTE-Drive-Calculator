# 只读核验 Calc 随附的整套组件清单，不下载、不部署、不推断业务就绪。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Mapping


_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
_COMMIT = re.compile(r"[a-fA-F0-9]{7,64}\Z")


@dataclass(frozen=True)
class GameComponentBundleInspection:
    manifest_path: Path
    files: Mapping[str, str]
    roles: Mapping[str, str]
    issues: tuple[str, ...]
    layout: str = "native-capture-v1"

    @property
    def ready(self) -> bool:
        return not self.issues


def _file_path(root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        return None
    parts = PurePosixPath(relative)
    if parts.as_posix() != relative or parts.is_absolute() or not parts.parts or any(item in {".", ".."} for item in parts.parts):
        return None
    candidate = root.joinpath(*parts.parts)
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
        return None
    return candidate


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest key")
        result[key] = value
    return result


def inspect_game_component_bundle(application_root: str | Path) -> GameComponentBundleInspection:
    """Inspect the sole supported native bundle, without a legacy package fallback."""
    from src.integrations.native_plugin_bundle import inspect_native_plugin_bundle
    return inspect_native_plugin_bundle(application_root)
