# 验证托管组件清理的归属、退出等待、幂等和加载配置边界。
from __future__ import annotations

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    cleanup_mod_workspace,
)
from src.services.managed_plugin_cleanup import cleanup_managed_plugin, inspect_managed_plugin


class ManagedPluginCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.game = self.root / "HTGame.exe"
        self.game.write_bytes(b"game")
        self.dll = self.root / "dwmapi.dll"
        self.dll.write_bytes(b"managed")
        self.workspace = self.root / "workspace"
        self.registry = patch(
            "src.services.managed_plugin_cleanup.mod_workspace_registry_snapshot",
            return_value=(False, None),
        ).start()
        self.clear_registry = patch(
            "src.services.managed_plugin_cleanup.cleanup_mod_workspace", return_value=True,
        ).start()
        self.addCleanup(patch.stopall)

    def clean(self, **kwargs):
        return cleanup_managed_plugin(
            game_executable_path=self.game,
            mod_workspace_path=self.workspace, **kwargs,
        )

    def test_running_game_defers_every_mutation_and_retry_cleans(self) -> None:
        self.assertEqual(self.clean(game_running=lambda: True).status, "waiting_game_exit")
        self.assertEqual(self.dll.read_bytes(), b"managed")
        self.clear_registry.assert_not_called()
        self.assertEqual(self.clean(game_running=lambda: False).status, "cleaned")
        self.assertFalse(self.dll.exists())

    def test_missing_executable_and_dll_are_already_cleaned(self) -> None:
        self.game.unlink()
        self.dll.unlink()
        self.assertEqual(self.clean(game_running=lambda: False).status, "cleaned")
        self.assertEqual(self.clean(game_running=lambda: False).status, "cleaned")

    def test_old_proxy_without_deployment_record_is_removed(self) -> None:
        result = cleanup_managed_plugin(game_executable_path=self.game, game_running=lambda: False)
        self.assertEqual(result.status, "cleaned")
        self.assertFalse(self.dll.exists())
        self.clear_registry.assert_called_once()

    def test_changed_registry_preserves_dll_and_registration(self) -> None:
        self.registry.return_value = (True, str(self.root / "another-workspace"))
        self.assertEqual(self.clean(game_running=lambda: False).status, "conflict")
        self.assertTrue(self.dll.exists())
        self.clear_registry.assert_not_called()

    def test_background_cleanup_preserves_unrecorded_application_registry(self) -> None:
        self.registry.return_value = (True, str(self.workspace))
        result = cleanup_managed_plugin(
            game_executable_path=self.game,
            game_running=lambda: False,
        )
        self.assertEqual(result.status, "conflict")
        self.assertTrue(self.dll.exists())
        self.clear_registry.assert_not_called()

    def test_explicit_cleanup_adopts_unrecorded_application_registry(self) -> None:
        self.registry.return_value = (True, str(self.workspace))
        self.clear_registry.side_effect = lambda **_kwargs: self.registry.configure_mock(
            return_value=(False, None),
        ) or True
        result = cleanup_managed_plugin(
            game_executable_path=self.game,
            game_running=lambda: False,
            allow_unrecorded_workspace_adoption=True,
        )
        self.assertEqual(result.status, "cleaned")
        self.assertFalse(self.dll.exists())
        self.clear_registry.assert_called_once_with(workspace_path=str(self.workspace))

    def test_directory_at_dll_location_is_not_deleted(self) -> None:
        self.dll.unlink()
        self.dll.mkdir()
        self.assertEqual(self.clean(game_running=lambda: False).status, "conflict")
        self.assertTrue(self.dll.is_dir())

    def test_game_launching_during_inspection_prevents_deletion(self) -> None:
        probe = MagicMock(side_effect=[False, True])
        self.assertEqual(self.clean(game_running=probe).status, "waiting_game_exit")
        self.assertTrue(self.dll.exists())
        self.clear_registry.assert_not_called()

    def test_unknown_process_state_never_deletes(self) -> None:
        probe = MagicMock(side_effect=EquipmentPluginDeploymentError("process unavailable"))
        with self.assertRaises(EquipmentPluginDeploymentError):
            self.clean(game_running=probe)
        self.assertTrue(self.dll.exists())
        self.clear_registry.assert_not_called()

    def test_read_only_inspection_does_not_claim_pipe_or_business_readiness(self) -> None:
        result = inspect_managed_plugin(
            game_executable_path=self.game,
            game_running=lambda: False,
        )
        self.assertEqual(result.dll_state, "managed")
        self.assertTrue(self.dll.exists())
        self.clear_registry.assert_not_called()

    def test_different_old_proxy_bytes_do_not_block_filename_cleanup(self) -> None:
        def process():
            if process.calls == 1:
                self.dll.write_bytes(b"changed")
            process.calls += 1
            return False
        process.calls = 0
        self.assertEqual(self.clean(game_running=process).status, "cleaned")
        self.assertFalse(self.dll.exists())


class WorkspaceRegistrationCleanupTests(unittest.TestCase):
    def test_cleanup_deletes_owned_value_without_restoring_historical_value(self) -> None:
        workspace = Path("owned-workspace").resolve()
        key = MagicMock()
        registry = SimpleNamespace(
            HKEY_CURRENT_USER=1, KEY_QUERY_VALUE=2, KEY_SET_VALUE=4, REG_SZ=1,
            OpenKey=MagicMock(return_value=key),
            QueryValueEx=MagicMock(return_value=(str(workspace), 1)),
            DeleteValue=MagicMock(), SetValueEx=MagicMock(),
        )
        with patch("src.services.equipment_plugin_deployment.os.name", "nt"), patch(
            "src.services.equipment_plugin_deployment.mod_workspace_registry_snapshot",
            return_value=(True, str(workspace)),
        ), patch.dict("sys.modules", {"winreg": registry}):
            self.assertTrue(cleanup_mod_workspace(workspace_path=workspace))
        registry.DeleteValue.assert_called_once()
        registry.SetValueEx.assert_not_called()

    def test_absent_registration_is_already_cleaned(self) -> None:
        with patch(
            "src.services.equipment_plugin_deployment.mod_workspace_registry_snapshot",
            return_value=(False, None),
        ):
            self.assertTrue(cleanup_mod_workspace(workspace_path=None))

    def test_unknown_registration_is_not_removed(self) -> None:
        with patch(
            "src.services.equipment_plugin_deployment.mod_workspace_registry_snapshot",
            return_value=(True, "another-workspace"),
        ):
            with self.assertRaisesRegex(EquipmentPluginDeploymentError, "已修改"):
                cleanup_mod_workspace(workspace_path=Path("owned-workspace"))
