# 用临时组件核对真实构建输入、来源保留、发行路径映射和安装前整包门禁。
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.equipment_plugin_deployment import MOD_WORKSPACE_FILES
from tools.release.game_component_bundle_build import (
    BINARY_SOURCE_PATHS, REQUIRED_PROVENANCE_FILES, ROLE_DESTINATIONS,
    component_build_inputs, component_distribution_path, prepare_component_bundle, validate_packaged_component_bundle,
)


class ComponentBundleBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "third_party/mods-plugin/workspace"
        self.binary_paths = {}
        for role, relative in BINARY_SOURCE_PATHS.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture-" + role).encode())
            self.binary_paths[role] = path
        for relative in MOD_WORKSPACE_FILES:
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("fixture-" + relative.as_posix()).encode())
        native_hash = hashlib.sha256((self.workspace / "NTE_Capture.dll").read_bytes()).hexdigest()
        (self.workspace / "native-capture.json").write_text(json.dumps({
            "protocol_version": 1, "capabilities": ["combat.hit_buff.v1", "combat.context.v1"], "sha256": native_hash,
        }), encoding="utf-8")
        self.metadata = {}
        for relative in REQUIRED_PROVENANCE_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic fixture provenance\n", encoding="utf-8")
            self.metadata[relative] = path
        self.inputs = component_build_inputs(
            **self.binary_paths, workspace=self.workspace, metadata=self.metadata,
        )
        by_destination = {item.destination: key for key, item in self.inputs.items()}
        self.payload = {
            "protocol_version": 1,
            "source_commits": {"fixture-core": "a" * 40, "fixture-plugin": "b" * 40},
            "files": {key: hashlib.sha256(item.source.read_bytes()).hexdigest() for key, item in self.inputs.items()},
            "roles": {role: by_destination[destination] for role, destination in ROLE_DESTINATIONS.items()},
        }
        self.source_manifest = self.root / "third_party/mods-plugin/component-bundle.json"
        self.write_manifest()

    def write_manifest(self):
        self.source_manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def prepare(self):
        return prepare_component_bundle(application_root=self.root, inputs=self.inputs, output_parent=self.root / "build")

    def test_maps_every_actual_input_to_resource_root_and_preserves_audited_sources(self):
        prepared = self.prepare()
        manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_commits"], self.payload["source_commits"])
        self.assertEqual(manifest["roles"], ROLE_DESTINATIONS)
        self.assertEqual(set(manifest["files"]), {item.destination for item in self.inputs.values()})
        self.assertFalse(any(path.startswith("third_party/") for path in manifest["files"]))
        self.assertEqual((prepared.resource_root / "plugins/NTE_Capture.dll").read_bytes(), (self.workspace / "NTE_Capture.dll").read_bytes())
        validate_packaged_component_bundle(prepared.resource_root)

    def test_identical_hash_override_is_accepted_without_inventing_provenance(self):
        override = self.root / "explicit-override.exe"
        override.write_bytes(self.binary_paths["core"].read_bytes())
        key = BINARY_SOURCE_PATHS["core"]
        self.inputs[key] = replace(self.inputs[key], source=override)
        self.binary_paths["core"].unlink()
        prepared = self.prepare()
        self.assertEqual((prepared.resource_root / "nte-core.exe").read_bytes(), override.read_bytes())
        self.assertEqual(json.loads(prepared.manifest_path.read_text(encoding="utf-8"))["source_commits"], self.payload["source_commits"])

    def test_different_override_hash_is_rejected(self):
        override = self.root / "explicit-override.exe"
        override.write_bytes(b"different unapproved version")
        key = BINARY_SOURCE_PATHS["loader"]
        self.inputs[key] = replace(self.inputs[key], source=override)
        with self.assertRaisesRegex(ValueError, "哈希不符"):
            self.prepare()

    def test_missing_manifest_is_rejected_before_staging(self):
        self.source_manifest.unlink()
        with self.assertRaisesRegex(ValueError, "来源组件"):
            self.prepare()
        self.assertFalse((self.root / "build").exists())

    def test_missing_sources_cannot_be_recreated_from_git_or_selected_bytes(self):
        self.payload.pop("source_commits")
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "整包校验失败"):
            self.prepare()

    def test_missing_required_input_is_rejected(self):
        self.binary_paths["proxy"].unlink()
        with self.assertRaisesRegex(ValueError, "缺少实际组件"):
            self.prepare()

    def test_provenance_not_declared_in_manifest_is_rejected(self):
        key = next(iter(REQUIRED_PROVENANCE_FILES))
        self.payload["files"].pop(key)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "许可、来源"):
            self.prepare()

    def test_extra_source_member_is_not_silently_copied(self):
        self.payload["files"]["private/source.nte"] = "c" * 64
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "未选择"):
            self.prepare()

    def test_noncanonical_or_escaping_distribution_path_is_rejected(self):
        key = BINARY_SOURCE_PATHS["core"]
        self.inputs[key] = replace(self.inputs[key], destination="../nte-core.exe")
        with self.assertRaisesRegex(ValueError, "路径无效"):
            self.prepare()
        self.inputs[key] = replace(self.inputs[key], destination="dwmapi.dll")
        with self.assertRaisesRegex(ValueError, "正式发行布局"):
            self.prepare()

    def test_installer_gate_rejects_missing_manifest_and_modified_bundled_binary(self):
        prepared = self.prepare()
        (prepared.resource_root / "nte-core.exe").write_bytes(b"tampered after packaging")
        with self.assertRaisesRegex(ValueError, "整包校验失败"):
            validate_packaged_component_bundle(prepared.resource_root)
        prepared.manifest_path.unlink()
        with self.assertRaisesRegex(ValueError, "整包校验失败"):
            validate_packaged_component_bundle(prepared.resource_root)

    def test_build_metadata_does_not_pick_up_unapproved_sources_from_license_directory(self):
        metadata = {**self.metadata, "third_party/mod-loader/licenses/private-source.nte": self.binary_paths["core"]}
        with self.assertRaisesRegex(ValueError, "尚未批准"):
            component_build_inputs(**self.binary_paths, workspace=self.workspace, metadata=metadata)

    def test_release_entry_rejects_missing_source_manifest_without_running_build(self):
        from tools.release import prepare_release

        self.source_manifest.unlink()
        with patch.object(prepare_release, "ROOT", self.root), patch.object(prepare_release, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "源码组件整包"):
                prepare_release.validate_components()
        run.assert_not_called()

    def installer_fields(self, resource_root):
        fake_file = self.root / "other-required-input"
        fake_file.write_bytes(b"fixture")
        names = (
            "APP_EXE", "APP_NTE_CORE", "APP_ANALYSIS_CORE", "APP_ANALYSIS_CORE_MANIFEST", "APP_MODS_PLUGIN",
            "APP_MOD_LOADER", "APP_MOD_SET", "APP_EQUIPMENT_MOD", "APP_COMBAT_CLOCK_MOD", "APP_USER_SCHEMA",
            "APP_STATIC_DATABASE", "APP_STATIC_MANIFEST", "APP_SHARED_DATABASE_SEED", "APP_SHAPE_BONUS_BASELINE",
        )
        fields = {name: fake_file for name in names}
        fields["APP_INTERNAL"] = resource_root
        fields["ROOT"] = self.root
        return fields

    def test_installer_entry_requires_manifest_from_actual_internal_resource_root(self):
        import build_installer

        prepared = self.prepare()
        fields = self.installer_fields(prepared.resource_root)
        with patch.multiple(build_installer, **fields):
            build_installer._validate_app_bundle()
            prepared.manifest_path.unlink()
            with self.assertRaisesRegex(RuntimeError, "游戏组件整包清单"):
                build_installer._validate_app_bundle()

    def test_existing_distribution_must_match_current_approved_sources(self):
        prepared = self.prepare()
        validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)
        self.payload["source_commits"]["fixture-core"] = "c" * 40
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "当前已批准"):
            validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)

    def test_swapped_metadata_and_manifest_hashes_do_not_prove_source_correspondence(self):
        keys = ("third_party/mod-loader/LICENSE", "third_party/mod-loader/SOURCE.md")
        for key in keys:
            self.metadata[key].write_bytes(key.encode())
            self.payload["files"][key] = hashlib.sha256(key.encode()).hexdigest()
        self.write_manifest()
        prepared = self.prepare()
        manifest = json.loads(prepared.manifest_path.read_text(encoding="utf-8"))
        first, second = (component_distribution_path(key) for key in keys)
        first_path, second_path = prepared.resource_root / first, prepared.resource_root / second
        first_bytes, second_bytes = first_path.read_bytes(), second_path.read_bytes()
        first_path.write_bytes(second_bytes)
        second_path.write_bytes(first_bytes)
        manifest["files"][first], manifest["files"][second] = manifest["files"][second], manifest["files"][first]
        prepared.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(sorted(manifest["files"].values()), sorted(self.payload["files"].values()))
        validate_packaged_component_bundle(prepared.resource_root)
        with self.assertRaisesRegex(ValueError, "当前已批准"):
            validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)

    def test_packaged_source_validation_rejects_unknown_canonical_source_path(self):
        prepared = self.prepare()
        key = "third_party/mod-loader/SOURCE.md"
        self.payload["files"]["third_party/mod-loader/unapproved/SOURCE.md"] = self.payload["files"].pop(key)
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, "无法核对"):
            validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)

    def test_skip_build_rejects_every_undeclared_managed_file_with_real_validator(self):
        import build_installer

        prepared = self.prepare()
        with patch.multiple(build_installer, **self.installer_fields(prepared.resource_root)), patch.object(build_installer, "_run") as run:
            build_installer._ensure_app_bundle(skip_app_build=True)
            for relative in ("plugins/private-source.nte", "plugins/nte-mods/debug.pdb",
                             "licenses/nte-core/arbitrary.bin", "licenses/mods-plugin/unlisted",
                             "licenses/mod-loader/private.cpp"):
                with self.subTest(relative=relative):
                    extra = prepared.resource_root / relative
                    extra.write_bytes(b"synthetic undeclared fixture")
                    try:
                        with self.assertRaisesRegex(ValueError, "清单外"):
                            build_installer._ensure_app_bundle(skip_app_build=True)
                    finally:
                        extra.unlink()
            run.assert_not_called()

    def test_managed_extra_empty_directory_is_rejected(self):
        prepared = self.prepare()
        (prepared.resource_root / "plugins/unlisted").mkdir()
        with self.assertRaisesRegex(ValueError, "清单外目录"):
            validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)

    def test_unrelated_python_qt_and_license_resources_remain_allowed(self):
        prepared = self.prepare()
        for relative in ("PySide6/Qt/plugins/platforms/qwindows.dll", "python311.dll", "licenses/another-library/LICENSE"):
            path = prepared.resource_root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unrelated fixture")
        validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)

    def test_managed_file_directory_and_parent_reparse_points_are_rejected(self):
        prepared = self.prepare()
        original = Path.lstat
        for relative in ("plugins/nte-mods/equipment.nte", "plugins/nte-mods", "licenses", "licenses/mod-loader"):
            with self.subTest(relative=relative):
                target = prepared.resource_root / relative

                def simulated_reparse(path):
                    info = original(path)
                    if path == target:
                        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                    return info

                with patch.object(Path, "lstat", autospec=True, side_effect=simulated_reparse):
                    with self.assertRaisesRegex(ValueError, "符号链接或重解析点"):
                        validate_packaged_component_bundle(prepared.resource_root, source_manifest_path=self.source_manifest)
