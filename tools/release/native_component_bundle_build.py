# 将原生组件声明映射到发行目录，仅携带显式允许的程序、公开清单与许可。
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from src.integrations.game_component_bundle import inspect_game_component_bundle
from src.integrations.native_plugin_bundle import native_upgrade_predecessors


NATIVE_ROLE_DESTINATIONS = {
    "host": "d3d12.dll", "capture_plugin": "NTE_Capture.dll", "core": "nte-core.exe",
    "capture_license": "licenses/native-capture/capture/LICENSE.txt",
    "capture_source": "licenses/native-capture/capture/SOURCE.md",
    "core_license": "licenses/native-capture/core/LICENSE",
    "core_source": "licenses/native-capture/core/SOURCE.md",
}
OPTIONAL_LOADER_ROLES = {
    "loader": "nte-mod-loader.exe", "loader_license": "licenses/mod-loader/LICENSE",
    "loader_source": "licenses/mod-loader/SOURCE.md",
}
NATIVE_PROGRAMS = {
    "third_party/native-capture/capture/d3d12.dll": "d3d12.dll",
    "third_party/native-capture/capture/NTE_Capture.dll": "NTE_Capture.dll",
    "third_party/native-capture/core/nte-core.exe": "nte-core.exe",
}
NATIVE_NOTICES = frozenset({
    "capture/LICENSE.txt", "capture/SOURCE.md", "capture/Detours-LICENSE.md",
    "capture/capture-component.json", "compatibility.json", "core/LICENSE", "core/SOURCE.md",
    "core/LICENSING.md", "core/NOTICE.md", "core/THIRD_PARTY_LICENSES.md",
})
DEPENDENCY_NOTICE_NAMES = frozenset({
    "LICENSE", "LICENSE.txt", "LICENSE-APACHE", "LICENSE-MIT", "LICENSE-THIRD-PARTY",
    "LICENSE-UNICODE", "COPYING", "PACKAGE_NOTICE.md", "license-apache-2.0", "license-mit",
})


def native_distribution_path(source_path: str) -> str:
    from tools.release.game_component_bundle_build import _relative, loader_distribution_path

    key = _relative(source_path)
    if key in NATIVE_PROGRAMS:
        return NATIVE_PROGRAMS[key]
    if key.startswith("third_party/mod-loader/"):
        return loader_distribution_path(key)
    prefix = "third_party/native-capture/"
    if not key.startswith(prefix):
        raise ValueError("原生组件来源路径不属于允许的发行输入。")
    tail = key.removeprefix(prefix)
    parts = PurePosixPath(tail).parts
    notice = (len(parts) == 4 and parts[:2] == ("core", "licenses")
              and parts[-1] in DEPENDENCY_NOTICE_NAMES)
    if tail not in NATIVE_NOTICES and not notice:
        raise ValueError("原生组件包含尚未批准的发行文件，禁止携带源码或归档。")
    return "licenses/native-capture/" + tail


def _validate_native_contract(payload: dict) -> None:
    native_upgrade_predecessors(payload)
    files, roles = payload["files"], payload["roles"]
    mandatory = {"third_party/native-capture/" + name for name in NATIVE_NOTICES}
    if not mandatory.issubset(files):
        raise ValueError("原生组件缺少完整许可、来源或配套声明。")
    mapped_roles = {role: native_distribution_path(path) for role, path in roles.items()}
    if any(mapped_roles.get(role) != path for role, path in NATIVE_ROLE_DESTINATIONS.items()):
        raise ValueError("原生组件角色未映射到正式发行布局。")
    loader_files = any(path.startswith("third_party/mod-loader/") for path in files)
    loader_roles = bool(set(roles) & set(OPTIONAL_LOADER_ROLES))
    if loader_files or loader_roles:
        if any(mapped_roles.get(role) != path for role, path in OPTIONAL_LOADER_ROLES.items()):
            raise ValueError("可选 Loader 必须成套声明程序、许可和来源。")
        required = {"third_party/mod-loader/" + name for name in (
            "THIRD_PARTY_LICENSES.md", "licenses/MinHook-LICENSE.txt", "licenses/ManualMap-LICENSE.txt",
        )}
        if not required.issubset(files):
            raise ValueError("可选 Loader 缺少第三方许可。")


