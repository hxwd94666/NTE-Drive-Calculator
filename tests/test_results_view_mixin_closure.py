import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_VIEW = ROOT / "src/features/allocation/results_view.py"
MAIN_WINDOW_MIXINS = ROOT / "src/ui/main_window_mixins.py"


def _results_view_all_and_funcs():
    mod = ast.parse(RESULTS_VIEW.read_text(encoding="utf-8"))
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


class ResultsViewMixinClosureTests(unittest.TestCase):
    def test_all_self_calls_are_exported(self):
        all_names, funcs = _results_view_all_and_funcs()
        calls = set()
        for fn in funcs.values():
            calls |= _self_method_callees(fn)
        missing = sorted(call for call in calls if call not in all_names)
        self.assertEqual(missing, [], f"self._*() not in __all__: {missing}")

    def test_exported_methods_self_call_closure_stays_in_all(self):
        all_names, funcs = _results_view_all_and_funcs()
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

    def test_mixin_matches_results_view_all(self):
        all_names, _ = _results_view_all_and_funcs()
        mixin_text = MAIN_WINDOW_MIXINS.read_text(encoding="utf-8")
        mixin_names = set(re.findall(r"^\s+(_\w+)\s*=\s*allocation_results_view\.", mixin_text, re.M))
        self.assertEqual(mixin_names, all_names)


if __name__ == "__main__":
    unittest.main()
