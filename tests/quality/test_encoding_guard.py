# 验证真实源码编码、导入与未解析名称；不固定历史路径、显示常量或主题 getter。
import unittest
import ast
import builtins
import importlib
import json
import symtable
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.encoding_guard import find_text_encoding_issues, iter_python_sources


def _repo_python_files() -> list[Path]:
    # 对方提供的本地核心与插件目录不是本项目源码，不适用项目的中文摘要规范。
    excluded_parts = {
        "local-audits",
        "nte-core", "nte-equipment-plugin", "third_party",
    }
    return sorted(
        path
        for path in iter_python_sources([ROOT])
        if path.is_file()
        and not excluded_parts.intersection(path.relative_to(ROOT).parts)
        and not any(part.startswith("nte-core-windows-x64") for part in path.relative_to(ROOT).parts)
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


    def test_scan_scope_keeps_source_packages_and_explicit_build_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 请求根外的 build 只是临时目录祖先，不是本次源码范围的排除身份。
            root = Path(tmp) / "build" / "project"
            sources = (
                "src/services/battle_report/build/source.py",
                "src/dist/source.py",
                "src/installer/output/source.py",
                "src/builders.py",
                "src/redistributor.py",
                "src/.github/source.py",
            )
            omitted = (
                "build/generated.py", "dist/generated.py", "installer/output/generated.py",
                "src/.git/cached.py", "src/.venv/cached.py", "src/__pycache__/cached.py",
            )
            for name in sources + omitted:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("VALUE = 1\n", encoding="utf-8")
            generated = root / "build/generated.py"
            generated.write_text('MESSAGE = "' + "?" * 8 + '"\n', encoding="utf-8")

            # 项目根身份是合成 fixture；断言正式枚举和检测结果，不固化 helper 布局。
            with patch("tools.encoding_guard.PROJECT_ROOT", root):
                expected = sorted(root / name for name in sources)
                self.assertEqual(expected, iter_python_sources([root]))
                self.assertEqual(expected, iter_python_sources([root / "src"]))
                self.assertEqual([generated], iter_python_sources([generated]))
                self.assertEqual([generated], iter_python_sources([root / "build"]))
                self.assertEqual([], find_text_encoding_issues([root]))
                issues = find_text_encoding_issues([root / "build"])
                self.assertTrue(any("question-mark mojibake" in issue for issue in issues), issues)


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


    def test_main_window_has_no_duplicate_method_names(self):
        tree = ast.parse(Path("src/ui/app.py").read_text(encoding="utf-8"))
        main_window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
        names = [node.name for node in main_window.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        self.assertEqual([], duplicates)


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
            allowed = builtins_names
            missing = sorted(_annotation_names(tree) - module_names - allowed)
            if missing:
                issues.append(f"{path}: {', '.join(missing)}")
        self.assertEqual([], issues)

    def test_annotation_guard_recognizes_type_checking_imports_only_at_module_scope(self):
        tree = ast.parse(
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from example import FrozenInput as Input\n"
            "def helper():\n"
            "    from example import LocalOnly\n"
            "if runtime_condition:\n"
            "    from example import ConditionalOnly\n"
            "def consume(value: Input, missing: Unknown) -> None: pass\n"
        )
        defined = _module_defined_names(tree)
        self.assertIn("Input", defined)
        self.assertNotIn("LocalOnly", defined)
        self.assertNotIn("ConditionalOnly", defined)
        self.assertEqual({"Unknown"}, _annotation_names(tree) - defined)


def _missing_names_for_path(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    table = symtable.symtable(text, str(path), "exec")
    module_names = {symbol.get_name() for symbol in table.get_symbols() if symbol.is_assigned() or symbol.is_imported()}
    return sorted(_missing_global_names(table, module_names, set(dir(builtins)) | {"__file__"}))


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
        elif (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            names.update(_module_defined_names(ast.Module(body=node.body, type_ignores=[])))
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
            if symbol.is_global() and symbol.is_referenced() and name not in module_names and name not in allowed:
                missing.add(name)
        missing.update(_missing_global_names(child, module_names, allowed))
    return missing


if __name__ == "__main__":
    unittest.main()
