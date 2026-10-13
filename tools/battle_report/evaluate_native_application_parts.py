# 用既有公共行为测试差分原生应用层，逐字段核对而不改写运行时入口。
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.battle_report.build.counterfactual import BattleBuildCounterfactualService  # noqa: E402
from src.services.battle_report.damage.composition import BattleDamageCompositionService  # noqa: E402
from src.services.battle_report.build.timeline_projection import BattleBuildTimelineProjectionService  # noqa: E402
from tools.battle_report.evaluate_native_analysis import compare_results  # noqa: E402


def wire(value):
    if is_dataclass(value):
        return wire(asdict(value))
    if isinstance(value, dict):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [wire(item) for item in value]
    return value


def numeric_wire(value):
    """JSON has one numeric category; preserve booleans and all missing values."""
    if type(value) in (int, float):
        return value if type(value) is int and abs(value) > 2**53 else float(value)
    if isinstance(value, dict):
        return {key: numeric_wire(item) for key, item in value.items()}
    if isinstance(value, list):
        return [numeric_wire(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = []

    def compare(operation, payload, expected):
        request = {"operation": operation, "input": wire(payload)}
        result = subprocess.run(
            [str(args.executable.resolve())], input=json.dumps(request, ensure_ascii=False).encode(),
            capture_output=True, timeout=60, check=False,
        )
        if result.returncode:
            raise AssertionError(f"native_application_part_failed:{operation}:{result.stderr.decode()[:80]}")
        actual = json.loads(result.stdout)
        comparison = compare_results(numeric_wire(wire(expected)), numeric_wire(actual))
        cases.append({"operation": operation, **comparison})
        if comparison["difference_count"]:
            artifact = args.output.parent / f"application-part-failure-{len(cases)}.json"
            artifact.write_text(json.dumps({"input": request, "expected": wire(expected), "actual": actual},
                                           ensure_ascii=False, indent=2), encoding="utf-8")
            raise AssertionError(f"{operation}: {comparison['difference_count']} fields differ: {comparison['differences'][:3]}")

    original_compare = BattleBuildCounterfactualService.compare
    original_composition = BattleDamageCompositionService.calculate_from_hits
    original_timeline = BattleBuildTimelineProjectionService.project

    def timeline(analysis, counterfactual):
        expected = original_timeline(analysis, counterfactual)
        compare("timeline", {"analysis": analysis, "counterfactual": counterfactual}, expected)
        return expected

    def build_compare(**kwargs):
        expected = original_compare(**kwargs)
        compare("build_compare", {key: kwargs[key] for key in ("original", "candidate")}, expected)
        return expected

    def composition(**kwargs):
        expected = original_composition(**kwargs)
        if kwargs.get("grouping", "coarse") == "coarse":
            identities = {role.character_id: role.character_name for role in kwargs.get("roles", ())}
            identities.update(dict(kwargs.get("role_identities", ())))
            compare("composition", {**kwargs, "identities": [
                {"character_id": key, "character_name": value} for key, value in identities.items()
            ]}, expected)
        return expected

    args.output.parent.mkdir(parents=True, exist_ok=True)
    suite = unittest.defaultTestLoader.loadTestsFromNames([
        "tests.battle_report.build.test_battle_build_counterfactual_service",
        "tests.battle_report.build.test_battle_build_weave_source_ratio",
        "tests.battle_report.damage.test_composition",
        "tests.test_battle_partial_quantification_contract",
    ])
    with patch.object(BattleBuildCounterfactualService, "compare", side_effect=build_compare), \
            patch.object(BattleDamageCompositionService, "calculate_from_hits", side_effect=composition), \
            patch.object(BattleBuildTimelineProjectionService, "project", side_effect=timeline):
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    summary = {"tests": result.testsRun, "successful": result.wasSuccessful(), "cases": cases}
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
