"""Verify old battle contracts remain viewable but reject counterfactual writes."""

from __future__ import annotations

import unittest
from contextlib import contextmanager

from src.services.battle_build_edit_history_mixin import (
    BattleBuildEditHistoryMixin,
)


class _Dao:
    def __init__(self, editable: bool) -> None:
        self.editable = editable

    def battle_report_counterfactual_editable(self, _record_id: int) -> bool:
        return self.editable


class _History(BattleBuildEditHistoryMixin):
    def __init__(self, editable: bool) -> None:
        self.dao = _Dao(editable)

    @contextmanager
    def _open_current_dao(self):
        yield self.dao


class BattleReportViewOnlyPolicyTests(unittest.TestCase):
    def test_old_contract_rejects_counterfactual_mutation(self) -> None:
        with self.assertRaisesRegex(ValueError, "旧版战报仅支持查看"):
            _History(False)._assert_counterfactual_editable(7)

    def test_v4_contract_allows_counterfactual_mutation_boundary(self) -> None:
        _History(True)._assert_counterfactual_editable(7)


if __name__ == "__main__":
    unittest.main()
