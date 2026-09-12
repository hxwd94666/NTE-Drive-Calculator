# 只读核验 Calc 随附的整套组件清单，不下载、不部署、不推断业务就绪。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping

from src.integrations.native_capture_release import NATIVE_CAPTURE_FILES, validate_native_capture_release


REQUIRED_BUNDLE_ROLES = frozenset({
    "proxy", "loader", "core", "equipment_mod", "combat_clock_mod", "workspace_version",
    "enabled", "native_capture", "native_manifest", "native_license", "native_notices",
})
_HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
_COMMIT = re.compile(r"[a-fA-F0-9]{7,64}\Z")


@dataclass(frozen=True)
class GameComponentBundleInspection:
    manifest_path: Path
    files: Mapping[str, str]
    roles: Mapping[str, str]
    issues: tuple[str, ...]
    layout: str = "legacy-mods-v1"

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
    """Validate version-1 manifest paths and every declared hash before auto-management.

    All ``files`` keys are application-root-relative POSIX paths. ``roles`` binds
    semantic inputs to those keys. ``source_commits`` is a nonempty mapping from
    public component identifiers to source commit hashes. Missing manifests never
    fall back to a repository branch, filename marker, or a different installation.
    """
    root = Path(application_root).expanduser().resolve()
    native_source = root / "third_party/native-capture/component-bundle.json"
    if native_source.exists():
        from src.integrations.native_plugin_bundle import inspect_native_plugin_bundle
        return inspect_native_plugin_bundle(root)
    source_layout = root / "third_party" / "mods-plugin" / "component-bundle.json"
    manifest = source_layout if source_layout.exists() else root / "component-bundle.json"
    files: dict[str, str] = {}
    roles: dict[str, str] = {}
    issues: list[str] = []
    if not manifest.is_file():
        issues.append("缺少整套游戏组件清单 component-bundle.json；自动管理不可用。")
    else:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
            if isinstance(payload, dict) and payload.get("layout") == "native-capture-v1":
                from src.integrations.native_plugin_bundle import _inspect_native_plugin_payload
                return _inspect_native_plugin_payload(root, manifest, payload)
            if isinstance(payload, dict) and payload.get("layout", "legacy-mods-v1") != "legacy-mods-v1":
                raise ValueError("unsupported layout")
            if not isinstance(payload, dict) or payload.get("protocol_version") != 1:
                raise ValueError("unsupported manifest")
            declared = payload.get("files")
            declared_roles = payload.get("roles")
            commits = payload.get("source_commits")
            if not isinstance(declared, dict) or not declared:
                raise ValueError("missing files")
            if not isinstance(commits, dict) or not commits or any(
                not isinstance(key, str) or not key or not isinstance(value, str)
                or _COMMIT.fullmatch(value) is None for key, value in commits.items()
            ):
                raise ValueError("invalid source commits")
            if not isinstance(declared_roles, dict) or not REQUIRED_BUNDLE_ROLES.issubset(declared_roles):
                raise ValueError("missing component roles")
            seen: set[str] = set()
            for relative, expected in declared.items():
                path = _file_path(root, relative)
                normalized = str(relative).casefold()
                if path is None or normalized in seen or not isinstance(expected, str) or not _HASH.fullmatch(expected):
                    raise ValueError("invalid file entry")
                seen.add(normalized)
                files[relative] = expected.casefold()
                if not path.is_file():
                    issues.append(f"整套组件缺少文件：{relative}")
                    continue
                with path.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected.casefold():
                    issues.append(f"整套组件文件校验失败：{relative}")
            for role, relative in declared_roles.items():
                if not isinstance(role, str) or not isinstance(relative, str) or relative not in files:
                    raise ValueError("role outside declared files")
                roles[role] = relative
            if len({roles[role] for role in REQUIRED_BUNDLE_ROLES}) != len(REQUIRED_BUNDLE_ROLES):
                raise ValueError("component roles share a file")
            native_path = root / roles["native_capture"]
            native_root = native_path.parent
            if native_path.name != "NTE_Capture.dll" or not all(
                (native_root / relative).relative_to(root).as_posix() in files
                for relative in NATIVE_CAPTURE_FILES
            ):
                raise ValueError("native companion files outside bundle")
            validate_native_capture_release(native_root)
        except (OSError, UnicodeError, ValueError, TypeError):
            issues.append("整套游戏组件清单格式、来源或文件声明无效；自动管理不可用。")
    return GameComponentBundleInspection(
        manifest, MappingProxyType(files), MappingProxyType(roles), tuple(issues),
    )
