# 对伤害弧盘的时停、事件去重、叠层与消费状态机保留原生逐字段差分。
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import unittest

from tests.battle_report.forks.test_damage_completion import _action, _hit, _rules
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.forks.damage_state import BattleForkDamageStateService


def _fixture():
    rules = (
        *_rules("upgradestar_pack_fork_Time", {
            "buff_Time_AtkUp": .16, "buff_Time_stateCritDamageUp": .24,
            "buff_Time_CritDamageUp": .08, "buff_Time_DefIgnore": .12,
            "buff_Time_DefIgnore_Dur": 70.0,
        }),
        *_rules("upgradestar_pack_fork_TigerTally", {
            "buff_TigerTally_AtkUp": .15, "buff_TigerTally_NormalUp": .15,
            "buff_TigerTally_CD": 15.0, "buff_TigerTally_CD4": 15.0,
            "buff_TigerTally_Qup": .10, "buff_TigerTally_CD3": 10.0,
        }),
        *_rules("upgradestar_pack_fork_Rose", {
            "buff_Rose_AtkUp": .1, "buff_Rose_CritDamageUp": .03,
            "buff_Rose_CD": 10.0, "buff_Rose_UnbalTime": 3.0,
        }),
        *_rules("upgradestar_pack_fork_moon", {
            "buff_moon_PsycheUp": .12, "buff_moon_CritDamageUp": .02, "buff_moon_CD": 5.0,
        }),
        *_rules("upgradestar_pack_fork_spider", {
            "buff_spider_CD": 8.0, "buff_spider_AtkUp": .02, "buff_spider_AtkUp2": .1,
        }),
    )
    actions = (
        _action(1, 1001, "E", 0, 1_000_000),
        _action(2, 2002, "E", 1_000_000, 3_000_000),
        _action(3, 2002, "E", 2_000_000, 4_000_000),
        _action(4, 2003, "QTE", 4_000_000, 4_500_000),
        _action(5, 2004, "E", 5_000_000, 6_000_000),
        _action(6, 1001, "Q", 7_000_000, 8_000_000),
        _action(7, 1001, "Q", 9_000_000, 10_000_000,
                gameplay_effect_ids=("GE_Nanally_Cat_Skill_Damage",)),
        _action(8, 1001, "E", 14_000_000, 15_000_000),
        _action(9, 1001, "Q", 16_000_000, 17_000_000),
    )
    hits = tuple(_hit(i, i * 100_000, channel="dot", damage_attribute="psyche")
                 for i in range(1, 66))
    return rules, actions, hits


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "isolated native executable not configured")
class NativeForkDamageStateDifferentialTests(unittest.TestCase):
    def test_all_damage_forks_match_python_with_time_stops_and_overlapping_casts(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-fork-damage-test",
        )
        rules, actions, hits = _fixture()
        for stops in ((), ((2_000_000, 3_000_000), (2_500_000, 3_500_000),
                          (7_000_000, 9_000_000), (None, 100))):
            for selected_rules in (rules, tuple(reversed(rules)),
                                   tuple(replace(rule, duration_seconds=None) for rule in rules)):
                kwargs = dict(actions=actions, hits=hits, battle_end_us=90_000_000,
                              time_stop_intervals=stops)
                with self.subTest(stops=stops, rules=selected_rules):
                    expected = BattleForkDamageStateService.infer_specialized(selected_rules, **kwargs)
                    actual = BattleForkDamageStateService.infer_specialized(
                        selected_rules, **kwargs, compute_backend=client,
                    )
                    self.assertEqual(expected, actual)
