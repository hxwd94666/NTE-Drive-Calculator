# 强制已迁移架构边界的公开函数具备完整类型注解。
from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TYPED_BOUNDARY_FILES = (
    "src/app/context.py",
    "src/domain/allocation_rating.py",
    "src/domain/drive_layout.py",
    "src/domain/equipment_duplicate_marker.py",
    "src/domain/post_actions.py",
    "src/features/identification/dependencies.py",
    "src/features/blueprints/controller.py",
    "src/features/blueprints/dependencies.py",
    "src/features/configuration/controller.py",
    "src/features/configuration/dependencies.py",
    "src/features/official_role/controller.py",
    "src/features/official_role/dependencies.py",
    "src/features/scanning/dependencies.py",
    "src/features/weighted_allocation/dependencies.py",
    "src/observability/context.py",
    "src/observability/events.py",
    "src/observability/operation.py",
    "src/observability/redaction.py",
    "src/services/bulk_equipment_apply_service.py",
    "src/services/blueprint_service.py",
    "src/services/official_role_profile_service.py",
    "src/storage/sqlite/shared_data_dao.py",
)


class TypeAnnotationBoundaryTests(unittest.TestCase):
    def test_public_boundary_functions_have_complete_annotations(self):
        failures: list[str] = []
        for relative_path in TYPED_BOUNDARY_FILES:
            source_path = ROOT / relative_path
            tree = ast.parse(
                source_path.read_text(encoding="utf-8"),
                filename=str(source_path),
            )
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if node.name.startswith("_"):
                    continue
                arguments = (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
                missing = [
                    argument.arg
                    for argument in arguments
                    if argument.arg not in {"self", "cls"} and argument.annotation is None
                ]
                if missing:
                    failures.append(f"{relative_path}:{node.lineno} {node.name} 缺少参数注解：{', '.join(missing)}")
                if node.returns is None:
                    failures.append(f"{relative_path}:{node.lineno} {node.name} 缺少返回注解")

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
