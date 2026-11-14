# 只读核对无界面采集 DLL、最小 D3D 入口与配套 Core 的发行声明。
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.integrations.components.game_component_bundle import (
    GameComponentBundleInspection, _COMMIT, _HASH, _file_path, _unique_object,
)
from src.domain.localization.catalog import TextCatalog, localized_text


NATIVE_PLUGIN_LAYOUT = "native-capture-v1"
HOT_PLUGIN_LAYOUT = "native-plugins-v2"
SPLIT_PLUGIN_LAYOUT = "native-plugins-v3"
HOT_PLUGIN_LAYOUTS = frozenset({HOT_PLUGIN_LAYOUT, SPLIT_PLUGIN_LAYOUT})
NATIVE_PLUGIN_LAYOUTS = frozenset({NATIVE_PLUGIN_LAYOUT, *HOT_PLUGIN_LAYOUTS})
NATIVE_PLUGIN_CAPABILITIES = frozenset({
    "combat.hit_buff.v1", "character.snapshot.v1", "inventory.snapshot.v1",
    "team.snapshot.v1", "environment.snapshot.v1",
})
NATIVE_PLUGIN_DEPLOYMENT_PATHS = MappingProxyType({
    "host": "d3d12.dll", "capture_plugin": "NTE_Capture.dll",
})
HOT_PLUGIN_DEPLOYMENT_PATHS = MappingProxyType({
    "user_plugin": "plugins/NTE_PluginUser.dll",
    "user_signature": "plugins/NTE_PluginUser.dll.sig",
    "capture_plugin": "plugins/NTE_PluginCombat.dll",
    "capture_signature": "plugins/NTE_PluginCombat.dll.sig",
    "host": "d3d12.dll",
})


PERFORMANCE_DEPLOYMENT_PATHS = {
    'performance_plugin': 'plugins/NTE_PluginPerformance.dll',
    'performance_signature': 'plugins/NTE_PluginPerformance.dll.sig',
}
HUD_DEPLOYMENT_PATHS = {
    "hud_plugin": "plugins/NTE_PluginHUD.dll",
    "hud_signature": "plugins/NTE_PluginHUD.dll.sig",
}


TRAINING_DEPLOYMENT_PATHS = {
    "training_plugin": "plugins/NTE_PluginTraining.dll",
    "training_signature": "plugins/NTE_PluginTraining.dll.sig",
}

def native_deployment_paths(layout: str, roles=()) -> Mapping[str, str]:
    if layout == SPLIT_PLUGIN_LAYOUT:
        return MappingProxyType({**HOT_PLUGIN_DEPLOYMENT_PATHS, **HUD_DEPLOYMENT_PATHS,
                                 **PERFORMANCE_DEPLOYMENT_PATHS, **(TRAINING_DEPLOYMENT_PATHS
                                 if set(roles) & TRAINING_DEPLOYMENT_PATHS.keys() else {})})
    if layout == HOT_PLUGIN_LAYOUT:
        return MappingProxyType({**HOT_PLUGIN_DEPLOYMENT_PATHS, **(PERFORMANCE_DEPLOYMENT_PATHS
                                if set(roles) & PERFORMANCE_DEPLOYMENT_PATHS.keys() else {})})
    if layout == NATIVE_PLUGIN_LAYOUT:
        return NATIVE_PLUGIN_DEPLOYMENT_PATHS
    raise ValueError("unsupported native layout")
REQUIRED_NATIVE_PLUGIN_ROLES = frozenset({
    *NATIVE_PLUGIN_DEPLOYMENT_PATHS, "core", "capture_license", "capture_source", "core_license", "core_source",
})


@dataclass(frozen=True)
class NativePluginBundleInspection(GameComponentBundleInspection):
    layout: str = NATIVE_PLUGIN_LAYOUT
    file_sizes: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    input_digests: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    source_commits: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    capture_protocol_version: int | None = None
    native_capabilities: frozenset[str] = frozenset()
    upgrade_from: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def deployment_paths(self) -> Mapping[str, str]:
        return native_deployment_paths(self.layout, self.roles)


def native_upgrade_predecessors(payload: dict) -> Mapping[str, tuple[str, ...]]:
    """Validate reviewed predecessor hashes against exact game deployment paths."""
    declared = payload.get("upgrade_from", {})
    if not isinstance(declared, dict):
        raise ValueError("upgrade_from mapping")
    predecessors = {}
    for relative, hashes in declared.items():
        if relative not in {*NATIVE_PLUGIN_DEPLOYMENT_PATHS.values(), *HOT_PLUGIN_DEPLOYMENT_PATHS.values(),
                            *PERFORMANCE_DEPLOYMENT_PATHS.values(), *HUD_DEPLOYMENT_PATHS.values(), *TRAINING_DEPLOYMENT_PATHS.values()}:
            raise ValueError("upgrade_from deployment path")
        if not isinstance(hashes, list) or not hashes or any(
            not isinstance(digest, str) or not _HASH.fullmatch(digest) for digest in hashes
        ):
            raise ValueError("upgrade_from SHA256 list")
        normalized = tuple(digest.casefold() for digest in hashes)
        if len(set(normalized)) != len(normalized):
            raise ValueError("upgrade_from duplicate SHA256")
        predecessors[relative] = normalized
    return MappingProxyType(predecessors)


