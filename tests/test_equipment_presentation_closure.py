# 测试公共装备展示组件的方法闭包完整性。
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EQUIPMENT_PRESENTATION = (
    ROOT / "src/ui/equipment_presentation.py"
)
MAIN_WINDOW_MIXINS = ROOT / "src/ui/main_window_mixins.py"


def _presentation_all_and_funcs():
    mod = ast.parse(EQUIPMENT_PRESENTATION.read_text(encoding="utf-8"))
    all_names = set()
    funcs = {}
    for node in mod.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    all_names = set(ast.literal_eval(node.value))
        elif isinstance(node, ast.FunctionDef):
            funcs[node.name] = node
    return all_names, funcs


def _self_method_callees(fn):
    out = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self":
            out.add(func.attr)
    return out


class EquipmentPresentationClosureTests(unittest.TestCase):
    def test_all_self_calls_are_exported(self):
        all_names, funcs = _presentation_all_and_funcs()
        calls = set()
        for fn in funcs.values():
            calls |= _self_method_callees(fn)
        missing = sorted(call for call in calls if call not in all_names)
        self.assertEqual(missing, [], f"self._*() not in __all__: {missing}")

    def test_exported_methods_self_call_closure_stays_in_all(self):
        all_names, funcs = _presentation_all_and_funcs()
        closure = set(all_names)
        changed = True
        while changed:
            changed = False
            for name in list(closure):
                fn = funcs.get(name)
                if fn is None:
                    continue
                for callee in _self_method_callees(fn):
                    if callee.startswith("_") and callee not in closure:
                        closure.add(callee)
                        changed = True
        leaked = sorted(name for name in closure if name not in all_names and name in funcs)
        self.assertEqual(leaked, [], f"closure needs __all__ entries: {leaked}")

    def test_presentation_object_owns_exported_private_behavior(self):
        all_names, _ = _presentation_all_and_funcs()
        source = EQUIPMENT_PRESENTATION.read_text(encoding="utf-8")
        owned_names = set(
            re.findall(r"^\s+(_\w+)\s*=\s*_\w+\s*$", source, re.M)
        )
        self.assertEqual(
            owned_names,
            {name for name in all_names if name.startswith("_")},
        )

    def test_main_window_does_not_reinstall_private_module_functions(self):
        tree = ast.parse(MAIN_WINDOW_MIXINS.read_text(encoding="utf-8"))
        private_aliases = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr.startswith("_")
            ):
                private_aliases.append(node.value.attr)
        self.assertEqual([], private_aliases)


if __name__ == "__main__":
    unittest.main()
