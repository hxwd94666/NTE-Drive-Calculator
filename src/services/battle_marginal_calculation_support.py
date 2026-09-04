# 固定轴属性边际的候选目录、量化桶与展示说明。
"""Pure support functions for battle marginal calculations."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
    QuantificationStatus,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleHitReplayResult,
)
from src.domain.official_role import ROLE_PANEL_MARGINAL_UNITS
from src.services.battle_fixed_critical_ratio_service import (
    continuous_direct_attribute,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_marginal_formula_scope import (
    formula_panel_character_id,
    property_owner_matches,
)
from src.services.battle_buff_counterfactual_projection_support import (
    VitalProjection,
)


ELEMENT_PROPERTIES = {
    "DamageUpChaosBase", "DamageUpCosmosBase", "DamageUpIncantationBase",
    "DamageUpLakshanaBase", "DamageUpNatureBase", "DamageUpPsycheBase",
    "DamageUpPsychicallyBase",
}
ATTRIBUTE_ELEMENT_PROPERTY = {
    "chaos": "DamageUpChaosBase", "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase", "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
MARGINAL_LABELS = {
    "CritBase": "暴击率", "CritDamageBase": "暴击伤害",
    "DamageUpGeneralBase": "通用伤害增强", "AtkUp": "攻击力提升",
    "AtkAdd": "固定攻击力", "HPMaxUp": "生命值提升",
    "HPMaxAdd": "固定生命值", "DefUp": "防御力提升",
    "DefAdd": "固定防御力", "DefIgnore": "防御忽略",
    "ElementDamage": "属性伤害增强", "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
MARGINAL_UNITS = {
    **ROLE_PANEL_MARGINAL_UNITS,
    "DefIgnore": 0.01,
    "MagBase": 6.0,
    "UnbalIntensityBase": 6.0,
}
DRIVE_SUBSTAT_PROPERTIES = {
    "暴击率%": "CritBase",
    "暴击伤害%": "CritDamageBase",
    "伤害增加%": "DamageUpGeneralBase",
    "攻击力%": "AtkUp",
    "攻击力": "AtkAdd",
    "防御力": "DefAdd",
    "防御力%": "DefUp",
    "生命值%": "HPMaxUp",
    "生命值": "HPMaxAdd",
    "环合强度": "MagBase",
    "倾陷强度": "UnbalIntensityBase",
}
DAMAGE_PENETRATION_PROPERTY = {
    "chaos": "DamagePenetrateChaos", "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature", "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}
WEAVE_SOURCE_PROPERTIES = {
    "AtkUp", "AtkAdd", "HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd",
    "CritBase", "CritDamageBase", "DamageUpGeneralBase", "DefIgnore",
    *ELEMENT_PROPERTIES,
    *DAMAGE_PENETRATION_PROPERTY.values(),
}


def drive_substat_marginal_units(
    gold_base_values: Mapping[str, float] | None,
) -> dict[str, float]:
    """Return only rollable drive sub-stats in the canonical catalog order."""

    values = gold_base_values or {}
    units: dict[str, float] = {}
    for stat_name, property_id in DRIVE_SUBSTAT_PROPERTIES.items():
        if values and stat_name not in values:
            continue
        configured = values.get(stat_name)
        if configured is None:
            configured = MARGINAL_UNITS[property_id]
            units[property_id] = float(configured)
        else:
            units[property_id] = float(configured) / (
                100.0 if stat_name.endswith("%") else 1.0
            )
    return units


def default_marginal_units(
    baseline: BattleCharacterBaseline,
    *,
    hits: Sequence[BattleAnalysisHit] = (),
    replays: Mapping[str, BattleHitReplayResult] | None = None,
    topple_ratio: Callable[..., float | None],
) -> dict[str, float]:
    present = {row.property_id for row in baseline.stats}
    replay_map = {} if replays is None else replays
    result = {
        property_id: float(unit)
        for property_id, unit in MARGINAL_UNITS.items()
        if property_id in present
        and property_id not in ELEMENT_PROPERTIES
        and property_id not in DAMAGE_PENETRATION_PROPERTY.values()
    }
    formal_attributes = {
        (
            "nature"
            if BattleHitCounterfactualRatioService.is_kuhara_formula_hit(hit)
            else str(getattr(
                replay_map.get(hit.event_id), "formula_damage_attribute", ""
            ) or "").casefold()
            or continuous_direct_attribute(hit)
            or hit.damage_attribute.casefold()
        )
        for hit in hits
        if formula_panel_character_id(hit, replay_map.get(hit.event_id))
        == baseline.character_id
        and hit.direction == "outgoing"
    }
    if not formal_attributes:
        formal_attributes = {
            attribute
            for attribute, property_id in ATTRIBUTE_ELEMENT_PROPERTY.items()
            if property_id in present
        }
    for attribute in formal_attributes:
        element_property = ATTRIBUTE_ELEMENT_PROPERTY.get(attribute)
        penetration_property = DAMAGE_PENETRATION_PROPERTY.get(attribute)
        if element_property is not None:
            result[element_property] = float(MARGINAL_UNITS["ElementDamage"])
        if penetration_property is not None:
            result[penetration_property] = 0.01
    if any(
        topple_ratio(
            replay_map.get(hit.event_id),
            character_id=baseline.character_id,
            unit=0.0,
        ) is not None
        for hit in hits
    ):
        result["UnbalIntensityBase"] = float(MARGINAL_UNITS["UnbalIntensityBase"])
    if any(
        BattleHitCounterfactualRatioService.supports_ring_strength(
            hit,
            replay_map.get(hit.event_id),
        )
        and property_owner_matches(
            "MagBase",
            hit,
            hits,
            replay_map,
            character_id=baseline.character_id,
            weave_source_properties=WEAVE_SOURCE_PROPERTIES,
        )
        for hit in hits
    ):
        result["MagBase"] = float(MARGINAL_UNITS["MagBase"])
    return result


def topple_character_contribution(
    replay: BattleHitReplayResult | None,
    *,
    character_id: int,
    team_topple_damage: float,
) -> float | None:
    if replay is None or replay.critical_state == "unreplayable":
        return None
    contributions = tuple(
        factor
        for factor in replay.factors
        if factor.factor_id.startswith("topple_character:")
    )
    source = next(
        (
            factor
            for factor in contributions
            if factor.factor_id == f"topple_character:{character_id}"
        ),
        None,
    )
    total = sum(max(0.0, float(factor.value)) for factor in contributions)
    if source is None or total <= 0.0:
        return None
    return max(0.0, float(team_topple_damage)) * (
        max(0.0, float(source.value)) / total
    )


def quantify_marginal(
    *,
    role_damage: float,
    relevant_hits: Sequence[BattleAnalysisHit],
    hit_ratios: Mapping[str, BattleCounterfactualRatio],
    vital_projections: Sequence[VitalProjection],
    topple_hits: Sequence[BattleAnalysisHit],
    topple_ratios: Mapping[str, float],
    replays: Mapping[str, BattleHitReplayResult],
    anchor_damage: Callable[[BattleAnalysisHit], float],
    anchor_quantification: Callable[
        [BattleAnalysisHit], BattleCounterfactualRatio | None
    ],
    character_id: int,
) -> tuple[BattleDamageQuantification, float | None]:
    buckets = {"complete": 0.0, "partial": 0.0, "unavailable": 0.0}
    known_increment = 0.0
    statuses: list[QuantificationStatus] = []
    gaps: list[BattleQuantificationGap] = []
    relevant_event_ids: set[str] = set()
    for hit in relevant_hits:
        ratio = hit_ratios[hit.event_id]
        damage = anchor_damage(hit)
        if damage <= 0.0:
            continue
        relevant_event_ids.add(hit.event_id)
        statuses.append(ratio.status)
        gaps.extend(ratio.gaps)
        if ratio.status in buckets:
            buckets[ratio.status] += damage
        if ratio.quantified_ratio is not None and ratio.status in {"complete", "partial"}:
            known_increment += damage * (ratio.quantified_ratio - 1.0)
    for row in vital_projections:
        if row.baseline_damage <= 0.0:
            continue
        statuses.append(row.status)
        gaps.extend(row.gaps)
        if row.status in buckets:
            buckets[row.status] += row.baseline_damage
        if row.status in {"complete", "partial"}:
            known_increment += row.predicted_damage - row.baseline_damage
    for hit in topple_hits:
        ratio = topple_ratios.get(hit.event_id)
        if ratio is None or hit.event_id in relevant_event_ids:
            continue
        team_damage = anchor_damage(hit)
        contribution = topple_character_contribution(
            replays.get(hit.event_id),
            character_id=character_id,
            team_topple_damage=team_damage,
        )
        if contribution is None:
            continue
        if contribution <= 0.0:
            continue
        relevant_event_ids.add(hit.event_id)
        anchor = anchor_quantification(hit)
        anchor_status: QuantificationStatus = (
            "complete" if anchor is None else anchor.status
        )
        statuses.append(anchor_status)
        if anchor is not None:
            gaps.extend(anchor.gaps)
        # Team topple is emitted as one observed hit, while its structured
        # factors assign a separate contribution to every participating role.
        # The role denominator already uses that same contribution split, so
        # the quantified bucket must not depend on the packet's raw owner.
        if anchor_status in buckets:
            buckets[anchor_status] += contribution
        if anchor_status in {"complete", "partial"}:
            known_increment += team_damage * (ratio - 1.0)
    quantified_basis = sum(buckets.values())
    basis_damage = max(0.0, float(role_damage))
    tolerance = max(1e-6, basis_damage * 1e-9)
    if quantified_basis > basis_damage + tolerance:
        raise ValueError(
            "marginal quantified buckets exceed current role damage: "
            f"{quantified_basis} > {basis_damage}"
        )
    proven_unchanged = max(0.0, basis_damage - quantified_basis)
    active = tuple(status for status in statuses if status != "not_applicable")
    has_known = any(status in {"complete", "partial"} for status in active)
    if not active:
        status: QuantificationStatus = "not_applicable"
        increment: float | None = 0.0
    elif "partial" in active or (has_known and "unavailable" in active):
        status, increment = "partial", known_increment
    elif "unavailable" in active:
        status, increment = "unavailable", None
    else:
        status, increment = "complete", known_increment
    return BattleDamageQuantification(
        status=status,
        basis_damage=basis_damage,
        fully_quantified_damage=buckets["complete"],
        partially_quantified_damage=buckets["partial"],
        unavailable_damage=buckets["unavailable"],
        proven_unchanged_damage=proven_unchanged,
        quantified_increment=increment,
        gaps=tuple(dict.fromkeys(gaps)),
    ), increment


def marginal_assumption(
    property_id: str,
    status: QuantificationStatus,
    *,
    applied_count: int,
    excluded_count: int,
    critical_policies: tuple[str, ...],
) -> str:
    if status == "unavailable":
        if property_id == "DefIgnore":
            return "当前相关逐击缺少可靠冻结敌方防御画像；本项未量化，不表示没有收益。"
        if property_id in DAMAGE_PENETRATION_PROPERTY.values():
            return "当前相关逐击缺少可靠冻结敌方分属性抗性画像；本项未量化，不表示没有收益。"
        if property_id in {"CritBase", "CritDamageBase"}:
            policies = "/".join(sorted(set(critical_policies))) or "unknown"
            return (
                f"当前相关逐击暴击策略为 {policies}，变化缺少正式策略；"
                "未知不会退回本场暴击拟合，也不会显示为零收益。"
            )
        return (
            "当前相关变化缺少必要公式输入，本项未量化；"
            f"已将 {applied_count} 个动态 Buff 区间按击投影，"
            f"{excluded_count} 个区间未进入数值。"
        )
    if status == "partial":
        return (
            "只计算具备冻结公式输入的逐击分量；其余逐击仍保留原始事实，"
            "该数值不代表完整收益或收益下限。"
        )
    if status == "not_applicable":
        if property_id in {"CritBase", "CritDamageBase"}:
            policies = "/".join(sorted(set(critical_policies))) or "unknown"
            return f"当前相关逐击暴击策略为 {policies}，已证明该单位变化不作用。"
        return "已证明该属性单位变化不作用于当前相关逐击，收益精确为零。"
    if property_id in {"CritBase", "CritDamageBase"}:
        policies = "/".join(sorted(set(critical_policies))) or "character"
        basis = f"按逐击 {policies} 暴击策略消费已知分支或正式期望，不拟合暴击结果。"
    elif property_id == "MagBase":
        basis = (
            "仅重放已保存结构化环合公式的覆纹、创生、浊燃与黯星逐击；"
            "浸染官方逐击公式已确认，但生产固定轴消费者尚未接入。"
        )
    elif property_id == "UnbalIntensityBase":
        return "复用团队倾陷逐角色贡献，单位只改变当前角色倾陷强度格；命中时 Buff 保留在该角色因子中。"
    else:
        basis = "以真实逐击伤害为锚点，仅替换角色属性相关乘区。"
    return (
        f"{basis}已将 {applied_count} 个动态 Buff 区间按击投影；"
        f"{excluded_count} 个区间因常驻重复或证据不足未进入数值。"
    )


def formula_context_assumption(
    hits: Sequence[BattleAnalysisHit],
    replays: Mapping[str, BattleHitReplayResult],
) -> str:
    confidences = tuple(sorted({
        replay.formula_context_confidence
        for hit in hits
        if (replay := replays.get(hit.event_id)) is not None
        and replay.formula_context_kind
        and replay.formula_context_confidence
    }))
    if not confidences:
        return ""
    return (
        f" 其中包含公式身份推论（置信度：{'/'.join(confidences)}）；"
        "逐击数值可重放不会提高该身份推论的置信度。"
    )
