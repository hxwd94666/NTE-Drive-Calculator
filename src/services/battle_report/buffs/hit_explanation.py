# 把单次逐击的 Buff 证据与属性投影整理为可审计的中文详情。

from __future__ import annotations

from collections.abc import Sequence

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.buffs.models import BattleBuffProjectionDecision, BattleInferredBuffInterval
from src.domain.localization.catalog import TextCatalog, localized_text
from src.services.battle_report.buffs.attribute_projection import (
    BattleBuffAttributeProjectionService,
    normalize_battle_buff_property_id,
)
from src.services.official_role.skill_names import preferred_battle_damage_name


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
    "AtkUp": ("atk_up", "攻击力提升"),
    "AtkAdd": ("atk_add", "额外攻击力"),
    "HPMaxUp": ("hp_max_up", "生命上限提升"),
    "HPMaxAdd": ("hp_max_add", "额外生命上限"),
    "DefUp": ("def_up", "防御力提升"),
    "DefAdd": ("def_add", "额外防御力"),
    "CritBase": ("crit_base", "暴击率"),
    "CritDamageBase": ("crit_damage_base", "暴击伤害"),
    "DamageUpGeneralBase": ("damage_up_general_base", "通用伤害提升"),
    "DamageUpChaosBase": ("damage_up_chaos_base", "暗属性异能伤害提升"),
    "DamageUpCosmosBase": ("damage_up_cosmos_base", "光属性异能伤害提升"),
    "DamageUpIncantationBase": ("damage_up_incantation_base", "咒属性异能伤害提升"),
    "DamageUpLakshanaBase": ("damage_up_lakshana_base", "相属性异能伤害提升"),
    "DamageUpNatureBase": ("damage_up_nature_base", "灵属性异能伤害提升"),
    "DamageUpPsycheBase": ("damage_up_psyche_base", "魂属性异能伤害提升"),
    "DamageUpPsychicallyBase": ("damage_up_psychically_base", "心灵伤害提升"),
    "DefIgnore": ("def_ignore", "防御穿透"),
    "DamagePenetrateChaos": ("damage_penetrate_chaos", "暗属性穿透"),
    "DamagePenetrateCosmos": ("damage_penetrate_cosmos", "光属性穿透"),
    "DamagePenetrateIncantation": ("damage_penetrate_incantation", "咒属性穿透"),
    "DamagePenetrateLakshana": ("damage_penetrate_lakshana", "相属性穿透"),
    "DamagePenetrateNature": ("damage_penetrate_nature", "灵属性穿透"),
    "DamagePenetratePsyche": ("damage_penetrate_psyche", "魂属性穿透"),
    "DamagePenetratePsychically": ("damage_penetrate_psychically", "心灵属性穿透"),
    "DamageResistChaosBase": ("damage_resist_chaos_base", "敌方暗属性抗性变化"),
    "DamageResistChaosAdd": ("damage_resist_chaos_add", "敌方额外暗属性抗性变化"),
    "DamageResistCosmosBase": ("damage_resist_cosmos_base", "敌方光属性抗性变化"),
    "DamageResistCosmosAdd": ("damage_resist_cosmos_add", "敌方额外光属性抗性变化"),
    "DamageResistIncantationBase": ("damage_resist_incantation_base", "敌方咒属性抗性变化"),
    "DamageResistIncantationAdd": ("damage_resist_incantation_add", "敌方额外咒属性抗性变化"),
    "DamageResistLakshanaBase": ("damage_resist_lakshana_base", "敌方相属性抗性变化"),
    "DamageResistLakshanaAdd": ("damage_resist_lakshana_add", "敌方额外相属性抗性变化"),
    "DamageResistNatureBase": ("damage_resist_nature_base", "敌方灵属性抗性变化"),
    "DamageResistNatureAdd": ("damage_resist_nature_add", "敌方额外灵属性抗性变化"),
    "DamageResistPsycheBase": ("damage_resist_psyche_base", "敌方魂属性抗性变化"),
    "DamageResistPsycheAdd": ("damage_resist_psyche_add", "敌方额外魂属性抗性变化"),
    "DamageResistPsychicallyBase": ("damage_resist_psychically_base", "敌方心灵抗性变化"),
    "DamageResistPsychicallyAdd": ("damage_resist_psychically_add", "敌方额外心灵抗性变化"),
    "MagBase": ("mag_base", "环合强度"),
    "UnbalIntensityBase": ("unbal_intensity_base", "倾陷强度"),
    "UnbalIntensityUp": ("unbal_intensity_up", "倾陷强度提升"),
    "UnbalIntensityAdd": ("unbal_intensity_add", "额外倾陷强度"),
    "UnbalDamageUp": ("unbal_damage_up", "倾陷伤害提升"),
    "ChargeGetEfficiencyBase": ("charge_get_efficiency_base", "充能效率"),
    "ImmuneDeadByTeammates": ("immune_dead_by_teammates", "队友免死"),
    "ShareOutTeammatesDamageMul": ("share_out_teammates_damage_mul", "队友伤害分摊"),
    "MoveSpeedMaxMult": ("move_speed_max_mult", "移动速度上限"),
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

