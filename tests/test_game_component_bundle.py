# 验证组件整包缺失、哈希、来源与路径声明，以及手动实际部署检查。
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.integrations.game_component_bundle import REQUIRED_BUNDLE_ROLES, inspect_game_component_bundle
from src.services.deployed_plugin_inspection import inspect_deployed_plugin
from src.services.equipment_plugin_deployment import MOD_PLUGIN_SIGNATURE, MOD_WORKSPACE_FILES


class GameComponentBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "component-bundle.json"
        self.files = {}
        self.roles = {}
        native_names = {
            "native_capture": "NTE_Capture.dll", "native_manifest": "native-capture.json",
            "native_license": "NTE_Capture.LICENSE.txt", "native_notices": "NTE_Capture.NOTICES.txt",
        }
        for role in REQUIRED_BUNDLE_ROLES:
            relative = "components/" + native_names.get(role, role + ".bin")
            path = self.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(role.encode())
            self.roles[role] = relative
        native_manifest = self.root / self.roles["native_manifest"]
        native_manifest.write_text(json.dumps({
            "protocol_version": 1, "capabilities": ["combat.hit_buff.v1", "combat.context.v1"],
            "sha256": hashlib.sha256(b"native_capture").hexdigest(),
        }), encoding="utf-8")
        for relative in self.roles.values():
            self.files[relative] = hashlib.sha256((self.root / relative).read_bytes()).hexdigest()
        self.payload = {
            "protocol_version": 1, "source_commits": {"fixture": "a" * 40},
            "files": self.files, "roles": self.roles,
        }

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.payload), encoding="utf-8")

    def test_missing_manifest_does_not_grant_auto_management(self):
        result = inspect_game_component_bundle(self.root)
        self.assertFalse(result.ready)
        self.assertIn("缺少", result.issues[0])

    def test_entire_declared_bundle_is_checked(self):
        self.write_manifest()
        self.assertTrue(inspect_game_component_bundle(self.root).ready)
        (self.root / self.roles["loader"]).write_bytes(b"changed")
        result = inspect_game_component_bundle(self.root)
        self.assertFalse(result.ready)
        self.assertTrue(any("校验失败" in issue for issue in result.issues))

    def test_missing_source_or_role_fails_closed(self):
        self.payload["source_commits"] = {}
        self.write_manifest()
        self.assertFalse(inspect_game_component_bundle(self.root).ready)
        self.payload["source_commits"] = {"fixture": "a" * 40}
        self.roles.pop("loader")
        self.write_manifest()
        self.assertFalse(inspect_game_component_bundle(self.root).ready)

    def test_path_escape_and_duplicate_key_are_rejected(self):
        self.files["../outside.dll"] = "a" * 64
        self.write_manifest()
        self.assertFalse(inspect_game_component_bundle(self.root).ready)
        self.manifest.write_text('{"protocol_version":1,"protocol_version":1}', encoding="utf-8")
        self.assertFalse(inspect_game_component_bundle(self.root).ready)


class DeployedPluginInspectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "game" / "HTGame.exe"
        self.game.parent.mkdir()
        self.game.write_bytes(b"game")
        self.package = self.root / "dwmapi.dll"
        self.package.write_bytes(MOD_PLUGIN_SIGNATURE + b"fixture")
        self.workspace = self.root / "manual-workspace"
        for relative in MOD_WORKSPACE_FILES:
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        (self.workspace / "native-capture.json").write_text(json.dumps({
            "protocol_version": 1, "capabilities": ["combat.hit_buff.v1", "combat.context.v1"],
            "sha256": hashlib.sha256(b"fixture").hexdigest(),
        }), encoding="utf-8")

    def inspect(self):
        with patch("src.services.deployed_plugin_inspection.mod_workspace_registry_snapshot",
                   return_value=(True, str(self.workspace))):
            return inspect_deployed_plugin(application_root=self.root, game_executable_path=self.game)

    def test_bundled_dll_does_not_prove_deployment(self):
        result = self.inspect()
        self.assertFalse(result.compatible)
        self.assertFalse(result.proxy_present)
        self.assertTrue(result.workspace_valid)

    def test_manual_compatible_deployment_without_management_manifest(self):
        (self.game.parent / "dwmapi.dll").write_bytes(self.package.read_bytes())
        result = self.inspect()
        self.assertTrue(result.compatible)
        self.assertFalse(result.proxy_matches_record)
        self.assertTrue(result.proxy_matches_package)

    def test_modified_capture_in_live_workspace_is_not_ready(self):
        (self.game.parent / "dwmapi.dll").write_bytes(self.package.read_bytes())
        (self.workspace / "NTE_Capture.dll").write_bytes(b"modified")
        result = self.inspect()
        self.assertFalse(result.compatible)
        self.assertFalse(result.workspace_valid)
