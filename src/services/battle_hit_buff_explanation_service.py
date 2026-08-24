# 把单次逐击的推算 Buff 投影整理为可审计的中文详情。
"""Qt-free explanation for inferred Buffs active on one battle hit."""

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffProjectionDecision,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
    normalize_battle_buff_property_id,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


_SCOPE_LABELS = {
    "self": "自身",
    "team": "全队",
    "target": "敌方目标",
    "unknown": "作用对象待确认",
}
_STATUS_LABELS = {
    "applied": "已投影",
    "not_applied": "未采用",
    "unresolved": "待确认/结构化",
}
_PROPERTY_LABELS = {
    "AtkUp": "攻击力提升",
    "AtkAdd": "额外攻击力",
    "HPMaxUp": "生命上限提升",
    "HPMaxAdd": "额外生命上限",
    "DefUp": "防御力提升",
    "DefAdd": "额外防御力",
    "CritBase": "暴击率",
    "CritDamageBase": "暴击伤害",
    "DamageUpGeneralBase": "通用伤害提升",
    "DamageUpChaosBase": "暗属性异能伤害提升",
    "DamageUpCosmosBase": "光属性异能伤害提升",
    "DamageUpIncantationBase": "咒属性异能伤害提升",
    "DamageUpLakshanaBase": "灵属性异能伤害提升",
    "DamageUpNatureBase": "自然属性异能伤害提升",
    "DamageUpPsycheBase": "魂属性异能伤害提升",
    "DamageUpPsychicallyBase": "心灵伤害提升",
    "DefIgnore": "防御穿透",
    "DamagePenetrateChaos": "暗属性穿透",
    "DamagePenetrateCosmos": "光属性穿透",
    "DamagePenetrateIncantation": "咒属性穿透",
    "DamagePenetrateLakshana": "灵属性穿透",
    "DamagePenetrateNature": "自然属性穿透",
    "DamagePenetratePsyche": "魂属性穿透",
    "DamagePenetratePsychically": "心灵属性穿透",
    "DamageResistChaosBase": "敌方暗属性抗性变化",
    "DamageResistChaosAdd": "敌方额外暗属性抗性变化",
    "DamageResistCosmosBase": "敌方光属性抗性变化",
    "DamageResistCosmosAdd": "敌方额外光属性抗性变化",
    "DamageResistIncantationBase": "敌方咒属性抗性变化",
    "DamageResistIncantationAdd": "敌方额外咒属性抗性变化",
    "DamageResistLakshanaBase": "敌方灵属性抗性变化",
    "DamageResistLakshanaAdd": "敌方额外灵属性抗性变化",
    "DamageResistNatureBase": "敌方自然属性抗性变化",
    "DamageResistNatureAdd": "敌方额外自然属性抗性变化",
    "DamageResistPsycheBase": "敌方魂属性抗性变化",
    "DamageResistPsycheAdd": "敌方额外魂属性抗性变化",
    "DamageResistPsychicallyBase": "敌方心灵抗性变化",
    "DamageResistPsychicallyAdd": "敌方额外心灵抗性变化",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
    "UnbalIntensityUp": "倾陷强度提升",
    "UnbalIntensityAdd": "额外倾陷强度",
    "UnbalDamageUp": "倾陷伤害提升",
    "ChargeGetEfficiencyBase": "充能效率",
    "ImmuneDeadByTeammates": "队友免死",
    "ShareOutTeammatesDamageMul": "队友伤害分摊",
    "MoveSpeedMaxMult": "移动速度上限",
}
_PERCENT_PROPERTIES = frozenset({
    "AtkUp",
    "HPMaxUp",
    "DefUp",
    "CritBase",
    "CritDamageBase",
    "DamageUpGeneralBase",
    "DefIgnore",
    "UnbalIntensityUp",
    "UnbalDamageUp",
    "ChargeGetEfficiencyBase",
    *(
        property_id
        for property_id in _PROPERTY_LABELS
        if property_id.startswith("DamageUp")
        or property_id.startswith("DamagePenetrate")
        or property_id.startswith("DamageResist")
    ),
})


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def _property_label(property_id: str) -> str:
    return _PROPERTY_LABELS.get(property_id, property_id)


def _value(property_id: str, value: float) -> str:
    if property_id in _PERCENT_PROPERTIES:
        return f"{value * 100:+g}%"
    return f"{value:+,.3f}".rstrip("0").rstrip(".")