_LOCALE_PREFIX = "battle_report.hit_explanation."


def _text(
    text_catalog: TextCatalog | None,
    key: str,
    fallback: str,
    **values: object,
) -> str:
    """按稳定 key 取词；未注入目录或未登记时逐字回退本文件的中文基线。"""

    return localized_text(text_catalog, f"{_LOCALE_PREFIX}{key}", fallback, **values)


def _scope_label(scope: str, *, text_catalog: TextCatalog | None = None) -> str:
    """作用对象先查稳定表；未登记的标识原样保留，不为它构造 key。"""

    if scope not in _SCOPE_LABELS:
        return scope
    return _text(
        text_catalog,
        f"label.scope.{scope}",
        _SCOPE_LABELS[scope],
    )


def _status_label(status: str, *, text_catalog: TextCatalog | None = None) -> str:
    return _text(
        text_catalog,
        f"label.status.{status}",
        _STATUS_LABELS[status],
    )


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def battle_buff_property_label(
    property_id: str,
    *,
    text_catalog: TextCatalog | None = None,
) -> str:
    """属性显示名：原表为兜底，未登记的属性仍显示原始属性标识。"""

    suffix, fallback = _PROPERTY_LABELS.get(property_id, ("", property_id))
    if not suffix:
        return fallback
    return _text(text_catalog, f"label.property.{suffix}", fallback)


def format_battle_buff_value(property_id: str, value: float) -> str:
    if property_id in _PERCENT_PROPERTIES:
        return f"{value * 100:+g}%"
    return f"{value:+,.3f}".rstrip("0").rstrip(".")


def _raw_modifier_lines(
    interval: BattleInferredBuffInterval,
    decision: BattleBuffProjectionDecision,
    *,
    text_catalog: TextCatalog | None = None,
) -> tuple[str, ...]:
    lines = []
    applied_ids = set(decision.applied_property_ids)
    for modifier in interval.modifiers:
        property_id = normalize_battle_buff_property_id(modifier.property_id)
        label = battle_buff_property_label(property_id, text_catalog=text_catalog)
        if modifier.magnitude_value is not None:
            total = float(modifier.magnitude_value) * max(1, interval.stacks)
            value = format_battle_buff_value(property_id, total)
            if interval.stacks > 1:
                value += _text(
                    text_catalog,
                    "modifier.per_stack",
                    "（{per_stack} × {stacks} 层）",
                    per_stack=format_battle_buff_value(
                        property_id, float(modifier.magnitude_value)
                    ),
                    stacks=interval.stacks,
                )
        elif modifier.calculation_asset_path:
            value = _text(
                text_catalog,
                "modifier.calculation_asset",
                "Calculation：{asset}",
                asset=modifier.calculation_asset_path.rsplit("/", 1)[-1],
            )
        else:
            value = _text(text_catalog, "modifier.value_unresolved", "数值尚未解析")
        if property_id in applied_ids:
            usage = _text(text_catalog, "modifier.usage_applied", "已投影到属性值")
        else:
            usage = _text(text_catalog, "modifier.usage_not_applied", "未投影到属性值")
        lines.append(
            _text(
                text_catalog,
                "modifier.line",
                "  - {label} {value}（{property}，{usage}，数值置信度 {confidence}）",
                label=label,
                value=value,
                property=property_id,
                usage=usage,
                confidence=modifier.value_confidence,
            )
        )
    return tuple(lines) or (
        _text(text_catalog, "modifier.none", "  - 没有提取到属性修正。"),
    )


