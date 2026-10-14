# 验证覆纹索引保留正式事件边界和原始顺序，拒绝无效来源。
from __future__ import annotations

from dataclasses import replace
import unittest

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.services.battle_report.damage.weave_source import (
    BattleWeaveSourceIndex, find_paired_weave_source_hit,
)


def hit(event_id: str, **changes) -> BattleAnalysisHit:
    base = BattleAnalysisHit(
        event_id=event_id, sequence=7, relative_time_us=100,
        character_id=1, character_name="甲", skill_name="技能", damage_name="伤害",
        damage_component="primary", attack_type="skill", damage_attribute="nature",
        target_id="target-a", target_name="目标", damage=100.0,
        direction="outgoing", is_follow_up=False, classification="direct",
        scope_half="first",
    )
    return replace(base, **changes)


class WeaveSourceIndexTests(unittest.TestCase):
    def test_first_eligible_packet_wins_without_time_or_actor_guessing(self):
        first = hit("first", relative_time_us=500, character_id=2)
        later = hit("later", relative_time_us=1)
        query = hit("weave", classification="weave")
        rows = [query, hit("follow", is_follow_up=True), hit("zero", damage=0), first, later]
        index = BattleWeaveSourceIndex(rows)
        self.assertIs(first, index.find(query))
        self.assertIs(first, find_paired_weave_source_hit(query, index))
        self.assertIs(first, find_paired_weave_source_hit(query, rows))
        self.assertIs(later, BattleWeaveSourceIndex(list(reversed(rows))).find(query))

    def test_sequence_target_half_and_direction_each_partition_sources(self):
        rows = [hit("base"), hit("sequence", sequence=8), hit("target", target_id="target-b"),
                hit("half", scope_half="second"), hit("direction", direction="incoming"),
                hit("case", scope_half="FIRST")]
        index = BattleWeaveSourceIndex(rows)
        for source in rows:
            with self.subTest(source=source.event_id):
                query = replace(source, event_id="weave", classification="weave")
                self.assertIs(source, index.find(query))
        self.assertIsNone(index.find(hit("missing", sequence=9, classification="weave")))

    def test_invalid_damage_is_not_a_source_and_index_freezes_collection(self):
        query = hit("weave", classification="weave")
        rows = [hit("negative", damage=-1), hit("nan", damage=float("nan")),
                hit("follow", is_follow_up=True), query]
        index = BattleWeaveSourceIndex(rows)
        self.assertIsNone(index.find(query))
        rows.append(hit("new"))
        self.assertIsNone(index.find(query))
        self.assertIs(rows[-1], BattleWeaveSourceIndex(rows).find(query))


if __name__ == "__main__":
    unittest.main()
