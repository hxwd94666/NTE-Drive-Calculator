# 验证安装包资源去重后仍能恢复两套图鉴路径及内容。
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.release.installer_asset_dedup import (
    inno_asset_copy_lines,
    prepare_installer_asset_stage,
    validate_installer_asset_stage,
)


class InstallerAssetDedupTests(unittest.TestCase):
    def _fixture(self, root: Path) -> Path:
        internal = root / "dist" / "app" / "_internal"
        main = internal / "assets" / "game_ui"
        catalog = internal / "data" / "role_catalog" / "game_ui"
        (main / "characters").mkdir(parents=True)
        (catalog / "characters").mkdir(parents=True)
        (main / "characters" / "main.png").write_bytes(b"same image bytes")
        (catalog / "characters" / "alias.png").write_bytes(b"same image bytes")
        (catalog / "characters" / "unique.png").write_bytes(b"other image bytes")
        (catalog / "manifest.json").write_text("{}", encoding="utf-8")
        return internal

    def test_reuses_identical_image_but_preserves_installed_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._fixture(root)
            stage = root / "build" / "installer-stage" / "_internal"
            copies = prepare_installer_asset_stage(source, stage, root)
            self.assertEqual(1, len(copies))
            self.assertEqual(hashlib.sha256(b"same image bytes").hexdigest(), copies[0].sha256)
            self.assertFalse((stage / copies[0].destination_relative).exists())
            self.assertTrue((stage / "data/role_catalog/game_ui/characters/unique.png").is_file())
            self.assertTrue((stage / "data/role_catalog/game_ui/manifest.json").is_file())
            self.assertIn('DestName: "alias.png"', inno_asset_copy_lines(stage, copies))
            validate_installer_asset_stage(source, stage, copies)

            # Model Inno's second [Files] entry: the installed destination
            # receives the exact original bytes, not a runtime alias.
            for copy in copies:
                target = stage / copy.destination_relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / copy.source_relative, target)
                self.assertEqual((source / copy.destination_relative).read_bytes(), target.read_bytes())

    def test_changed_donor_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self._fixture(root)
            stage = root / "build" / "installer-stage" / "_internal"
            copies = prepare_installer_asset_stage(source, stage, root)
            (stage / copies[0].source_relative).write_bytes(b"dame image bytes")
            with self.assertRaisesRegex(RuntimeError, "哈希不匹配"):
                validate_installer_asset_stage(source, stage, copies)


if __name__ == "__main__":
    unittest.main()