def native_component_build_inputs(application_root: Path):
    from tools.release.game_component_bundle_build import ComponentBuildInput, _unique

    inspection = inspect_game_component_bundle(application_root)
    if inspection.layout != "native-capture-v1" or not inspection.ready:
        raise ValueError("原生来源整包未通过核对：" + "；".join(inspection.issues))
    payload = json.loads(inspection.manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    _validate_native_contract(payload)
    inputs = {key: ComponentBuildInput(application_root / key, native_distribution_path(key))
              for key in inspection.files}
    # All dependency notices present in the delivered source must be explicit inputs.
    directory = application_root / "third_party/native-capture/core/licenses"
    for path in directory.rglob("*"):
        if path.is_file() and path.relative_to(application_root).as_posix() not in inputs:
            raise ValueError("原生 Core 依赖许可未全部列入组件清单。")
    return inputs


def native_distribution_manifest(payload: dict) -> dict:
    _validate_native_contract(payload)
    result = dict(payload)
    result["files"] = {native_distribution_path(key): value for key, value in payload["files"].items()}
    result["file_sizes"] = {native_distribution_path(key): value for key, value in payload["file_sizes"].items()}
    result["roles"] = {role: native_distribution_path(key) for role, key in payload["roles"].items()}
    return result


def validate_native_packaged_bundle(resource_root: Path, inspection, source_manifest_path: Path | None) -> None:
    from tools.release.game_component_bundle_build import _unique, _validate_managed_members

    bundled = json.loads(inspection.manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    required = dict(NATIVE_ROLE_DESTINATIONS)
    loader_present = bool(set(inspection.roles) & set(OPTIONAL_LOADER_ROLES))
    if loader_present:
        required.update(OPTIONAL_LOADER_ROLES)
    if any(inspection.roles.get(role) != destination for role, destination in required.items()):
        raise ValueError("发行原生组件不是当前资源目录布局。")
    # Reverse the approved paths to validate the same license and source contract.
    reverse = {destination: source for source, destination in NATIVE_PROGRAMS.items()}
    reverse["nte-mod-loader.exe"] = "third_party/mod-loader/bin/nte-mod-loader.exe"
    source_files = {}
    for path, digest in inspection.files.items():
        if path in reverse:
            key = reverse[path]
        elif path.startswith("licenses/native-capture/"):
            key = "third_party/native-capture/" + path.removeprefix("licenses/native-capture/")
        elif path.startswith("licenses/mod-loader/"):
            tail = path.removeprefix("licenses/mod-loader/")
            key = "third_party/mod-loader/" + tail.replace("dependencies/", "licenses/", 1)
        else:
            raise ValueError("发行原生组件包含未批准路径。")
        if native_distribution_path(key) != path:
            raise ValueError("发行原生组件路径不规范。")
        reverse[path], source_files[key] = key, digest
    _validate_native_contract({"files": source_files,
                               "roles": {role: reverse[path] for role, path in inspection.roles.items()}})
    directories = ("licenses/native-capture",) + (("licenses/mod-loader",) if loader_present else ())
    _validate_managed_members(resource_root, inspection.files, directories=directories)
    for relative in ("plugins", "dwmapi.dll", "licenses/mods-plugin"):
        if (resource_root / relative).exists():
            raise ValueError("发行原生组件混入旧 Mods 插件或工作区。")
    if not loader_present and ((resource_root / "nte-mod-loader.exe").exists()
                               or (resource_root / "licenses/mod-loader").exists()):
        raise ValueError("发行原生组件包含未声明 Loader。")
    if source_manifest_path is not None:
        source = json.loads(source_manifest_path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
        if native_distribution_manifest(source) != bundled:
            raise ValueError("发行原生组件的来源、能力、输入摘要或文件与当前已批准输入不一致。")
