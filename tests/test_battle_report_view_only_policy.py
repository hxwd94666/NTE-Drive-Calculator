# 验证旧战报契约可查看但拒绝反事实写入。
"""Verify old battle contracts remain viewable but reject counterfactual writes."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from src.services.battle_build_edit_history_mixin import (
    BattleBuildEditHistoryMixin,
)


class _Dao:
    def __init__(self, editable: bool, build_edit=None) -> None:
        self.editable = editable
        self.build_edit = build_edit

    def battle_report_counterfactual_editable(self, _record_id: int) -> bool:
        return self.editable

    def load_battle_build_edit(self, _record_id: int):
        return self.build_edit


class _History(BattleBuildEditHistoryMixin):
    def __init__(self, editable: bool, build_edit=None) -> None:
        self.dao = _Dao(editable, build_edit)
        self._dependencies = SimpleNamespace(
            user_database_path="user.sqlite3",
            static_database_path="static.sqlite3",
        )

    @contextmanager
    def _open_current_dao(self):
        yield self.dao


class BattleReportViewOnlyPolicyTests(unittest.TestCase):
    def test_old_contract_rejects_counterfactual_mutation(self) -> None:
        with self.assertRaisesRegex(ValueError, "旧版战报仅支持查看"):
            _History(False)._assert_counterfactual_editable(7)

    def test_v4_contract_allows_counterfactual_mutation_boundary(self) -> None:
        _History(True)._assert_counterfactual_editable(7)

    def test_old_null_fork_stage_is_resolved_only_at_role_page_sync(self) -> None:
        history = _History(True, {
            "characters": [{
                "character_id": 1004,
                "character_level": 70,
                "breakthrough_stage": 5,
                "awakening_level": 0,
                "likeability_level_10_enabled": False,
                "fork_id": "fork_Rose",
                "fork_level": 70,
                "fork_breakthrough_stage": None,
                "fork_refinement_level": 1,
                "selected_skill_id": None,
                "ordinal": 0,
                "profile": {
                    "selected_awaken_effect_ids": [],
                    "skill_levels": {"melee": 8},
                },
            }],
        })
        saved_updates = []

        class FakeProfileService:
            def __init__(self, _path) -> None:
                pass

            def save_profiles(self, updates) -> int:
                saved_updates.extend(updates)
                return len(updates)

        with patch(
            "src.services.battle_build_edit_history_mixin."
            "load_official_role_detail",
            return_value={
                "profile": {"ordinal": 2},
                "forks": [{
                    "fork_id": "fork_Rose",
                    "breakthroughs": [
                        {"stage": 5, "max_fork_level": 70},
                        {"stage": 6, "max_fork_level": 80},
                    ],
                }],
            },
        ), patch(
            "src.services.battle_build_edit_history_mixin."
            "OfficialRoleProfileService",
            FakeProfileService,
        ):
            self.assertEqual(1, history.sync_build_edit_to_role_page(7))

        self.assertEqual(5, saved_updates[0].fork_breakthrough_stage)
        self.assertIsNone(
            history.dao.build_edit["characters"][0]["fork_breakthrough_stage"]
        )


if __name__ == "__main__":
    unittest.main()
