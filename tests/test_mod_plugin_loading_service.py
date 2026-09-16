# 验证代理 DLL 与备用 Loader 的互斥加载 contract。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.integrations.mod_loader import (
    ModLoaderRuntimeError,
    _managed_stop_event_name,
    game_launcher_executable,
    packaged_mod_loader,
)


class ModPluginLoadingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.launcher = self.root / 'NTELauncher.exe'
        self.launcher.write_bytes(b'launcher')
        self.game = self.root / 'Client/WindowsNoEditor/HT/Binaries/Win64/HTGame.exe'
        self.game.parent.mkdir(parents=True)
        self.game.write_bytes(b'game')

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_packaged_loader_accepts_a_user_replacement_without_hash_pin(self) -> None:
        loader = (
            self.root
            / "third_party"
            / "mod-loader"
            / "bin"
            / "nte-mod-loader.exe"
        )
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"loader")

        self.assertEqual(packaged_mod_loader(self.root), loader.resolve())

    def test_managed_stop_event_matches_upstream_control_contract(self) -> None:
        self.assertEqual(
            _managed_stop_event_name(
                process_id=0x1234,
                session_suffix=0xABCDEF,
            ),
            "Local\\NTE-DPS-TOOL-ModLoader-0000123400abcdef",
        )

    def test_launcher_is_resolved_from_the_selected_game_installation(self) -> None:
        self.assertEqual(
            game_launcher_executable(self.game),
            self.launcher.resolve(),
        )

    def test_launcher_resolution_rejects_a_nonstandard_game_layout(self) -> None:
        unrelated_game = self.root / "other" / "HTGame.exe"
        unrelated_game.parent.mkdir()
        unrelated_game.write_bytes(b"game")

        with self.assertRaisesRegex(ModLoaderRuntimeError, "官方 Client 目录结构"):
            game_launcher_executable(unrelated_game)


if __name__ == "__main__":
    unittest.main()
