# 验证轨外期数优先按冻结的战报发生时间选择，而不是依赖构建时目录顺序。
from __future__ import annotations

import unittest

from src.domain.battle_encounter import BattleEncounterCandidate
from src.services.battle_outer_realm_period_service import (
    filter_candidates_for_period,
    resolve_outer_realm_period,
)


def _candidate(config_id: str) -> BattleEncounterCandidate:
    return BattleEncounterCandidate(
        environment_kind="outer_realm",
        environment_ref=f"{config_id}|10|EAbyssFightStage::FirstHalf",
        environment_name=config_id,
        scope_half="upper",
        outer_realm_floor=10,
        difficulty_id=None,
        feast_options=(),
        targets=(),
    )


class BattleOuterRealmPeriodServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.configs = (
            {
                "level_config_id": "Abyss_8",
                "starts_at_mainland": "2026-08-21T05:00:00",
                "ends_at_mainland": "2026-09-04T04:59:59",
                "season_buff": {"season_name_zh": "烛天环线"},
            },
            {
                "level_config_id": "Abyss_9",
                "starts_at_mainland": "2026-09-04T05:00:00",
                "ends_at_mainland": "2026-09-18T04:59:59",
                "season_buff": {"season_name_zh": "幽语环线"},
            },
        )

    def test_utc_battle_time_selects_mainland_period_after_rotation(self) -> None:
        period = resolve_outer_realm_period(
            self.configs,
            "2026-09-04T08:16:46.743+00:00",
        )

        assert period is not None
        self.assertEqual("Abyss_9", period.config_id)
        self.assertEqual("幽语环线（09-04—09-18）", period.display_label)
        self.assertIn("2026-09-04 16:16:46", period.inference_basis)
        self.assertEqual(
            ("Abyss_9|10|EAbyssFightStage::FirstHalf",),
            tuple(
                row.environment_ref
                for row in filter_candidates_for_period(
                    (_candidate("Abyss_8"), _candidate("Abyss_9")),
                    period,
                )
            ),
        )

    def test_missing_or_invalid_time_keeps_existing_candidate_set(self) -> None:
        candidates = (_candidate("Abyss_8"), _candidate("Abyss_9"))

        self.assertIsNone(resolve_outer_realm_period(self.configs, None))
        self.assertIsNone(resolve_outer_realm_period(self.configs, "not-a-time"))
        self.assertEqual(candidates, filter_candidates_for_period(candidates, None))


if __name__ == "__main__":
    unittest.main()
