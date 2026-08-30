# 通过 AST 固定 services、domain 与 UI/feature 的依赖方向。
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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

    def test_services_do_not_import_features(self):
        self.assert_no_import_prefixes("src/services", ("src.features",))

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

    def test_role_weight_and_blueprint_slices_do_not_read_runtime_paths(self):
        relative_paths = (
            "src/features/official_role/page.py",
            "src/features/official_role/role_shell.py",
            "src/features/configuration/page.py",
            "src/features/blueprints/page.py",
        )
        violations = []
        for relative_path in relative_paths:
            source = (ROOT / relative_path).read_text(encoding="utf-8")
            if (
                "from src.app import runtime" in source
                or "src.app.runtime" in source
            ):
                violations.append(relative_path)
        self.assertEqual([], violations)

    def test_official_role_slice_has_no_dynamic_global_exports(self):
        violations = []
        for path in sorted((ROOT / "src/features/official_role").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "globals":
                    violations.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], violations)

    def test_catalog_link_exports_share_domain_identity(self):
        from src.domain.static_catalog import CatalogLink as DomainCatalogLink
        from src.features.static_catalog.contracts import (
            CatalogLink as FeatureCatalogLink,
        )
        from src.services.static_catalog_mechanics_models import (
            CatalogLink as MechanicsCatalogLink,
        )
        from src.services.static_catalog_mechanics_service import (
            CatalogLink as PublicMechanicsCatalogLink,
        )

        self.assertIs(DomainCatalogLink, FeatureCatalogLink)
        self.assertIs(DomainCatalogLink, MechanicsCatalogLink)
        self.assertIs(DomainCatalogLink, PublicMechanicsCatalogLink)

    def test_catalog_pages_do_not_import_catalog_link_from_mechanics_service(self):
        violations = []
        directory = ROOT / "src" / "features" / "static_catalog" / "domain_pages"
        for path in sorted(directory.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if (
                "CatalogLink" in source
                and "static_catalog_mechanics_service import CatalogLink" in source
            ):
                violations.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([], violations)


if __name__ == "__main__":
    unittest.main()
