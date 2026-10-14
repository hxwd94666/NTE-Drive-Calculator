# 通过 AST 固定 services、domain 与 UI/feature 的依赖方向。
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


class DependencyBoundaryTests(unittest.TestCase):
    def assert_no_import_prefixes(
        self,
        directory: str,
        forbidden_prefixes: tuple[str, ...],
    ) -> None:
        violations = []
        for path in sorted((ROOT / directory).rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith(forbidden_prefixes):
                    violations.append(
                        f"{path.relative_to(ROOT).as_posix()}: {module}"
                    )
        self.assertEqual([], violations)

    def test_lower_application_layers_do_not_import_features(self):
        for directory in (
            "src/app",
            "src/scanner",
            "src/services",
            "src/integrations",
        ):
            with self.subTest(directory=directory):
                self.assert_no_import_prefixes(directory, ("src.features",))

    def test_domain_does_not_import_features_ui_or_sqlite(self):
        self.assert_no_import_prefixes(
            "src/domain",
            ("src.features", "src.ui", "src.storage.sqlite"),
        )

    def test_observability_does_not_import_feature_service_dao_or_ui(self):
        self.assert_no_import_prefixes(
            "src/observability",
            (
                "src.features",
                "src.services",
                "src.storage",
                "src.ui",
            ),
        )

if __name__ == "__main__":
    unittest.main()