def _validate_split_protection(root: Path, roles: dict, files: dict, digests: dict) -> None:
    """Bind every new delivery DLL to its upstream checked protection and notice."""
    relative = Path(roles["capture_source"]).with_name("capture-component.json").as_posix()
    if relative not in files:
        raise ValueError("missing protected component metadata")
    metadata = json.loads((root / relative).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(metadata, dict):
        raise ValueError("protected component metadata")
    if (metadata.get("layout") != SPLIT_PLUGIN_LAYOUT or
            metadata.get("sourceTreeSha256") != digests["capture"]):
        raise ValueError("protected component input identity")
    protection = metadata.get("protection", {})
    if not isinstance(protection, dict):
        raise ValueError("protected component metadata")
    if protection.get("tool") != "VMProtect" or not _HASH.fullmatch(protection.get("policy_sha256", "")):
        raise ValueError("protected component policy")
    binaries = protection.get("binaries", {})
    dll_roles = ("host", "user_plugin", "capture_plugin", "hud_plugin", "performance_plugin") + (("training_plugin",) if "training_plugin" in roles else ())
    expected_names = {Path(roles[role]).name for role in dll_roles}
    if not isinstance(binaries, dict) or set(binaries) != expected_names:
        raise ValueError("all delivery DLLs require protection")
    for role in dll_roles:
        path = roles[role]
        name = Path(path).name
        record = binaries[name]
        if not isinstance(record, dict):
            raise ValueError("DLL protection record")
        options = ["StripDebugInfo"]
        profile = "selected-functions-v1"
        if (record.get("sha256") != files[path] or type(record.get("functions")) is not int or
                record["functions"] <= 0 or record.get("profile") != {"name": profile, "options": options}):
            raise ValueError("DLL protection identity/profile")
        notice = record.get("notice", {})
        if not isinstance(notice, dict):
            raise ValueError("DLL embedded notice")
        if (notice.get("schema") != "nte.component-notice/1" or notice.get("component") != Path(name).stem or
                any(not isinstance(notice.get(key), str) or not notice[key]
                    for key in ("purpose", "authorization", "prohibited_use", "license_boundary"))):
            raise ValueError("DLL embedded notice identity")


def _inspect_native_plugin_payload(
    root: Path, manifest: Path, payload: object, *,
    text_catalog: TextCatalog | None = None,
) -> NativePluginBundleInspection:
    files, roles, sizes, digests, commits = {}, {}, {}, {}, {}
    capabilities = frozenset()
    capture_protocol = None
    predecessors = MappingProxyType({})
    issues = []
    layout = NATIVE_PLUGIN_LAYOUT
    try:
        if not isinstance(payload, dict) or payload.get("layout") not in NATIVE_PLUGIN_LAYOUTS:
            raise ValueError("layout")
        layout = payload["layout"]
        deployment_paths = native_deployment_paths(layout, payload.get("roles", {}))
        required_roles = REQUIRED_NATIVE_PLUGIN_ROLES | deployment_paths.keys()
        if layout in HOT_PLUGIN_LAYOUTS and payload.get("plugin_policy") != "calc-publisher-rsa3072-sha256-v1":
            raise ValueError("publisher policy")
        predecessors = native_upgrade_predecessors(payload)
        if type(payload.get("protocol_version")) is not int or payload["protocol_version"] != 1:
            raise ValueError("manifest protocol")
        if type(payload.get("capture_protocol_version")) is not int or payload["capture_protocol_version"] != 1:
            raise ValueError("capture protocol")
        capture_protocol = 1
        declared, declared_roles = payload.get("files"), payload.get("roles")
        declared_sizes = payload.get("file_sizes")
        declared_commits, declared_digests = payload.get("source_commits"), payload.get("input_digests")
        raw_capabilities = payload.get("capabilities")
        if not isinstance(declared, dict) or not declared or not isinstance(declared_sizes, dict) or set(declared_sizes) != set(declared):
            raise ValueError("files and sizes")
        if not isinstance(declared_roles, dict) or not required_roles.issubset(declared_roles):
            raise ValueError("required roles")
        if not isinstance(declared_commits, dict) or not {"capture", "core"}.issubset(declared_commits) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not _COMMIT.fullmatch(value)
            for key, value in declared_commits.items()
        ):
            raise ValueError("source commits")
        if not isinstance(declared_digests, dict) or set(declared_digests) != {"capture", "core"} or any(
            not isinstance(value, str) or not _HASH.fullmatch(value) for value in declared_digests.values()
        ):
            raise ValueError("input digests")
        if not isinstance(raw_capabilities, list) or any(not isinstance(value, str) or not value.strip() for value in raw_capabilities):
            raise ValueError("capabilities")
        if len(set(raw_capabilities)) != len(raw_capabilities) or not NATIVE_PLUGIN_CAPABILITIES.issubset(raw_capabilities):
            raise ValueError("required capabilities")
        capabilities = frozenset(raw_capabilities)
        if capabilities & {'calc.quick_wheel.v1', 'calc.quick_wheel.v2'} and not TRAINING_DEPLOYMENT_PATHS.keys() <= declared_roles.keys():
            raise ValueError('quick wheel capability requires signed training plugin pair')
        if 'performance.trace.v1' in capabilities and not PERFORMANCE_DEPLOYMENT_PATHS.keys() <= declared_roles.keys():
            raise ValueError('performance capability requires signed plugin pair')
        commits = {key: value.casefold() for key, value in declared_commits.items()}
        digests = {key: value.casefold() for key, value in declared_digests.items()}
        seen = set()
        for relative, expected in declared.items():
            path = _file_path(root, relative)
            size = declared_sizes[relative]
            if path is None or relative.casefold() in seen or not isinstance(expected, str) or not _HASH.fullmatch(expected):
                raise ValueError("file path/hash")
            if type(size) is not int or size < 0:
                raise ValueError("file size")
            seen.add(relative.casefold())
            files[relative], sizes[relative] = expected.casefold(), size
            if not path.is_file():
                issues.append(localized_text(
                    text_catalog,
                    "integration.plugin_bundle.issue.missing_file",
                    "原生整包缺少文件：{relative}",
                    relative=relative,
                ))
                continue
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if path.stat().st_size != size or actual != expected.casefold():
                issues.append(localized_text(
                    text_catalog,
                    "integration.plugin_bundle.issue.mismatch",
                    "原生整包文件大小或哈希不匹配：{relative}",
                    relative=relative,
                ))
        for role, relative in declared_roles.items():
            if not isinstance(role, str) or not isinstance(relative, str) or relative not in files:
                raise ValueError("role binding")
            roles[role] = relative
        if len({roles[role] for role in required_roles}) != len(required_roles):
            raise ValueError("shared required role file")
        for role, filename in {**deployment_paths, "core": "nte-core.exe"}.items():
            relative = roles[role]
            if Path(relative).name != Path(filename).name or sizes[relative] <= 0:
                raise ValueError("program identity")
        if layout == SPLIT_PLUGIN_LAYOUT:
            _validate_split_protection(root, roles, files, digests)
    except (OSError, UnicodeError, ValueError, TypeError):
        issues.append(localized_text(
            text_catalog,
            "integration.plugin_bundle.issue.invalid_manifest",
            "原生采集整包清单的布局、来源、输入摘要、能力或文件声明无效。",
        ))
    return NativePluginBundleInspection(
        manifest, MappingProxyType(files), MappingProxyType(roles), tuple(issues), layout=layout,
        file_sizes=MappingProxyType(sizes), input_digests=MappingProxyType(digests),
        source_commits=MappingProxyType(commits), capture_protocol_version=capture_protocol,
        native_capabilities=capabilities, upgrade_from=predecessors,
    )


