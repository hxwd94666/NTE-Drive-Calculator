# 验证源码编码、导入和拆分结构约束。
import unittest
import ast
import builtins
import importlib
import json
import symtable
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.encoding_guard import find_text_encoding_issues
from tools.import_guard import find_import_issues


LEGACY_SHIM_PATHS = [
    "src/ui/account_manager.py",
    "src/ui/config_page.py",
    "src/ui/execute_page.py",
    "src/ui/hotkey_manager.py",
    "src/ui/identify_page.py",
    "src/ui/role_selector.py",
    "src/ui/settings_page.py",
    "src/ui/update_checker.py",
    "src/ui/vision_worker.py",
    "src/scanner/duplicate_filter.py",
    "src/scanner/equipment_classifier.py",
    "src/scanner/identify_parser.py",
    "src/scanner/inventory_exporter.py",
    "src/scanner/scan_file_lifecycle.py",
    "src/scanner/screenshot_parser.py",
]
FEATURE_STATIC_ALLOWLIST = {
    "src/features/configuration/page.py": {"NoWheelComboBox", "NoWheelDoubleSpinBox"},
    "src/integrations/nte_core.py": {"__file__"},
    "src/storage/sqlite/static_game_data_dao.py": {"__file__"},
    "src/utils/path_helper.py": {"__file__"},
}


def _repo_python_files() -> list[Path]:
    # 对方提供的本地核心与插件目录不是本项目源码，不适用项目的中文摘要规范。
    excluded_parts = {".venv", "__pycache__", "build", "dist", "nte-core", "nte-equipment-plugin"}
    return sorted(
        path for path in ROOT.rglob("*.py")
        if path.is_file()
        and not excluded_parts.intersection(path.relative_to(ROOT).parts)
    )


