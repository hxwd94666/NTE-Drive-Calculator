# 验证统一轴把直伤贴合角色动作，并把特殊/环合伤害投影为全队公共行。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.features.battle_report.timeline_layout import build_timeline_layout


def _action(ordinal: int, start_us: int, end_us: int) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=1001,
        character_name="测试角色",
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


def _group(channel_key: str, channel_label: str) -> BattleTimelineDamageGroup:
    return BattleTimelineDamageGroup(
        group_id=f"group:{channel_key}",
        character_id=1001,
        character_name="测试角色",
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
    def test_tap_is_square_and_hold_uses_its_time_span(self) -> None:
        tap = BattleInferredInput(
            input_event_id="tap",
            action_id="tap-action",
            device_kind="mouse",
            display_text="A",
            character_id=1001,
            character_name="测试角色",
            start_us=1_000_000,
            end_us=1_000_001,
            is_character_switch=False,
            timing_confidence="低",
        )
        hold = BattleInferredInput(
            input_event_id="hold",
            action_id="hold-action",
            device_kind="mouse",
            display_text="Z",
            character_id=1001,
            character_name="测试角色",
            start_us=2_000_000,
            end_us=4_000_000,
            is_character_switch=False,
            timing_confidence="低",
        )
        analysis = SimpleNamespace(
            inferred_inputs=(tap, hold),
            inferred_actions=(_action(1, 1_000_000, 4_000_000),),
            timeline_hits=(),
            timeline_damage_groups=(),
        )

        layout = build_timeline_layout(analysis, x_for_time=lambda value: value / 10_000)
        tap_rect = layout.input_rows[0][2]
        hold_rect = layout.input_rows[1][2]

        self.assertEqual(tap_rect.height(), tap_rect.width())
        self.assertGreater(hold_rect.width(), hold_rect.height())

    def test_role_has_one_action_row_with_direct_damage_above_it(self) -> None:
        analysis = SimpleNamespace(
            inferred_inputs=(),
            inferred_actions=(
                _action(1, 1_000_000, 1_100_000),
                _action(2, 1_150_000, 1_300_000),
            ),
            timeline_hits=(),
            timeline_damage_groups=(_group("direct", "直伤"),),
        )

        layout = build_timeline_layout(analysis, x_for_time=lambda value: value / 10_000)

        self.assertEqual(1, len({row[0].key for row in layout.action_rows}))
        first_action = layout.action_rows[0][2]
        second_action = layout.action_rows[1][2]
        direct_bar = layout.group_rows[0][2]
        self.assertLessEqual(first_action.right(), second_action.left())
        self.assertLess(direct_bar.center().y(), first_action.center().y())
        self.assertEqual(layout.action_rows[0][0].key, layout.group_rows[0][0].key)

    def test_special_rows_precede_reaction_rows_without_role_labels(self) -> None:
        analysis = SimpleNamespace(
            inferred_inputs=(),
            inferred_actions=(_action(1, 1_000_000, 1_100_000),),
            timeline_hits=(),
            timeline_damage_groups=(
                _group("reaction_scorch", "浊燃"),
                _group("special_nightmare", "噩梦"),
            ),
        )

        layout = build_timeline_layout(analysis, x_for_time=lambda value: value / 10_000)
        damage_lanes = [lane for lane in layout.lanes if lane.kind == "damage"]

        self.assertEqual(("噩梦", "浊燃"), tuple(lane.label for lane in damage_lanes))
        self.assertTrue(all(lane.character_id is None for lane in damage_lanes))


if __name__ == "__main__":
    unittest.main()
