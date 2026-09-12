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
from src.services.equipment_plugin_deployment import MOD_WORKSPACE_FILES


ROLE_DESTINATIONS = {
    "proxy": "dwmapi.dll", "loader": "nte-mod-loader.exe", "core": "nte-core.exe",
    "equipment_mod": "plugins/nte-mods/equipment.nte", "combat_clock_mod": "plugins/nte-mods/combat-clock.nte",
    "workspace_version": "plugins/mods-plugin.version", "enabled": "plugins/nte-mods.enabled",
    "native_capture": "plugins/NTE_Capture.dll", "native_manifest": "plugins/native-capture.json",
    "native_license": "plugins/NTE_Capture.LICENSE.txt", "native_notices": "plugins/NTE_Capture.NOTICES.txt",
}
BINARY_SOURCE_PATHS = {
    "core": "third_party/nte-core/bin/nte-core.exe",
    "proxy": "third_party/mods-plugin/bin/dwmapi.dll",
    "loader": "third_party/mod-loader/bin/nte-mod-loader.exe",
}
REQUIRED_PROVENANCE_FILES = frozenset(
    f"third_party/{component}/{name}" for component in ("nte-core", "mods-plugin", "mod-loader")
    for name in ("LICENSE", "SOURCE.md")
)
ALLOWED_METADATA = {
    "nte-core": frozenset({"LICENSE", "SOURCE.md", "COMPONENT.md", "BUILD_VARIANT.md", "CLI_PROTOCOL_ZH.md", "CLI_PROTOCOL.md", "THIRD_PARTY_LICENSES.md"}),
    "mods-plugin": frozenset({"LICENSE", "SOURCE.md", "COMPONENT.md"}),
    "mod-loader": frozenset({"LICENSE", "SOURCE.md", "COMPONENT.md", "THIRD_PARTY_LICENSES.md", "licenses/MinHook-LICENSE.txt", "licenses/ManualMap-LICENSE.txt"}),
}
MANAGED_DIRECTORIES = ("plugins", "licenses/nte-core", "licenses/mods-plugin", "licenses/mod-loader")


def source_component_manifest(application_root: Path) -> Path:
    native = application_root / "third_party/native-capture/component-bundle.json"
    return native if native.exists() else application_root / "third_party/mods-plugin/component-bundle.json"


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


def component_distribution_path(source_path: str) -> str:
    """Map an approved canonical source path to its sole distribution location."""
    key = _relative(source_path)
    for role, canonical in BINARY_SOURCE_PATHS.items():
        if key == canonical:
            return ROLE_DESTINATIONS[role]
    for relative in MOD_WORKSPACE_FILES:
        if key == "third_party/mods-plugin/workspace/" + relative.as_posix():
            return "plugins/" + relative.as_posix()
    parts = PurePosixPath(key).parts
    if len(parts) < 3 or parts[0] != "third_party" or parts[1] not in ALLOWED_METADATA:
        raise ValueError("组件来源路径不属于允许的发行输入。")
    tail = "/".join(parts[2:])
    if tail not in ALLOWED_METADATA[parts[1]]:
        raise ValueError("组件来源路径包含尚未批准的发行文件。")
    if parts[1] == "mod-loader" and tail.startswith("licenses/"):
        tail = "dependencies/" + tail.removeprefix("licenses/")
    return f"licenses/{parts[1]}/{tail}"


def component_build_inputs(
    *, core: Path, proxy: Path, loader: Path, workspace: Path,
    metadata: Mapping[str, Path],
) -> dict[str, ComponentBuildInput]:
    """Bind resolved build inputs, including overrides, to canonical source records."""
    inputs = {
        BINARY_SOURCE_PATHS[role]: ComponentBuildInput(Path(path), component_distribution_path(BINARY_SOURCE_PATHS[role]))
        for role, path in (("core", core), ("proxy", proxy), ("loader", loader))
    }
    for relative in MOD_WORKSPACE_FILES:
        key = "third_party/mods-plugin/workspace/" + relative.as_posix()
        inputs[key] = ComponentBuildInput(workspace / relative, component_distribution_path(key))
    for raw_key, path in metadata.items():
        key = _relative(raw_key)
        if key in inputs:
            raise ValueError("组件元数据不能替换程序或工作区输入。")
        inputs[key] = ComponentBuildInput(Path(path), component_distribution_path(key))
    return inputs


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
    files, roles = payload["files"], payload["roles"]
    native = payload.get("layout") == "native-capture-v1"
    if native:
        from tools.release.native_component_bundle_build import native_distribution_path, native_distribution_manifest
        mapper = native_distribution_path
        distribution = native_distribution_manifest(payload)
    else:
        mapper = component_distribution_path
    if set(inputs) != set(files) or (not native and not REQUIRED_PROVENANCE_FILES.issubset(files)):
        raise ValueError("组件清单必须覆盖全部实际构建输入及许可、来源文件，且不能携带未选择的文件。")
    destinations: set[str] = set()
    mapped_files: dict[str, str] = {}
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
        mapped_files[destination] = expected
    try:
        mapped_roles = {role: inputs[key].destination for role, key in roles.items()}
    except (KeyError, TypeError) as exc:
        raise ValueError("组件角色不在实际构建输入中。") from exc
    if not native and any(mapped_roles.get(role) != destination for role, destination in ROLE_DESTINATIONS.items()):
        raise ValueError("组件角色未映射到正式发行布局。")
    if not native:
        distribution = {
            "protocol_version": payload.get("protocol_version"), "source_commits": payload.get("source_commits"),
            "files": mapped_files, "roles": mapped_roles,
        }
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


def _validate_managed_members(resource_root: Path, declared_files: Mapping[str, str], *, directories=MANAGED_DIRECTORIES) -> None:
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
    if inspection.layout == "native-capture-v1":
        from tools.release.native_component_bundle_build import validate_native_packaged_bundle
        validate_native_packaged_bundle(resource_root, inspection, source_manifest_path)
        return
    if any(inspection.roles.get(role) != destination for role, destination in ROLE_DESTINATIONS.items()):
        raise ValueError("发行组件清单不是当前资源目录布局。")
    expected_provenance = {f"licenses/{component}/{name}" for component in ("nte-core", "mods-plugin", "mod-loader")
                           for name in ("LICENSE", "SOURCE.md")}
    if not expected_provenance.issubset(inspection.files):
        raise ValueError("发行组件整包缺少许可或来源记录。")
    _validate_managed_members(resource_root, inspection.files)
    if source_manifest_path is not None:
        try:
            source = json.loads(source_manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
            bundled = json.loads(inspection.manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
            expected_files = {component_distribution_path(key): value.lower() for key, value in source["files"].items()}
            expected_roles = {role: component_distribution_path(key) for role, key in source["roles"].items()}
            consistent = (
                source["source_commits"] == bundled["source_commits"]
                and source["protocol_version"] == bundled["protocol_version"]
                and expected_files == dict(inspection.files)
                and expected_roles == dict(inspection.roles)
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError("无法核对发行组件与已批准来源清单。") from exc
        if not consistent:
            raise ValueError("发行组件的来源路径或哈希与当前已批准输入不一致。")