def inspect_native_plugin_bundle(
    application_root: str | Path, *, text_catalog: TextCatalog | None = None,
) -> NativePluginBundleInspection:
    """Return declared file facts; never fall back to legacy DLLs or runtime readiness."""
    root = Path(application_root).expanduser().resolve()
    source = root / "third_party/native-capture/component-bundle.json"
    manifest = source if source.exists() else root / "component-bundle.json"
    if not manifest.is_file():
        return NativePluginBundleInspection(manifest, MappingProxyType({}), MappingProxyType({}),
                                            (localized_text(
                                                text_catalog,
                                                "integration.plugin_bundle.issue.bundle_missing",
                                                "缺少 原生采集整包 component-bundle.json；尚无可核对的新组件交付。",
                                            ),))
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, ValueError):
        return NativePluginBundleInspection(manifest, MappingProxyType({}), MappingProxyType({}),
                                            (localized_text(
                                                text_catalog,
                                                "integration.plugin_bundle.issue.bundle_unreadable",
                                                "无法读取有效的 原生采集整包清单。",
                                            ),))
    return _inspect_native_plugin_payload(
        root, manifest, payload, text_catalog=text_catalog,
    )


def resolve_bundled_native_core(application_root: str | Path) -> Path:
    """Choose only the Core covered by a verified native bundle."""
    from src.integrations.components.game_component_bundle import inspect_game_component_bundle

    root = Path(application_root).expanduser().resolve()
    inspection = inspect_game_component_bundle(root)
    if not inspection.ready:
        raise ValueError("原生配套 Core 不可用：" + "；".join(inspection.issues))
    return root / inspection.roles["core"]
