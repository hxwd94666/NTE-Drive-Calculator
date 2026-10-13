# 验证图纸评分行复用保持匹配结果且不跨候选池和配置泄漏。
import unittest
from unittest.mock import patch

import numpy as np

from src.models.equipment import Drive
from src.optimizer.role_priority_strategy import RolePriorityStrategy


def _strategy():
    return RolePriorityStrategy(
        {"A": {"default_set": "Set"}},
        {"Set": {"shapes": ["X"]}},
        {},
    )


def _drive(uid, shape, score, **sub_stats):
    return Drive(
        uid=uid, quality="Gold", area=1, shape_id=shape, set_name="Set",
        main_stats={"a": 1, "b": 1}, sub_stats=sub_stats, role_scores={"A": score},
    )


class AllocationMatrixReuseTests(unittest.TestCase):
    def test_repeated_combos_reuse_scores_and_keep_distinct_uid_columns(self):
        strategy = _strategy()
        drives = [_drive("a", "X", 10), _drive("b", "X", 8), _drive("c", "Y", 7)]
        blueprint = {"set_pieces": ["X"], "extra_pieces": ["X", "Y"]}
        prepared = strategy._prepare_profit_matrix(drives, {})
        with patch.object(strategy, "_rank_score_for_drive", wraps=strategy._rank_score_for_drive) as rank:
            _, first_profit, first_ranking = prepared.build([blueprint], ["A"], {"A": "Set"})
            # Matching callers may mutate returned arrays without corrupting reusable rows.
            first_profit[0, 0] = -999
            first_ranking[0, 0] = -999
            _, profit, ranking = prepared.build([blueprint], ["A"], {"A": "Set"})
        np.testing.assert_array_equal(profit, [[10, 8, -10000], [10, 8, -10000], [-10000, -10000, 7]])
        np.testing.assert_array_equal(ranking, profit)
        assert rank.call_count == 3

    def test_bonus_rows_do_not_leak_into_extra_or_four_piece_slots(self):
        strategy = _strategy()
        drives = [_drive("a", "X", 10), _drive("b", "X", 8)]
        prepared = strategy._prepare_profit_matrix(drives, {})
        two_piece = {"set_pieces": ["X"], "extra_pieces": ["X"], "set_effect_mode": "two_piece"}
        four_piece = {**two_piece, "set_effect_mode": "four_piece"}
        with patch.object(strategy, "_extra_shape_hidden_bonus", return_value=3):
            _, profit, ranking = prepared.build([two_piece], ["A"], {"A": "Set"})
            np.testing.assert_array_equal(profit, [[10, 8], [10, 8]])
            np.testing.assert_array_equal(ranking, [[13, 11], [10, 8]])
            _, _, plain = prepared.build([four_piece], ["A"], {"A": "Set"})
            np.testing.assert_array_equal(plain, profit)
            _, _, disabled = prepared.build([two_piece], ["A"], {"A": "Set"}, include_extra_shape_bonus=False)
            np.testing.assert_array_equal(disabled, profit)

    def test_new_candidate_pool_and_blacklist_get_fresh_rows(self):
        strategy = _strategy()
        a, b = _drive("a", "X", 10, blocked=1), _drive("b", "X", 8)
        blueprint = {"set_pieces": [], "extra_pieces": ["X"]}
        first = strategy._prepare_profit_matrix([a, b], {})
        _, profit, _ = first.build([blueprint], ["A"], {"A": "Set"})
        np.testing.assert_array_equal(profit, [[10, 8]])
        second = strategy._prepare_profit_matrix([b, a], {"A": {"blacklist": ["blocked"]}})
        _, profit, _ = second.build([blueprint], ["A"], {"A": "Set"})
        np.testing.assert_array_equal(profit, [[8, -10000]])
        third = strategy._prepare_profit_matrix([b], {})
        _, profit, _ = third.build([blueprint], ["A"], {"A": "Set"})
        np.testing.assert_array_equal(profit, [[8]])

    def test_blueprint_ranking_builds_shape_buckets_once_per_bonus_mode(self):
        strategy = _strategy()
        drives = [_drive("a", "X", 10), _drive("b", "X", 8), _drive("c", "Y", 7)]
        blueprints = [
            {"set_pieces": ["X"], "extra_pieces": ["Y"]},
            {"set_pieces": ["X"], "extra_pieces": ["X"]},
            {"set_pieces": ["X"], "extra_pieces": ["Y"], "set_effect_mode": "two_piece"},
        ]
        with patch.object(strategy, "_extra_shape_hidden_bonus", return_value=3):
            expected = sorted(
                [(strategy._blueprint_theoretical_score("A", bp, drives, {"A": "Set"}), index, bp)
                 for index, bp in enumerate(blueprints)],
                key=lambda item: (-item[0], item[1]),
            )
            with patch.object(strategy, "_shape_score_buckets", wraps=strategy._shape_score_buckets) as buckets:
                actual = strategy._rank_role_blueprints([blueprints], ["A"], drives, {"A": "Set"})
        assert actual == [expected]
        assert buckets.call_count == 2


