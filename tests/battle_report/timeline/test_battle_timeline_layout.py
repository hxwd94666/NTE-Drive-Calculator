# 验证公共伤害保持来源角色身份，不为仅有出伤证据的角色补造动作。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report.timeline.events import BattleInferredAction, BattleTimelineDamageGroup
from src.features.battle_report.timeline.layout import build_timeline_layout


def _action(
    ordinal: int,
    start_us: int,
    end_us: int,
    *,
    character_id: int = 1001,
    character_name: str = "测试角色",
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=character_id,
        character_name=character_name,
        action_name=f"动作 {ordinal}",
        input_kind="A",
        input_sequence="A",
        start_us=start_us,
        end_us=end_us,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="正式逐击窗口",
        evidence_event_ids=(f"{ordinal}:primary",),
        gameplay_effect_ids=(f"GE_Test_{ordinal}",),
    )


def _group(
    channel_key: str,
    channel_label: str,
    *,
    character_id: int = 1001,
    character_name: str = "测试角色",
) -> BattleTimelineDamageGroup:
    return BattleTimelineDamageGroup(
        group_id=f"group:{channel_key}",
        character_id=character_id,
        character_name=character_name,
        direction="outgoing",
        channel_key=channel_key,
        channel_label=channel_label,
        damage_name=channel_label,
        source_skill_name="测试来源技能",
        ability_id="GA_Test",
        start_us=1_000_000,
        end_us=1_200_000,
        hits=1,
        damage=100.0,
        evidence_event_ids=(f"{channel_key}:primary",),
    )


class BattleTimelineLayoutTests(unittest.TestCase):

    def test_public_reaction_owner_keeps_role_lane_without_splitting_damage_lane(
        self,
    ) -> None:
        analysis = SimpleNamespace(
            inferred_inputs=(),
            inferred_actions=(
                _action(
                    1,
                    1_000_000,
                    1_100_000,
                    character_id=1004,
                    character_name="安魂曲",
                ),
            ),
            timeline_hits=(
                SimpleNamespace(character_id=1036, character_name="残虹"),
            ),
            timeline_damage_groups=(
                _group("special_nightmare", "噩梦", character_id=1004, character_name="安魂曲"),
                _group(
                    "reaction_scorch",
                    "浊燃",
                    character_id=1036,
                    character_name="残虹",
                ),
            ),
        )

        layout = build_timeline_layout(analysis, x_for_time=lambda value: value / 10_000)
        action_lanes = [lane for lane in layout.lanes if lane.kind == "action"]

        self.assertEqual(
            {(1004, "安魂曲"), (1036, "残虹")},
            {(lane.character_id, lane.character_name) for lane in action_lanes},
        )
        self.assertFalse(
            any(row[0].character_id == 1036 for row in layout.action_rows),
        )
        self.assertEqual(
            {group.group_id for group in analysis.timeline_damage_groups},
            {group.group_id for _lane, group, _rect in layout.group_rows},
        )
        for lane, _entry, _rect in layout.group_rows:
            self.assertEqual("damage", lane.kind)
            self.assertIsNone(lane.character_id)


if __name__ == "__main__":
    unittest.main()