def _raw_modifier_lines(
    interval: BattleInferredBuffInterval,
    decision: BattleBuffProjectionDecision,
) -> tuple[str, ...]:
    lines = []
    applied_ids = set(decision.applied_property_ids)
    for modifier in interval.modifiers:
        property_id = normalize_battle_buff_property_id(modifier.property_id)
        label = _property_label(property_id)
        if modifier.magnitude_value is not None:
            total = float(modifier.magnitude_value) * max(1, interval.stacks)
            value = _value(property_id, total)
            if interval.stacks > 1:
                value += (
                    f"（{_value(property_id, float(modifier.magnitude_value))}"
                    f" × {interval.stacks} 层）"
                )
        elif modifier.calculation_asset_path:
            value = (
                "Calculation："
                + modifier.calculation_asset_path.rsplit("/", 1)[-1]
            )
        else:
            value = "数值尚未解析"
        usage = "已投影到属性值" if property_id in applied_ids else "未投影到属性值"
        lines.append(
            f"  - {label} {value}（{property_id}，{usage}，数值置信度"
            f" {modifier.value_confidence}）"
        )
    return tuple(lines) or ("  - 没有提取到属性修正。",)


class BattleHitBuffExplanationService:
    """Describe active intervals and their exact per-hit projection decision."""

    @classmethod
    def build(
        cls,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
    ) -> str:
        projection = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
        active_by_id = {row.interval_id: row for row in intervals}
        decisions = tuple(
            decision
            for decision in projection.decisions
            if decision.interval_id in active_by_id
        )
        counts = {
            status: sum(row.status == status for row in decisions)
            for status in _STATUS_LABELS
        }
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        lines = [
            f"{hit.character_name} · {damage_name}",
            f"命中时间：{_time(hit.relative_time_us)}    目标：{hit.target_name}",
            (
                f"命中时推算 Buff：{len(decisions)} 个    "
                f"已投影 {counts['applied']} / 未采用 {counts['not_applied']} / "
                f"待确认 {counts['unresolved']}"
            ),
            "口径：这是冻结配装、动作和逐击推算，不是 nte-core 运行时实测 Buff。",
            "公式消费口径：已投影只表示进入逐击属性值；是否被当前伤害公式消费，"
            "以伤害公式列出的乘区和来源项为准。",
            "",
            "【投影到逐击属性值的加成汇总】",
        ]
        if projection.modifiers:
            for modifier in projection.modifiers:
                sources = "、".join(modifier.buff_names)
                lines.append(
                    f"- {_property_label(modifier.property_id)} "
                    f"{_value(modifier.property_id, modifier.additive_value)}"
                    f"（{modifier.property_id}，{_SCOPE_LABELS.get(modifier.target_scope, modifier.target_scope)}，"
                    f"置信度 {modifier.confidence}）\n"
                    f"  来源：{sources}"
                )
        else:
            lines.append("- 没有 Buff 数值投影到本击属性值。")

        decision_by_status = {
            status: tuple(row for row in decisions if row.status == status)
            for status in _STATUS_LABELS
        }
        for status, title in _STATUS_LABELS.items():
            matching = decision_by_status[status]
            if not matching:
                continue
            lines.extend(("", f"【{title} Buff】"))
            for decision in matching:
                interval = active_by_id[decision.interval_id]
                lines.append(
                    f"- {interval.buff_name} ×{interval.stacks}"
                    f"（来源角色：{interval.source_character_name}；"
                    f"作用对象：{_SCOPE_LABELS.get(interval.target_scope, interval.target_scope)}；"
                    f"区间：{_time(interval.start_us)}—{_time(interval.end_us)}；"
                    f"状态 {interval.state_confidence} / 数值 {interval.value_confidence}）"
                )
                lines.extend(_raw_modifier_lines(interval, decision))
                if decision.reasons:
                    lines.append(f"  判定：{'；'.join(decision.reasons)}")
                lines.extend((
                    f"  ID：{interval.source_effect_definition_id}",
                    f"  资产：{interval.buff_asset_path}",
                    f"  推算依据：{interval.inference_basis}",
                ))
        if not decisions:
            lines.extend((
                "",
                "【证据边界】",
                "- 本击没有匹配到命中时有效的推算 Buff 区间。",
            ))
        return "\n".join(lines)
