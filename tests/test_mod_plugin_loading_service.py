# 验证代理 DLL 与备用 Loader 的互斥加载 contract。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.mod_loader import (
    MOD_LOADER_STOP_TIMEOUT_MS,
    ModLoaderRuntimeError,
    ModLoaderRuntimeSnapshot,
    _managed_stop_event_name,
    packaged_mod_loader,
)
from src.services.equipment_plugin_deployment import (
    MOD_PLUGIN_SIGNATURE,
    MOD_WORKSPACE_FILES,
)
from src.services.mod_plugin_loading_service import (
    ModPluginLoadingError,
    ModPluginLoadingService,
)


class _FakeRuntime:
    def __init__(self, *, phase: str = "stopped") -> None:
        self.phase = phase
        self.started_payload: Path | None = None
        self.stop_calls: list[int] = []

    def snapshot(self, *, payload_path):
        return ModLoaderRuntimeSnapshot(
            self.phase,
            Path("loader.exe"),
            Path(payload_path),
            123 if self.phase == "running" else None,
        )

    def start(self, *, payload_path):
        self.started_payload = Path(payload_path)
        self.phase = "running"
        return self.snapshot(payload_path=payload_path)

    def stop(self, *, timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS):
        self.stop_calls.append(timeout_ms)
        was_running = self.phase == "running"
        self.phase = "stopped"
        return was_running

    def close(self):
        self.stop(timeout_ms=MOD_LOADER_STOP_TIMEOUT_MS)


class _FailingRuntime(_FakeRuntime):
    def start(self, *, payload_path):
        raise ModLoaderRuntimeError("launch failed")


class ModPluginLoadingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        plugin = self.root / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll"
        plugin.parent.mkdir(parents=True)
        plugin.write_bytes(MOD_PLUGIN_SIGNATURE + b":0.4.1")
        workspace = self.root / "third_party" / "mods-plugin" / "workspace"
        for relative in MOD_WORKSPACE_FILES:
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("test\n", encoding="utf-8")
        self.game = self.root / "game" / "HTGame.exe"
        self.game.parent.mkdir()
        self.game.write_bytes(b"game")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_packaged_loader_accepts_the_audited_component_layout(self) -> None:
        loader = (
            self.root
            / "third_party"
            / "mod-loader"
            / "bin"
            / "nte-mod-loader.exe"
        )
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"loader")

        with patch(
            "src.integrations.mod_loader._file_sha256",
            return_value=(
                "398039f5314d8e0843e0df2f144e7081a3e2bbe9879afb68a671c9e360af9c80"
            ),
        ):
            self.assertEqual(packaged_mod_loader(self.root), loader.resolve())

    def test_managed_stop_event_matches_upstream_control_contract(self) -> None:
        self.assertEqual(
            _managed_stop_event_name(
                process_id=0x1234,
                session_suffix=0xABCDEF,
            ),
            "Local\\NTE-DPS-TOOL-ModLoader-0000123400abcdef",
        )

    def test_loader_refuses_to_start_while_proxy_dll_exists(self) -> None:
        (self.game.parent / "dwmapi.dll").write_bytes(b"proxy")
        service = ModPluginLoadingService(
            application_root=self.root,
            runtime=_FakeRuntime(),
        )

        with self.assertRaisesRegex(ModPluginLoadingError, "仍存在 dwmapi.dll"):
            service.start_loader(
                game_executable_path=self.game,
                writable_workspace_path=self.root / "writable-mods",
            )

    def test_loader_prepares_workspace_and_starts_audited_payload(self) -> None:
        runtime = _FakeRuntime()
        service = ModPluginLoadingService(
            application_root=self.root,
            runtime=runtime,
        )
        writable = self.root / "writable-mods"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(True, "C:\\previous-mods"),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ), patch(
            "src.services.mod_plugin_loading_service.restore_mod_workspace",
            return_value=True,
        ) as restore_workspace:
            result = service.start_loader(
                game_executable_path=self.game,
                writable_workspace_path=writable,
            )
            service.stop_loader()

        self.assertEqual(result.runtime.phase, "running")
        self.assertEqual(
            runtime.started_payload,
            (self.root / "third_party" / "mods-plugin" / "bin" / "dwmapi.dll").resolve(),
        )
        self.assertTrue((writable / "nte-mods.enabled").is_file())
        restore_workspace.assert_called_once_with(
            workspace_path=writable.resolve(),
            previous_value="C:\\previous-mods",
            previous_value_existed=True,
        )

    def test_loader_start_failure_restores_previous_workspace_registration(self) -> None:
        service = ModPluginLoadingService(
            application_root=self.root,
            runtime=_FailingRuntime(),
        )
        writable = self.root / "writable-mods"

        with patch(
            "src.services.equipment_plugin_deployment._register_mod_workspace",
            return_value=(False, None),
        ), patch(
            "src.services.mod_plugin_loading_service.mod_workspace_registry_snapshot",
            return_value=(True, "C:\\previous-mods"),
        ), patch(
            "src.services.mod_plugin_loading_service.probe_mod_plugin_msvc_runtime",
            return_value={"supported": True, "ready": True, "files": {}},
        ), patch(
            "src.services.mod_plugin_loading_service.restore_mod_workspace",
            return_value=True,
        ) as restore_workspace:
            with self.assertRaisesRegex(ModPluginLoadingError, "launch failed"):
                service.start_loader(
                    game_executable_path=self.game,
                    writable_workspace_path=writable,
                )

        restore_workspace.assert_called_once_with(
            workspace_path=writable.resolve(),
            previous_value="C:\\previous-mods",
            previous_value_existed=True,
        )

    def test_proxy_deployment_is_blocked_while_loader_runs(self) -> None:
        runtime = _FakeRuntime(phase="running")
        service = ModPluginLoadingService(
            application_root=self.root,
            runtime=runtime,
        )

        with self.assertRaisesRegex(ModPluginLoadingError, "正在运行"):
            service.ensure_proxy_deployment_allowed()

        service.stop_loader()
        self.assertEqual(runtime.stop_calls, [MOD_LOADER_STOP_TIMEOUT_MS])


if __name__ == "__main__":
    unittest.main()
