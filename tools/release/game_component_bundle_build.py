# 核对实际组件构建输入并生成保留原来源声明的发行布局清单。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
from typing import Mapping

from src.integrations.game_component_bundle import inspect_game_component_bundle


LOADER_METADATA = frozenset({
    "LICENSE", "SOURCE.md", "COMPONENT.md", "THIRD_PARTY_LICENSES.md",
    "licenses/MinHook-LICENSE.txt", "licenses/ManualMap-LICENSE.txt",
})


def source_component_manifest(application_root: Path) -> Path:
    return application_root / "third_party/native-capture/component-bundle.json"


@dataclass(frozen=True)
class ComponentBuildInput:
    source: Path
    destination: str


@dataclass(frozen=True)
class PreparedComponentBundle:
    resource_root: Path
    manifest_path: Path
    inputs: tuple[ComponentBuildInput, ...]


def _relative(value: object) -> str:
    if not isinstance(value, str) or "\\" in value or ":" in value:
        raise ValueError("组件清单路径无效。")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts or path.as_posix() != value:
        raise ValueError("组件清单路径无效。")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("组件清单不允许重复字段。")
        result[key] = value
    return result


def loader_distribution_path(source_path: str) -> str:
    """Map the current native Loader and its license files only."""
    key = _relative(source_path)
    if key == "third_party/mod-loader/bin/nte-mod-loader.exe":
        return "nte-mod-loader.exe"
    prefix = "third_party/mod-loader/"
    tail = key.removeprefix(prefix)
    if not key.startswith(prefix) or tail not in LOADER_METADATA:
        raise ValueError("Loader 来源路径包含尚未批准的发行文件。")
    if tail.startswith("licenses/"):
        tail = "dependencies/" + tail.removeprefix("licenses/")
    return "licenses/mod-loader/" + tail


def prepare_component_bundle(
    *, application_root: Path, inputs: Mapping[str, ComponentBuildInput], output_parent: Path,
) -> PreparedComponentBundle:
    """Stage only manifest-approved bytes; never synthesize source commits or hashes.

    The source manifest must cover exactly the selected component inputs. An
    override at another location is accepted only if its bytes match the audited
    source hash. The published manifest changes paths, preserving source identity
    and expected hashes. No build or subprocess is invoked here.
    """
    source = source_component_manifest(application_root)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("构建缺少有效的来源组件整包清单。") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), dict) or not isinstance(payload.get("roles"), dict):
        raise ValueError("来源组件整包清单格式无效。")
    files = payload["files"]
    if payload.get("layout") != "native-capture-v1":
        raise ValueError("仅支持 native-capture-v1 原生组件包，旧 Mods 布局已移除。")
    from tools.release.native_component_bundle_build import native_distribution_path, native_distribution_manifest
    mapper = native_distribution_path
    distribution = native_distribution_manifest(payload)
    if set(inputs) != set(files):
        raise ValueError("组件清单必须覆盖全部实际构建输入及许可、来源文件，且不能携带未选择的文件。")
    destinations: set[str] = set()
    for key, item in inputs.items():
        _relative(key)
        destination = _relative(item.destination)
        if destination.casefold() in destinations:
            raise ValueError("发行组件目标路径重复。")
        destinations.add(destination.casefold())
        if destination != mapper(key):
            raise ValueError("组件来源路径未映射到正式发行布局。")
        if item.source.is_symlink() or not item.source.is_file():
            raise ValueError(f"缺少实际组件构建输入：{key}")
        expected = files[key]
        if not isinstance(expected, str):
            raise ValueError("组件哈希声明无效。")
        with item.source.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected.lower():
            raise ValueError(f"实际构建输入与来源清单哈希不符：{key}")
    output_parent.mkdir(parents=True, exist_ok=True)
    resource_root = Path(tempfile.mkdtemp(prefix="component-candidate-", dir=output_parent))
    staged = []
    for item in inputs.values():
        destination = resource_root / item.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, destination)
        staged.append(ComponentBuildInput(destination, item.destination))
    manifest = resource_root / "component-bundle.json"
    manifest.write_text(json.dumps(distribution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validate_packaged_component_bundle(resource_root)
    return PreparedComponentBundle(resource_root, manifest, tuple(staged))


def _validate_managed_members(resource_root: Path, declared_files: Mapping[str, str], *, directories) -> None:
    """Reject undeclared members and links without traversing unrelated app resources."""
    def kind(path: Path) -> int:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("受管理组件目录不允许文件、目录符号链接或重解析点。")
        return info.st_mode

    try:
        # A linked parent would otherwise evade checks on the component subtree.
        for parent in (resource_root, resource_root / "licenses"):
            if not stat.S_ISDIR(kind(parent)):
                raise ValueError("受管理组件目录不是普通目录。")
        for directory in directories:
            expected = {name for name in declared_files if name.startswith(directory + "/")}
            expected_dirs = {directory}
            for name in expected:
                expected_dirs.update(str(parent) for parent in PurePosixPath(name).parents
                                     if str(parent) == directory or str(parent).startswith(directory + "/"))
            actual = set()
            pending = [resource_root / directory]
            while pending:
                path = pending.pop()
                mode = kind(path)
                relative = path.relative_to(resource_root).as_posix()
                if stat.S_ISDIR(mode):
                    if relative not in expected_dirs:
                        raise ValueError("受管理组件目录包含清单外目录。")
                    pending.extend(path.iterdir())
                elif stat.S_ISREG(mode):
                    actual.add(relative)
                else:
                    raise ValueError("受管理组件目录包含非普通文件。")
            if actual != expected:
                raise ValueError("受管理组件目录实际文件与清单声明不一致，存在缺失或清单外文件。")
    except OSError as exc:
        raise ValueError("无法完整枚举受管理组件目录。") from exc


def validate_packaged_component_bundle(resource_root: Path, *, source_manifest_path: Path | None = None) -> None:
    inspection = inspect_game_component_bundle(resource_root)
    if not inspection.ready:
        raise ValueError("发行组件整包校验失败：" + "；".join(inspection.issues))
    from tools.release.native_component_bundle_build import validate_native_packaged_bundle
    validate_native_packaged_bundle(resource_root, inspection, source_manifest_path)
