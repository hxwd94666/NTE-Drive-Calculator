# 核对仓库实际 Rust 图纸与原合法候选等价，缓存和取消不重新运行 Python 搜索。
from collections import Counter
from concurrent.futures import CancelledError
from pathlib import Path
from unittest.mock import patch

import pytest

from src.integrations.allocation.native_blueprints import create_bundled_blueprint_executor
from src.solver.orchestrator import NTEPipelineOrchestrator

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[2]


def signatures(plans):
    return {
        name: sorted(
            (tuple(sorted(row["set_pieces"])), tuple(sorted(row["extra_pieces"])), row["set_effect_mode"])
            for row in rows
        )
        for name, rows in plans.items()
    }


def test_rust_all_role_candidates_equal_reference_and_cached_results_are_detached():
    static = ROOT / "data/game_static.sqlite3"
    reference = NTEPipelineOrchestrator(ROOT / "config", static_database_path=static)
    native = NTEPipelineOrchestrator(
        ROOT / "config", static_database_path=static, blueprint_executor=create_bundled_blueprint_executor(static)
    )
    roles = list(reference.roles_db)
    expected = reference.solve_blueprints(roles)
    with patch("src.solver.orchestrator.DFSPuzzleSolver.solve", side_effect=AssertionError("Python DFS")):
        actual = native.solve_blueprints(roles)
        assert signatures(actual) == signatures(expected)
        assert sum(map(len, actual.values())) > 0
        for name in roles:
            if actual[name]:
                actual[name][0]["extra_pieces"].append("invalid")
                break
        cached = native.solve_blueprints(roles)
        assert signatures(cached) == signatures(expected)
        with pytest.raises(CancelledError):
            native.solve_blueprints(roles, cancel_check=lambda: True)


@pytest.mark.parametrize("mode", ["two_piece", "none"])
def test_native_keeps_set_modes_and_shape_multiplicity(mode):
    static = ROOT / "data/game_static.sqlite3"
    reference = NTEPipelineOrchestrator(ROOT / "config", static_database_path=static)
    role = next(iter(reference.roles_db))
    expected = reference.solve_blueprints([role], set_effect_modes={role: mode})
    native = NTEPipelineOrchestrator(
        ROOT / "config", static_database_path=static, blueprint_executor=create_bundled_blueprint_executor(static)
    )
    actual = native.solve_blueprints([role], set_effect_modes={role: mode})
    assert signatures(actual) == signatures(expected)
    for row in actual[role]:
        assert row["set_effect_mode"] == mode
        assert sum(Counter(row["set_pieces"]).values()) <= 2