def test_cold_dispatch_import_does_not_load_scipy():
    import importlib.util
    import json
    import subprocess
    import sys

    names = ("reservation_matching", "drive_candidate_ranker", "role_priority_group_strategy", "role_priority_strategy")
    sources = {"src.optimizer." + name: importlib.util.find_spec("src.optimizer." + name).origin for name in names}
    code = r"""
import sys, json, importlib.util, subprocess
sys.path[:] = json.loads(sys.argv[2])
class RejectHeavyImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] == 'scipy':
            raise AssertionError('cold optimizer import reached SciPy: ' + fullname)
class RejectPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        raise AssertionError('cold optimizer import started a process')
sys.meta_path.insert(0, RejectHeavyImport())
subprocess.Popen = RejectPopen
for name, source in json.loads(sys.argv[1]).items():
    __import__(name.rpartition('.')[0])
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    setattr(sys.modules[name.rpartition('.')[0]], name.rpartition('.')[2], module)
from src.optimizer.dispatcher import DispatcherEngine
assert 'scipy' not in sys.modules
assert DispatcherEngine
print('COLD_SOLVER_OK: formal dispatch import without SciPy or external process')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-X", "utf8", "-c", code, json.dumps(sources), json.dumps(sys.path)],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_dispatch_matches_frozen_uids_and_stops_after_dependency_load(monkeypatch):
    import builtins
    from concurrent.futures import CancelledError
    import pytest
    from src.models.equipment import Tape
    from src.optimizer.dispatcher import DispatcherEngine

    roles = {role: {"default_set": "Set"} for role in ("A", "B")}
    blueprints = {role: [{"set_pieces": [], "extra_pieces": ["X"]}] for role in roles}
    engine = DispatcherEngine(roles, {"Set": {"shapes": []}}, blueprints)
    drives = [
        Drive(uid=uid, quality="Gold", area=1, shape_id="X", set_name="Set",
              main_stats={"a": 1, "b": 1}, role_scores={"A": a, "B": b})
        for uid, a, b in (("a", 10, 9), ("b", 1, 8))
    ]
    tapes = [
        Tape(uid=uid, quality="Gold", area=15, set_name="Set", main_stats="攻击力%",
             role_scores={"A": a, "B": b})
        for uid, a, b in (("ta", 5, 1), ("tb", 1, 4))
    ]
    pool = {"drives": drives, "tapes": {role: tapes for role in roles}}
    def execute(candidate_pool):
        return engine.execute_dispatch("role_priority", candidate_pool, list(roles),
                                       {role: "Set" for role in roles}, priority_groups=[list(roles)])
    result = execute(pool)
    assert [result[role]["score"] for role in roles] == [15, 12]
    used = [item.uid for plan in result.values() for item in
            [plan["assigned_tape"], *plan["assigned_extra_drives"]]]
    assert len(used) == len(set(used)) == 4
    partial = execute({**pool, "drives": drives[:1]})
    assert sum(bool(plan["valid"]) for plan in partial.values()) == 1
    assert [drive.uid for plan in partial.values() for drive in plan["assigned_extra_drives"]] == ["a"]

    cancelled = False
    imports = []
    original_import = builtins.__import__
    def observed_import(name, *args, **kwargs):
        nonlocal cancelled
        value = original_import(name, *args, **kwargs)
        if name == "scipy.optimize":
            imports.append(name)
            cancelled = True
        return value
    engine.strategies["role_priority"].configure_execution(combo_limit=2000, cancel_check=lambda: cancelled)
    monkeypatch.setattr(builtins, "__import__", observed_import)
    with pytest.raises(CancelledError):
        execute(pool)
    assert imports == ["scipy.optimize"]
    assert [drive.uid for drive in pool["drives"]] == ["a", "b"]
    assert [tape.uid for tape in tapes] == ["ta", "tb"]
