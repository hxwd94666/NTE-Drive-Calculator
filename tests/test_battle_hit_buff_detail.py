# 验证逐击日志可点击查看推算 Buff、加成和采用判定。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.features.battle_report.analysis_view import BattleLongAnalysisView
from src.services.battle_hit_buff_explanation_service import (
    BattleHitBuffExplanationService,
)


def _hit() -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="1:primary",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=1036,
        character_name="残虹",
        skill_name="普通攻击：燎原",
        damage_name="残虹 · 燎原",
        damage_component="direct",
        attack_type="普攻",
        damage_attribute="incantation",
        target_id="target",
        target_name="墨菲斯托",
        damage=1_000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _interval(
    interval_id: str,
    *,
    name: str,
    property_id: str,
    value: float | None,
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path=f"/Game/Test/{interval_id}",
        buff_name=name,
        source_effect_definition_id=f"source:{interval_id}",
        source_kind="test",
        source_character_id=1036,
        source_character_name="残虹",
        target_scope="self",
        start_us=0,
        end_us=5_000_000,
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="中",
        value_confidence="中" if value is not None else "低",
        inference_basis="测试推算依据",
        trigger_event_type="STATIC_EQUIPPED_SOURCE",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(BattleBuffModifierEvidence(
            property_id=property_id,
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="constant" if value is not None else "unknown",
            magnitude_value=value,
            calculation_asset_path="",
            value_confidence="中" if value is not None else "低",
        ),),
    )


class BattleHitBuffDetailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_explanation_separates_applied_and_unresolved_buffs(self) -> None:
        text = BattleHitBuffExplanationService.build(
            _hit(),
            (
                _interval(
                    "damage-up",
                    name="通用伤害提升15%",
                    property_id="DamageUpGeneralBase",
                    value=0.15,
                ),
                _interval(
                    "unknown",
                    name="未知计算 Buff",
                    property_id="AtkUp",
                    value=None,
                ),
            ),
        )

        self.assertIn("已投影 1 / 未采用 0 / 待确认 1", text)
        self.assertIn("通用伤害提升 +15%", text)
        self.assertIn("DamageUpGeneralBase", text)
        self.assertIn("【已投影 Buff】", text)
        self.assertIn("【待确认/结构化 Buff】", text)
        self.assertIn("ID：source:damage-up", text)
        self.assertIn("资产：/Game/Test/damage-up", text)

    def test_log_buff_cell_opens_reusable_detail_dialog(self) -> None:
        view = BattleLongAnalysisView()
        hit = _hit()
        interval = _interval(
            "damage-up",
            name="通用伤害提升15%",
            property_id="DamageUpGeneralBase",
            value=0.15,
        )
        view._analysis = SimpleNamespace(
            hits=(hit,),
            buff_intervals=(interval,),
            hit_replays=(),
            battle_start_us=0,
            time_stop_intervals=(),
        )

        view._render_log()

        item = view.log_table.item(0, 9)
        self.assertEqual("查看", item.text())
        self.assertEqual(
            hit.event_id,
            item.data(Qt.ItemDataRole.UserRole),
        )
        view.log_table.cellClicked.emit(0, 9)
        self.app.processEvents()

        dialog = view._hit_buff_dialog
        self.assertTrue(dialog.isVisible())
        self.assertIn("事件 ID：1:primary", dialog.detail.toPlainText())
        self.assertIn("通用伤害提升 +15%", dialog.detail.toPlainText())
        dialog.close()
        view.close()


if __name__ == "__main__":
    unittest.main()
