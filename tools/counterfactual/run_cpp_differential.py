# 对比原生逐击输出与已提交的 Python 权威夹具。
"""Compare native per-hit output with the committed Python oracle fixture."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "native" / "counterfactual-core" / "tests" / "fixtures"
REQUEST_PATH = FIXTURE_DIR / "ordinary-buffs.request.json"
ORACLE_PATH = FIXTURE_DIR / "ordinary-buffs.oracle.json"
FLOAT_FIELDS = {
    "basis_damage",
    "candidate_damage",
    "fully_quantified_damage",
    "partially_quantified_damage",
    "proven_unchanged_damage",
    "quantified_increment",
    "quantified_ratio",
    "unavailable_damage",
}


def _compare(expected: Any, actual: Any, path: str = "$") -> list[str]:
    failures: list[str] = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected object, got {type(actual).__name__}"]
        if set(expected) != set(actual):
            failures.append(
                f"{path}: keys differ expected={sorted(expected)} actual={sorted(actual)}"
            )
        for key in expected.keys() & actual.keys():
            failures.extend(_compare(expected[key], actual[key], f"{path}.{key}"))
        return failures
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return [f"{path}: list shape differs"]
        for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            failures.extend(_compare(left, right, f"{path}[{index}]"))
        return failures
    field = path.rsplit(".", 1)[-1]
    if field in FLOAT_FIELDS and expected is not None:
        if not isinstance(actual, (int, float)) or not math.isclose(
            float(expected), float(actual), rel_tol=1e-12, abs_tol=1e-9
        ):
            failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    elif expected != actual:
        failures.append(f"{path}: expected {expected!r}, got {actual!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve()
    expected = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="counterfactual-diff-") as directory:
        output_path = Path(directory) / "native-response.json"
        completed = subprocess.run(
            [str(executable), str(REQUEST_PATH), str(output_path)],
            cwd=ROOT,
            check=False,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            print(completed.stdout)
            print(completed.stderr)
            return completed.returncode or 1
        actual = json.loads(output_path.read_text(encoding="utf-8"))
    failures = _compare(expected, actual)
    if failures:
        print("Python/C++ differential failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    hit_count = sum(len(row["hits"]) for row in actual["results"])
    print(
        f"Python/C++ differential passed: {len(actual['results'])} Buff groups, "
        f"{hit_count} per-hit comparisons"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