class EncodingGuardTests(unittest.TestCase):
    def test_python_code_files_start_with_chinese_summary_comment(self):
        missing = []
        for path in _repo_python_files():
            lines = path.read_text(encoding="utf-8").splitlines()
            first_line = lines[0] if lines else ""
            if not first_line.startswith("# ") or not any("\u4e00" <= ch <= "\u9fff" for ch in first_line):
                missing.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], missing)

    def test_python_sources_are_valid_utf8_without_mojibake(self):
        issues = find_text_encoding_issues(["src", "main.py", "build_exe.py", "build_installer.py"])
        self.assertEqual([], issues)

    def test_config_json_files_are_valid(self):
        failures = []
        for path in sorted((ROOT / "config").glob("*.json")):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                failures.append(f"{path.relative_to(ROOT).as_posix()}: {exc}")
        self.assertEqual([], failures)

    def test_question_mark_mojibake_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.py"
            source.write_text('MESSAGE = "????????"\n', encoding="utf-8")

            issues = find_text_encoding_issues([source])

        self.assertTrue(
            any("question-mark mojibake" in issue for issue in issues),
            issues,
        )

    def test_split_modules_are_importable(self):
        modules = [
            "src.features.accounts.manager",
            "src.features.allocation.execute_page",
            "src.features.allocation.runner",
            "src.features.allocation.results_view",
            "src.features.allocation.role_selector",
            "src.features.blueprints.page",
            "src.features.configuration.page",
            "src.features.identification.controller",
            "src.features.identification.dialogs",
            "src.features.identification.page",
            "src.features.identification.parser",
            "src.features.inventory.page",
            "src.features.inventory_import.duplicate_filter",
            "src.features.inventory_import.equipment_classifier",
            "src.features.inventory_import.exporter",
            "src.features.inventory_import.screenshot_parser",
            "src.features.onboarding.guide",
            "src.features.scanning.controller",
            "src.features.scanning.file_lifecycle",
            "src.features.scanning.vision_worker",
            "src.features.settings.page",
            "src.app.constants",
            "src.app.dialogs",
            "src.app.facade",
            "src.app.runtime",
            "src.app.theme",
            "src.app.workers",
            "src.ui.main_window_method_install",
            "src.ui.plain_text_edit",
            "src.ui.puzzle_board",
        ]
        self.assertEqual([], find_import_issues(modules))

    def test_all_src_modules_are_importable(self):
        failures = []
        for path in sorted(Path("src").rglob("*.py")):
            if path.name == "__init__.py":
                continue
            module = ".".join(path.with_suffix("").parts)
            try:
                importlib.import_module(module)
            except Exception as exc:
                failures.append(f"{module}: {type(exc).__name__}: {exc}")
        self.assertEqual([], failures)

    def test_theme_preference_defaults_to_original_theme(self):
        from src.app.theme import THEME_LABELS, theme_preference

        self.assertEqual("dark", theme_preference(None))
        self.assertEqual("dark", theme_preference(""))
        self.assertEqual("dark", theme_preference("unknown"))
        self.assertEqual("dark", theme_preference("dark"))
        self.assertEqual("black", theme_preference("black"))
        self.assertEqual("light", theme_preference("light"))
        self.assertEqual("原主题", THEME_LABELS["dark"])
        self.assertEqual("黑色主题", THEME_LABELS["black"])
        self.assertEqual("白色主题", THEME_LABELS["light"])

    def test_bilibili_homepage_link_is_configured(self):
        from src.app.constants import BILIBILI_HOME_URL

        self.assertEqual("https://b23.tv/nXJGdh3", BILIBILI_HOME_URL)

    def test_no_invalid_path_module_imports(self):
        offenders = []
        for path in sorted(Path("src").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "Path":
                            offenders.append(f"{path}:{node.lineno}")
        self.assertEqual([], offenders)

    def test_legacy_shim_files_are_removed(self):
        existing = [path for path in LEGACY_SHIM_PATHS if Path(path).exists()]
        self.assertEqual([], existing)

    def test_main_window_has_no_duplicate_method_names(self):
        tree = ast.parse(Path("src/ui/app.py").read_text(encoding="utf-8"))
        main_window = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
        )
        names = [
            node.name for node in main_window.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        self.assertEqual([], duplicates)

    def test_app_py_remains_main_window_shell(self):
        line_count = len(Path("src/ui/app.py").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(line_count, 1200)

    def test_feature_modules_are_statically_self_contained(self):
        issues: list[str] = []
        for path in sorted(Path("src/features").rglob("*.py")):
            if path.name == "__init__.py":
                continue
            missing = _missing_names_for_path(path)
            if missing:
                issues.append(f"{path}: {', '.join(missing)}")
        self.assertEqual([], issues)

    def test_feature_modules_do_not_use_runtime_global_injection(self):
        offenders = []
        for path in sorted(Path("src/features").rglob("*.py")):
            if "globals().update" in path.read_text(encoding="utf-8"):
                offenders.append(str(path))
        self.assertEqual([], offenders)

    def test_src_modules_have_no_unresolved_global_names(self):
        issues: list[str] = []
        for path in sorted(Path("src").rglob("*.py")):
            if path.name == "__init__.py":
                continue
            missing = _missing_names_for_path(path)
            if missing:
                issues.append(f"{path}: {', '.join(missing)}")
        self.assertEqual([], issues)

    def test_type_annotation_names_are_resolvable(self):
        issues: list[str] = []
        builtins_names = set(dir(builtins))
        for path in sorted(Path("src").rglob("*.py")):
            if path.name == "__init__.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_names = _module_defined_names(tree)
            allowed = builtins_names | FEATURE_STATIC_ALLOWLIST.get(path.as_posix(), set())
            missing = sorted(_annotation_names(tree) - module_names - allowed)
            if missing:
                issues.append(f"{path}: {', '.join(missing)}")
        self.assertEqual([], issues)


def _missing_names_for_path(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    table = symtable.symtable(text, str(path), "exec")
    module_names = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned() or symbol.is_imported()
    }
    allowlist = FEATURE_STATIC_ALLOWLIST.get(path.as_posix(), set())
    return sorted(_missing_global_names(table, module_names, set(dir(builtins)) | allowlist))


def _module_defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _annotation_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        annotation = None
        if isinstance(node, ast.arg):
            annotation = node.annotation
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotation = node.returns
        elif isinstance(node, ast.AnnAssign):
            annotation = node.annotation
        if annotation is not None:
            names.update(_names_in_annotation(annotation))
    return names


def _names_in_annotation(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
    return names


def _missing_global_names(table, module_names: set[str], allowed: set[str]) -> set[str]:
    missing: set[str] = set()
    for child in table.get_children():
        for symbol in child.get_symbols():
            name = symbol.get_name()
            if (
                symbol.is_global()
                and symbol.is_referenced()
                and name not in module_names
                and name not in allowed
            ):
                missing.add(name)
        missing.update(_missing_global_names(child, module_names, allowed))
    return missing


if __name__ == "__main__":
    unittest.main()
