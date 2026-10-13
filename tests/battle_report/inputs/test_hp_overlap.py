# 验证单敌人血量区间重叠只形成诊断，不改写正式逐击。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_report.inputs.hp_overlap import (
    BattleSingleTargetDamageNormalizationService,
)


def _hit(
    event_id: str,
    sequence: int,
    damage: float,
    before: float,
    after: float,
    *,
    character_id: int = 1003,
    effect: str = "GE_Player_Sagiri_UltraSkill1_Damage",
    target_id: str = "boss",
    relative_time_us: int | None = None,
    scope_half: str = "",
    overkill_damage: float | None = None,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=sequence,
        relative_time_us=(
            sequence * 100_000
            if relative_time_us is None
            else relative_time_us
        ),
        character_id=character_id,
        character_name="法帝娅" if character_id == 1039 else "早雾",
        skill_name="测试",
        damage_name="测试",
        damage_component="unknown",
        attack_type="Q技能",
        damage_attribute="incantation",
        target_id=target_id,
        target_name="墨菲斯托",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id=effect,
        scope_half=scope_half,
        target_hp_before=before,
        target_hp_after=after,
        overkill_damage=overkill_damage,
    )


class BattleHitReplaySupportTests(unittest.TestCase):
    def test_stale_before_value_records_overlap_without_reducing_damage(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit(
                "nova", 77, 57_600.0, 4_983_823.5, 4_926_223.5,
                character_id=1039,
                effect="Buff_Reaction_4_new",
            ),
            _hit("sagiri", 78, 60_756.0, 4_983_823.5, 4_923_067.5),
        ), confirmed_single_target=True)

        self.assertIsNone(hits[0].raw_damage)
        self.assertEqual(60_756.0, hits[1].raw_damage)
        self.assertEqual(60_756.0, hits[1].damage)
        self.assertEqual(
            "single_target_hp_interval_overlap_diagnostic",
            hits[1].damage_correction_kind,
        )

    def test_reliable_target_identity_normalizes_without_manual_confirmation(
        self,
    ) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit(
                "nova", 77, 57_600.0, 4_983_823.5, 4_926_223.5,
                character_id=1039,
                effect="Buff_Reaction_4_new",
            ),
            _hit("sagiri", 78, 60_756.0, 4_983_823.5, 4_923_067.5),
        ), confirmed_single_target=False)

        self.assertEqual(60_756.0, hits[1].damage)
        self.assertEqual(60_756.0, hits[1].raw_damage)

    def test_unknown_target_keeps_raw_damage_without_manual_confirmation(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit("first", 1, 100.0, 1_000.0, 900.0, target_id="unknown"),
            _hit("second", 2, 150.0, 1_000.0, 850.0, target_id="unknown"),
        ), confirmed_single_target=False)

        self.assertEqual((100.0, 150.0), tuple(hit.damage for hit in hits))
        self.assertIsNone(hits[1].raw_damage)

    def test_simultaneous_distinct_targets_keep_independent_hp_frontiers(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit(
                "first", 1, 100.0, 1_000.0, 900.0,
                target_id="boss-a", relative_time_us=1_000_000,
            ),
            _hit(
                "second", 2, 150.0, 1_000.0, 850.0,
                target_id="boss-b", relative_time_us=1_000_000,
            ),
        ), confirmed_single_target=False)

        self.assertEqual((100.0, 150.0), tuple(hit.damage for hit in hits))
        self.assertTrue(all(hit.raw_damage is None for hit in hits))

    def test_same_wire_id_reused_by_halves_keeps_independent_hp_frontiers(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit(
                "upper", 1, 100.0, 1_000.0, 900.0,
                target_id="enemy-wire:1", scope_half="upper",
            ),
            _hit(
                "lower", 2, 150.0, 1_000.0, 850.0,
                target_id="enemy-wire:1", scope_half="lower",
            ),
        ), confirmed_single_target=False)

        self.assertEqual((100.0, 150.0), tuple(hit.damage for hit in hits))
        self.assertTrue(all(hit.raw_damage is None for hit in hits))


    def test_fully_covered_stale_interval_does_not_move_hp_frontier(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit("first", 1, 100.0, 1_000.0, 900.0),
            _hit("stale", 2, 10.0, 1_000.0, 990.0),
            _hit("third", 3, 150.0, 950.0, 800.0),
        ), confirmed_single_target=True)

        self.assertEqual((100.0, 10.0, 150.0), tuple(hit.damage for hit in hits))


    def test_core_v3_overkill_authority_disables_hp_overlap_diagnostic(self) -> None:
        hits = BattleSingleTargetDamageNormalizationService.normalize((
            _hit("first", 1, 100.0, 1_000.0, 900.0, overkill_damage=0.0),
            _hit("second", 2, 150.0, 1_000.0, 850.0, overkill_damage=0.0),
        ), confirmed_single_target=True)

        self.assertTrue(all(hit.raw_damage is None for hit in hits))
        self.assertTrue(all(hit.damage_correction_kind == "" for hit in hits))


if __name__ == "__main__":
    unittest.main()
