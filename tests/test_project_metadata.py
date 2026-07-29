# 测试版本、工程元数据与本地发布准备工具保持一致。
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app.constants import APP_VERSION
from src.app.version import __version__
from tools.release import prepare_release


ROOT = Path(__file__).resolve().parent.parent


class ProjectMetadataTests(unittest.TestCase):
    def test_runtime_and_release_tool_share_one_version(self):
        self.assertEqual(__version__, APP_VERSION)
        self.assertEqual(
            f"NTE_Drive_Calc_Setup_{__version__}.exe",
            prepare_release.INSTALLER_NAME,
        )

    def test_pyproject_uses_dynamic_version_and_scoped_dependencies(self):
        metadata = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = metadata["project"]

        self.assertEqual(["version"], project["dynamic"])
        self.assertNotIn("version", project)
        self.assertNotIn("pyinstaller", " ".join(project["dependencies"]).lower())
        self.assertIn(
            "pyinstaller",
            " ".join(metadata["dependency-groups"]["build"]).lower(),
        )
        self.assertIn("ruff", " ".join(metadata["dependency-groups"]["dev"]).lower())
        self.assertEqual(
            "src.app.version.__version__",
            metadata["tool"]["setuptools"]["dynamic"]["version"]["attr"],
        )

    def test_release_tag_must_equal_application_version(self):
        prepare_release.ensure_tag_matches_version(__version__)
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            prepare_release.ensure_tag_matches_version(f"v{__version__}")

    def test_component_record_hash_parser_is_label_specific(self):
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "COMPONENT.md"
            record.write_text(
                "- 目标 SHA-256：`" + "A" * 64 + "`\n"
                "- 其他 SHA-256：`" + "B" * 64 + "`\n",
                encoding="utf-8",
            )

            self.assertEqual(
                "A" * 64,
                prepare_release._recorded_hash(record, "目标 SHA-256"),
            )

    def test_manual_commands_do_not_execute_release_actions(self):
        with patch("builtins.print") as mocked_print:
            prepare_release.print_manual_commands(
                __version__,
                prepare_release.INSTALLER_PATH,
                Path("notes.md"),
            )

        output = "\n".join(str(call.args[0]) for call in mocked_print.call_args_list)
        self.assertIn(f"git tag {__version__}", output)
        self.assertIn(f"git push origin {__version__}", output)
        self.assertIn(f"gh release create {__version__}", output)


if __name__ == "__main__":
    unittest.main()
