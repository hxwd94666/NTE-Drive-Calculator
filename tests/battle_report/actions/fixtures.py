# 共用不可变逐击夹具，避免测试模块间导入与重复收集。
from __future__ import annotations

from src.domain.battle_report.timeline.events import BattleAnalysisHit


def _hit(
    sequence: int,
    time_us: int,
    *,
    ability_id: str = "GA_Test_Melee",
    effect_id: str = "GE_Player_Test_Melee1_Damage",
    attack_type: str = "普攻",
    classification: str = "direct",
    follow_up: bool = False,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1001,
        character_name="测试角色",
        skill_name="普通攻击：测试连段",
        damage_name="测试连段",
        damage_component="skill",
        attack_type=attack_type,
        damage_attribute="CHAOS",
        target_id="target-1",
        target_name="测试目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=follow_up,
        classification=classification,
        ability_id=ability_id,
        gameplay_effect_id=effect_id,
    )
