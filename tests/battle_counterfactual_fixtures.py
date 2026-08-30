# 提供战斗反事实测试共用的公开契约夹具。
"""Shared public-contract fixtures for battle counterfactual tests."""

from __future__ import annotations

from src.domain.battle_report import BattleBuffModifierEvidence
from src.services.battle_buff_inference_service import BattleStaticBuffRule


def build_fixture() -> dict:
    return {
        "characters": [{
            "character_id": 1072,
            "observed_name": "灵可",
            "stat_snapshot_source": "frozen_v25",
            "stats": [
                {"source_group": "resolved", "property_id": "AtkBase", "display_name": "基础攻击力", "value": 1000, "is_percent": False},
                {"source_group": "resolved", "property_id": "AtkUp", "display_name": "攻击力提升", "value": 0.5, "is_percent": True},
                {"source_group": "resolved", "property_id": "AtkAdd", "display_name": "固定攻击力", "value": 100, "is_percent": False},
                {"source_group": "resolved", "property_id": "CritBase", "display_name": "暴击率", "value": 0.5, "is_percent": True},
                {"source_group": "resolved", "property_id": "CritDamageBase", "display_name": "暴击伤害", "value": 1.0, "is_percent": True},
                {"source_group": "resolved", "property_id": "DamageUpGeneralBase", "display_name": "通用伤害增强", "value": 0.2, "is_percent": True},
                {"source_group": "resolved", "property_id": "DamageUpNatureBase", "display_name": "自然属性伤害增强", "value": 0.25, "is_percent": True},
                {"source_group": "resolved", "property_id": "DefIgnore", "display_name": "防御忽略", "value": 0.10, "is_percent": True},
                {"source_group": "resolved", "property_id": "MagBase", "display_name": "环合强度", "value": 100, "is_percent": False},
            ],
        }]
    }


def evidence_fixture() -> dict:
    return {
        "axis_complete": True,
        "hits": [
            {
                "sequence_text": "1",
                "sequence_order": 1,
                "relative_time_us": 1_000_000,
                "character_id": 1072,
                "character_name": "灵可",
                "direction": "outgoing",
                "damage": 1000,
                "follow_up_damage": 200,
                "ability_name": "普通攻击",
                "damage_name": "第一段",
                "damage_component": "skill",
                "attack_type": "normal",
                "damage_attribute": "nature",
                "follow_up_damage_name": "覆纹追加攻击",
                "follow_up_damage_component": "reaction",
                "follow_up_attack_type": "follow_up",
                "follow_up_damage_attribute": "nature",
                "follow_up_labels": ["覆纹"],
                "target_id": "monster-1",
                "target_name": "训练目标",
                "target_hp_before": 5000,
                "target_hp_after": 3800,
                "target_max_hp": 5000,
            },
            {
                "sequence_text": "2",
                "sequence_order": 2,
                "relative_time_us": 3_000_000,
                "character_id": 1072,
                "character_name": "灵可",
                "direction": "outgoing",
                "damage": 300,
                "follow_up_damage": 0,
                "ability_name": "环合",
                "damage_name": "黯星",
                "damage_component": "reaction",
                "attack_type": "reaction",
                "damage_attribute": "psychically",
                "follow_up_labels": [],
                "target_id": "monster-1",
                "target_name": "训练目标",
            },
        ],
        "time_stop_intervals": [],
    }


def attack_buff_rule(event_type: str) -> BattleStaticBuffRule:
    return BattleStaticBuffRule(
        rule_id=f"test:{event_type}",
        source_effect_definition_id="test:attack-buff",
        source_kind="test",
        source_character_id=1072,
        source_character_name="灵可",
        source_asset_path="/Game/Test/BuffSource",
        target_asset_path="/Game/Test/AttackBuff",
        target_name="测试攻击 Buff",
        target_scope="self",
        event_type=event_type,
        effect_type="ADD",
        duration_policy="HasDuration",
        duration_seconds=10.0,
        stack_count=1,
        modifiers=(BattleBuffModifierEvidence(
            property_id="AtkUp",
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="ScalableFloat",
            magnitude_value=0.5,
            calculation_asset_path="",
            value_confidence="高",
        ),),
    )
