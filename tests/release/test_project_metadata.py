# 测试版本、工程元数据与本地发布准备工具保持一致。
import json
import tempfile
import unittest
from pathlib import Path

from src.app.constants import APP_VERSION
from src.app.version import __package_version__, __version__, windows_numeric_version
from src.integrations.updates.mirror import is_newer_version
from tools.release import prepare_release


ROOT = Path(__file__).resolve().parents[2]


class ProjectMetadataTests(unittest.TestCase):
    def test_runtime_and_release_tool_share_one_version(self):
        self.assertEqual(__version__, APP_VERSION)
        self.assertEqual(
            f"NTE_Drive_Calc_Setup_{__version__}.exe",
            prepare_release.INSTALLER_NAME,
        )

    def test_dated_test_versions_keep_release_order_and_package_identity(self):
        self.assertEqual(__package_version__, __version__.replace(".T", ".dev"))
        for remote, current, newer in (
            ("2.3.1.T261003", "2.3.1.T261002", True),
            ("2.3.1.T261002", "2.3.1.T261002", False),
            ("2.3.1.T261001", "2.3.1.T261002", False),
            ("2.3.1", "2.3.1.T261002", True),
            ("2.3.1.T261002", "2.3.1", False),
            ("2.3.2", "2.3.1.T261002", True),
            ("2.3.0", "2.3.1.T261002", False),
            ("v2.3.1.T261002", "2.3.0", True),
        ):
            with self.subTest(remote=remote, current=current):
                self.assertEqual(is_newer_version(remote, current), newer)
        self.assertEqual(windows_numeric_version("2.3.1.T261002"), "2.3.1.0")
        self.assertEqual(windows_numeric_version("2.3.1"), "2.3.1.0")
        with self.assertRaises(ValueError):
            windows_numeric_version("2.3.1.T261002\nInjected=yes")

    def test_release_tag_must_equal_application_version(self):
        prepare_release.ensure_tag_matches_version(__version__)
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            prepare_release.ensure_tag_matches_version(f"v{__version__}")

    def test_release_dataset_must_match_explicit_local_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content_root = root / "Content"
            content_root.mkdir()
            config_path = root / "local.paths.json"
            config_path.write_text(
                json.dumps(
                    {
                        "dataset_id": "configured-v2",
                        "official_content_root": str(content_root),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "请先完成候选晋升"):
                prepare_release.validate_static_dataset_against_local_config(
                    {"dataset": {"dataset_id": "committed-v1"}},
                    config_path,
                )


if __name__ == "__main__":
    unittest.main()