class BattleHitBuffExplanationService:
    """Describe active intervals and their exact per-hit projection decision."""

    @classmethod
    def build(
        cls,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        *, projection=None, allow_projection_fallback: bool = True,
        text_catalog: TextCatalog | None = None,
    ) -> str:
        if projection is None and allow_projection_fallback:
            projection = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
        if projection is None:
            return _text(
                text_catalog,
                "unavailable",
                "本击未生成 Buff 分析详情；请先加载对应时段的逐击分析。",
            )
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
            _text(
                text_catalog,
                "header.timing",
                "命中时间：{time}    目标：{target}",
                time=_time(hit.relative_time_us),
                target=hit.target_name,
            ),
            _text(
                text_catalog,
                "header.projection",
                "命中时 Buff 分析：{count} 个    已投影 {applied} / 未采用 "
                "{not_applied} / 待确认 {unresolved}",
                count=len(decisions),
                applied=counts["applied"],
                not_applied=counts["not_applied"],
                unresolved=counts["unresolved"],
            ),
            _text(
                text_catalog,
                "note.decision_scope",
                "口径：采用分析核心返回的逐击判定；运行时观测、静态规则与推断须按各项依据区分。",
            ),
            _text(
                text_catalog,
                "note.evidence_scope",
                "状态证据与数值证据分别标注；观测到效果存在，不等于已证明它对本击的数值收益。",
            ),
            _text(
                text_catalog,
                "note.formula_consumption",
                "公式消费口径：已投影只表示进入逐击属性值；是否被当前伤害公式消费，"
                "以伤害公式列出的乘区和来源项为准。",
            ),
            "",
            _text(text_catalog, "section.projected", "【投影到逐击属性值的加成汇总】"),
        ]
        if projection.modifiers:
            for modifier in projection.modifiers:
                sources = "、".join(modifier.buff_names)
                lines.append(
                    _text(
                        text_catalog,
                        "projection.line",
                        "- {label} {value}（{property}，{scope}，置信度 {confidence}）\n"
                        "  来源：{sources}",
                        label=battle_buff_property_label(
                            modifier.property_id, text_catalog=text_catalog
                        ),
                        value=format_battle_buff_value(
                            modifier.property_id, modifier.additive_value
                        ),
                        property=modifier.property_id,
                        scope=_scope_label(
                            modifier.target_scope, text_catalog=text_catalog
                        ),
                        confidence=modifier.confidence,
                        sources=sources,
                    )
                )
        else:
            lines.append(
                _text(
                    text_catalog,
                    "projection.empty",
                    "- 没有 Buff 数值投影到本击属性值。",
                )
            )

        decision_by_status = {
            status: tuple(row for row in decisions if row.status == status)
            for status in _STATUS_LABELS
        }
        for status in _STATUS_LABELS:
            matching = decision_by_status[status]
            if not matching:
                continue
            lines.extend((
                "",
                _text(
                    text_catalog,
                    "section.status",
                    "【{title} Buff】",
                    title=_status_label(status, text_catalog=text_catalog),
                ),
            ))
            for decision in matching:
                interval = active_by_id[decision.interval_id]
                lines.append(
                    _text(
                        text_catalog,
                        "decision.line",
                        "- {name}（公式采用区间层数 ×{stacks}）（来源角色：{source}；"
                        "作用对象：{scope}；区间：{start}—{end}；"
                        "状态 {state} / 数值 {value}）",
                        name=interval.buff_name,
                        stacks=interval.stacks,
                        source=interval.source_character_name,
                        scope=_scope_label(
                            interval.target_scope, text_catalog=text_catalog
                        ),
                        start=_time(interval.start_us),
                        end=_time(interval.end_us),
                        state=(
                            decision.state_confidence
                            or interval.state_confidence
                        ),
                        value=interval.value_confidence,
                    )
                )
                if decision.observed_stacks is not None:
                    lines.append(
                        _text(
                            text_catalog,
                            "decision.observed_stacks",
                            "  回调采样层数：{stacks}；该时点不等于执行前，"
                            "不据此替换公式采用层数。",
                            stacks=decision.observed_stacks,
                        )
                    )
                lines.extend(
                    _raw_modifier_lines(
                        interval, decision, text_catalog=text_catalog
                    )
                )
                if decision.reasons:
                    lines.append(
                        _text(
                            text_catalog,
                            "decision.reasons",
                            "  判定：{reasons}",
                            reasons="；".join(decision.reasons),
                        )
                    )
                lines.extend((
                    _text(
                        text_catalog,
                        "decision.id",
                        "  ID：{value}",
                        value=interval.source_effect_definition_id,
                    ),
                    _text(
                        text_catalog,
                        "decision.asset",
                        "  资产：{value}",
                        value=interval.buff_asset_path,
                    ),
                    _text(
                        text_catalog,
                        "decision.basis",
                        "  状态依据：{value}",
                        value=interval.inference_basis,
                    ),
                ))
        if not decisions:
            lines.extend((
                "",
                _text(text_catalog, "section.evidence", "【证据边界】"),
                _text(
                    text_catalog,
                    "evidence.none",
                    "- 本击没有匹配到命中时有效的推算 Buff 区间。",
                ),
            ))
        return "\n".join(lines)
