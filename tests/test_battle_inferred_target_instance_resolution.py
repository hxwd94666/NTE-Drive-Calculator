"""已知轨外环境从旧战报派生逐目标公式画像的公共行为测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from src.domain.battle_report import BattleTargetCondition
from src.services.battle_inferred_target_condition_service import (
    BattleInferredTargetConditionService,
)
from src.services.battle_inferred_target_resolution_support import (
    project_resolved_target_evidence,
    resolve_available_target_instances,
)


STATIC_DATABASE = Path("data/game_static.sqlite3")


def _hit(
    target_id: str,
    hp: float,
    *,
    half: str,
    time_us: int,
    context: object = ("formal-wire",),
    hp_before: float | None = None,
    hp_after: float | None = None,
    damage: float = 100.0,
) -> dict:
    before = hp if hp_before is None else hp_before
    after = before - damage if hp_after is None else hp_after
    return {
        "direction": "outgoing",
        "abyss_half": half,
        "target_id": target_id,
        "target_name": "",
        "target_monster_id": "",
        "target_context": context,
        "target_max_hp": hp,
        "target_hp_before": before,
        "target_hp_after": after,
        "damage": damage,
        "total_damage": damage,
        "relative_time_us": time_us,
    }


def _infer(floor: int, hits: list[dict]):
    return BattleInferredTargetConditionService.infer(
        static_database_path=STATIC_DATABASE,
        combat_context_kind="abyss",
        floor=floor,
        evidence={"hits": hits},
        range_start_us=0,
        range_end_us=100,
    )


class BattleInferredTargetInstanceResolutionTests(unittest.TestCase):
    def test_legacy_saved_single_target_lazily_derives_profile_and_name(self) -> None:
        condition = BattleTargetCondition(
            target_name="争锋赏宴·愿望成真",
            enemy_level=90.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("chaos", 0.2),),
            enemy_defense_base=1050.0,
            environment_kind="feast",
            environment_ref="DiyBossStage8",
            selected_target_ids=("boss_05",),
            primary_target_id="boss_05",
        )
        hits = [_hit(
            "runtime-target",
            5_419_605.0,
            half="",
            time_us=18,
        )]

        resolutions, required = resolve_available_target_instances(
            {"hits": hits},
            condition,
            None,
            static_database_path=STATIC_DATABASE,
        )
        self.assertTrue(required)
        self.assertEqual(1, len(resolutions))
        self.assertEqual("boss_05", resolutions[0].resolved_monster_id)
        assert resolutions[0].target_condition is not None
        self.assertEqual(1050.0, resolutions[0].target_condition.enemy_defense_base)

        project_resolved_target_evidence({"hits": hits}, resolutions)
        self.assertEqual("墨菲克斯", hits[0]["target_name"])
        self.assertEqual("", hits[0]["target_monster_id"])

    def test_known_outer_half_builds_per_instance_replay_profiles(self) -> None:
        hits = [
            _hit("boss", 2_628_918.0, half="upper", time_us=1),
            _hit("add-a", 808_898.0, half="upper", time_us=2),
            _hit("add-b", 808_898.0, half="upper", time_us=3),
        ]
        inferred = _infer(10, hits)

        assert inferred is not None
        resolutions, required = resolve_available_target_instances(
            {"hits": hits},
            None,
            inferred,
        )

        self.assertTrue(required)
        self.assertEqual({"boss", "add-a", "add-b"}, {
            row.captured_target_id for row in resolutions
        })
        self.assertTrue(all(row.target_condition is not None for row in resolutions))
        by_id = {row.captured_target_id: row for row in resolutions}
        self.assertNotEqual(
            by_id["boss"].default_monster_id,
            by_id["add-a"].default_monster_id,
        )
        self.assertEqual(
            by_id["add-a"].default_monster_id,
            by_id["add-b"].default_monster_id,
        )

        BattleInferredTargetConditionService.project_evidence(
            {"hits": hits},
            inferred,
        )
        self.assertTrue(all(hit["target_name"] for hit in hits))
        self.assertTrue(all(hit["target_monster_id"] == "" for hit in hits))

    def test_contextless_duplicate_transition_is_an_alias_not_an_extra_monster(self) -> None:
        hits = [
            _hit(
                "boss",
                2_455_570.0,
                half="upper",
                time_us=10,
                hp_before=2_424_338.0,
                hp_after=2_408_493.0,
                damage=15_845.0,
            ),
            _hit(
                "boss-duplicate",
                2_455_570.0,
                half="upper",
                time_us=12,
                context=(),
                hp_before=2_424_338.0,
                hp_after=2_408_493.0,
                damage=15_845.0,
            ),
            _hit("add-a", 398_200.0, half="upper", time_us=20),
            _hit("add-b", 398_200.0, half="upper", time_us=30),
        ]
        inferred = _infer(9, hits)

        assert inferred is not None
        self.assertEqual(3, len(inferred.identities))
        resolutions, required = resolve_available_target_instances(
            {"hits": hits},
            None,
            inferred,
        )
        self.assertTrue(required)
        by_id = {row.captured_target_id: row for row in resolutions}
        self.assertEqual({"boss", "boss-duplicate", "add-a", "add-b"}, set(by_id))
        self.assertEqual(
            by_id["boss"].default_monster_id,
            by_id["boss-duplicate"].default_monster_id,
        )
        self.assertEqual(
            by_id["boss"].target_condition,
            by_id["boss-duplicate"].target_condition,
        )

        BattleInferredTargetConditionService.project_evidence(
            {"hits": hits},
            inferred,
        )
        self.assertEqual(hits[0]["target_name"], hits[1]["target_name"])


if __name__ == "__main__":
    unittest.main()
