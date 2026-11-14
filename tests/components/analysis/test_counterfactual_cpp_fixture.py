# 验证公开 C++ 夹具仍由当前 Python oracle 可复现。
from __future__ import annotations

import json
from pathlib import Path
import unittest

from tools.counterfactual.generate_cpp_oracle_fixture import (
    ORACLE_PATH,
    REQUEST_PATH,
    python_oracle,
)


class CounterfactualCppFixtureTests(unittest.TestCase):
    def test_committed_oracle_matches_current_python_services(self) -> None:
        request = json.loads(Path(REQUEST_PATH).read_text(encoding="utf-8"))
        expected = json.loads(Path(ORACLE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(expected, python_oracle(request))

    def test_fixture_covers_all_public_statuses(self) -> None:
        expected = json.loads(Path(ORACLE_PATH).read_text(encoding="utf-8"))
        statuses = {row["status"] for row in expected["results"]}
        self.assertEqual(
            {"complete", "partial", "unavailable", "not_applicable"},
            statuses,
        )


if __name__ == "__main__":
    unittest.main()
