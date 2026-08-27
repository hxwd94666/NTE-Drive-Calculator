# 以真实逐击为锚点，在逐击动态 Buff 面板上计算属性边际。
"""Battle-report marginal calculations with safe per-hit Buff projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from src.domain.battle_counterfactual import BattleMarginalResult
from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleDamageQuantification,
    BattleQuantificationGap,
    QuantificationStatus,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
)
from src.domain.official_role import ROLE_PANEL_MARGINAL_UNITS
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)


_ELEMENT_PROPERTIES = {
    "DamageUpChaosBase",
    "DamageUpCosmosBase",
    "DamageUpIncantationBase",
    "DamageUpLakshanaBase",
    "DamageUpNatureBase",
    "DamageUpPsycheBase",
    "DamageUpPsychicallyBase",
}
_ATTRIBUTE_ELEMENT_PROPERTY = {
    "chaos": "DamageUpChaosBase",
    "cosmos": "DamageUpCosmosBase",
    "incantation": "DamageUpIncantationBase",
    "lakshana": "DamageUpLakshanaBase",
    "nature": "DamageUpNatureBase",
    "psyche": "DamageUpPsycheBase",
    "psychically": "DamageUpPsychicallyBase",
}
_MARGINAL_LABELS = {
    "CritBase": "暴击率",
    "CritDamageBase": "暴击伤害",
    "DamageUpGeneralBase": "通用伤害增强",
    "AtkUp": "攻击力提升",
    "AtkAdd": "固定攻击力",
    "HPMaxUp": "生命值提升",
    "HPMaxAdd": "固定生命值",
    "DefUp": "防御力提升",
    "DefAdd": "固定防御力",
    "DefIgnore": "防御忽略",
    "ElementDamage": "属性伤害增强",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}
_MARGINAL_UNITS = {
    **ROLE_PANEL_MARGINAL_UNITS,
    "DefIgnore": 0.01,
    "MagBase": 6.0,
    "UnbalIntensityBase": 6.0,
}
_DAMAGE_PENETRATION_PROPERTY = {
    "chaos": "DamagePenetrateChaos",
    "cosmos": "DamagePenetrateCosmos",
    "incantation": "DamagePenetrateIncantation",
    "lakshana": "DamagePenetrateLakshana",
    "nature": "DamagePenetrateNature",
    "psyche": "DamagePenetratePsyche",
    "psychically": "DamagePenetratePsychically",
}


class BattleMarginalCalculationService:
    """Calculate role margins without mistaking inferred Buffs for raw facts."""

    @staticmethod
    def default_units(baseline: BattleCharacterBaseline) -> dict[str, float]:
        present = {row.property_id for row in baseline.stats}
        result = {
            property_id: float(unit)
            for property_id, unit in _MARGINAL_UNITS.items()
            if property_id in present
        }
        element = next((item for item in present if item in _ELEMENT_PROPERTIES), None)
        if element is not None:
            result[element] = float(_MARGINAL_UNITS["ElementDamage"])
        for property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            if property_id in present:
                result[property_id] = 0.01
        return result

    @classmethod
    def calculate(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        character_id: int,
        edited_values: Mapping[str, float],
        units: Mapping[str, float],
    ) -> tuple[BattleMarginalResult, ...]:
        baseline = next(
            (row for row in analysis.baselines if row.character_id == character_id),
            None,
        )
        if baseline is None:
            return ()
        frozen = {row.property_id: row.value for row in baseline.stats}
        edited = {
            **frozen,
            **{str(key): float(value) for key, value in edited_values.items()},
        }
        outgoing_hits = tuple(
            hit for hit in analysis.hits if hit.direction == "outgoing"
        )
        role_hits = tuple(
            hit
            for hit in outgoing_hits
            if hit.character_id == character_id
        )
        replays = {row.event_id: row for row in analysis.hit_replays}
        projections = {
            hit.event_id: BattleBuffAttributeProjectionService.project_hit(
                hit,
                analysis.buff_intervals,
            )
            for hit in role_hits
        }
        target_conditions = {
            hit.event_id: BattleTargetInstanceMappingService.analysis_for_hit(
                analysis,
                hit,
            ).target_condition
            for hit in role_hits
        }
        applied_intervals = {
            interval_id
            for projection in projections.values()
            for interval_id in projection.applied_interval_ids
        }
        excluded_intervals = {
            interval_id
            for projection in projections.values()
            for interval_id in projection.excluded_interval_ids
        }
        derived_damage = next(
            (
                role.max_hp_reduction_damage
                for role in analysis.roles
                if role.character_id == character_id
            ),
            0.0,
        )
        observed_role_damage = sum(hit.damage for hit in role_hits) + derived_damage
        comparison = analysis.build_counterfactual
        comparison_hits = {
            row.event_id: cls._counterfactual_projection(
                row,
                fallback=next(
                    (
                        float(hit.damage)
                        for hit in role_hits
                        if hit.event_id == row.event_id
                    ),
                    0.0,
                ),
            )
            for row in (() if comparison is None else comparison.hits)
        }

        def anchor_damage(hit: BattleAnalysisHit) -> float:
            return comparison_hits.get(hit.event_id, max(0.0, float(hit.damage)))

        comparison_role = next(
            (
                row
                for row in (() if comparison is None else comparison.roles)
                if row.character_id == character_id
            ),
            None,
        )
        role_damage = (
            observed_role_damage
            if comparison_role is None
            else cls._counterfactual_projection(
                comparison_role,
                fallback=observed_role_damage,
            )
        )
        team_damage = (
            max(0.0, float(analysis.effective_damage))
            if comparison is None
            else cls._counterfactual_projection(
                comparison,
                fallback=max(0.0, float(analysis.effective_damage)),
            )
        )
        results = []
        for property_id, raw_unit in units.items():
            unit = float(raw_unit)
            changed = dict(edited)
            changed[property_id] = changed.get(property_id, 0.0) + unit
            edited_baseline = cls._baseline_with_values(baseline, edited)
            changed_baseline = cls._baseline_with_values(baseline, changed)
            relevant_hits = tuple(
                hit
                for hit in role_hits
                if hit.classification in {"direct", "direct_follow_up", "weave"}
                and cls._supports(
                    property_id,
                    hit,
                    replay=replays.get(hit.event_id),
                    character_id=character_id,
                )
            )
            hit_ratios = {
                hit.event_id: BattleHitCounterfactualRatioService.compare(
                    hit=hit,
                    original_baseline=edited_baseline,
                    candidate_baseline=changed_baseline,
                    original_projection=projections[hit.event_id],
                    candidate_projection=projections[hit.event_id],
                    original_replay=replays.get(hit.event_id),
                    target_condition=target_conditions[hit.event_id],
                )
                for hit in relevant_hits
            }
            topple_hits = tuple(
                hit
                for hit in outgoing_hits
                if property_id == "UnbalIntensityBase"
                and cls._topple_ratio(
                    replays.get(hit.event_id),
                    character_id=character_id,
                    unit=0.0,
                ) is not None
            )
            topple_ratios = {
                hit.event_id: ratio
                for hit in topple_hits
                if (
                    ratio := cls._topple_ratio(
                        replays.get(hit.event_id),
                        character_id=character_id,
                        unit=unit,
                    )
                ) is not None
            }
            quantification, known_increment = cls._quantification(
                role_damage=role_damage,
                relevant_hits=relevant_hits,
                hit_ratios=hit_ratios,
                topple_hits=topple_hits,
                topple_ratios=topple_ratios,
                anchor_damage=anchor_damage,
            )
            baseline_damage = quantification.basis_damage
            known_projection_damage = (
                None
                if known_increment is None
                else baseline_damage + known_increment
            )
            quantified_role_gain = (
                None
                if known_increment is None
                else (
                    known_increment / baseline_damage * 100.0
                    if baseline_damage
                    else 0.0
                )
            )
            quantified_team_gain = (
                None
                if known_increment is None
                else known_increment / team_damage * 100.0 if team_damage else 0.0
            )
            full_role_gain = (
                quantified_role_gain
                if quantification.status in {"complete", "not_applicable"}
                else None
            )
            full_team_gain = (
                quantified_team_gain
                if quantification.status in {"complete", "not_applicable"}
                else None
            )
            percent = property_id not in {
                "AtkAdd", "HPMaxAdd", "DefAdd", "MagBase", "UnbalIntensityBase",
            }
            results.append(BattleMarginalResult(
                property_id=property_id,
                label=cls._label(property_id, baseline),
                unit=unit,
                is_percent=percent,
                baseline_damage=baseline_damage,
                known_projection_damage=known_projection_damage,
                quantified_role_gain_percent=quantified_role_gain,
                quantified_team_gain_percent=quantified_team_gain,
                full_role_gain_percent=full_role_gain,
                full_team_gain_percent=full_team_gain,
                damage_share_percent=(
                    min(100.0, baseline_damage / team_damage * 100.0)
                    if team_damage else 0.0
                ),
                quantification=quantification,
                assumption=cls._assumption(
                    property_id,
                    quantification.status,
                    applied_count=len(applied_intervals),
                    excluded_count=len(excluded_intervals),
                    critical_policies=tuple(
                        cls._critical_policy(replays.get(hit.event_id))
                        for hit in role_hits
                        if hit.classification
                        in {"direct", "direct_follow_up", "weave"}
                    ),
                ),
            ))
        return tuple(sorted(
            results,
            key=lambda row: (
                row.quantified_role_gain_percent
                if row.quantified_role_gain_percent is not None
                else float("-inf")
            ),
            reverse=True,
        ))

    @staticmethod
    def _counterfactual_projection(row: object, *, fallback: float) -> float:
        """Use only complete or known-component projections, never heuristics."""

        for field_name in ("candidate_damage", "known_projection_damage"):
            value = getattr(row, field_name, None)
            if value is not None:
                return max(0.0, float(value))
        return max(0.0, float(fallback))

    @staticmethod
    def _baseline_with_values(
        baseline: BattleCharacterBaseline,
        values: Mapping[str, float],
    ) -> BattleCharacterBaseline:
        existing = {row.property_id: row for row in baseline.stats}
        stats = tuple(
            replace(row, value=float(values.get(row.property_id, row.value)))
            for row in baseline.stats
        )
        additions = tuple(
            BattleCharacterStat(
                property_id=property_id,
                label=property_id,
                value=float(value),
                is_percent=property_id not in {
                    "AtkBase", "AtkAdd", "HPMaxBase", "HPMaxAdd",
                    "DefBase", "DefAdd", "MagBase", "UnbalIntensityBase",
                },
            )
            for property_id, value in sorted(values.items())
            if property_id not in existing
        )
        return replace(baseline, stats=tuple((*stats, *additions)))

    @classmethod
    def _quantification(
        cls,
        *,
        role_damage: float,
        relevant_hits: Sequence[BattleAnalysisHit],
        hit_ratios: Mapping[str, BattleCounterfactualRatio],
        topple_hits: Sequence[BattleAnalysisHit],
        topple_ratios: Mapping[str, float],
        anchor_damage: Callable[[BattleAnalysisHit], float],
    ) -> tuple[BattleDamageQuantification, float | None]:
        fully_quantified_damage = 0.0
        partially_quantified_damage = 0.0
        unavailable_damage = 0.0
        known_increment = 0.0
        statuses: list[QuantificationStatus] = []
        gaps: list[BattleQuantificationGap] = []
        relevant_event_ids: set[str] = set()

        for hit in relevant_hits:
            ratio = hit_ratios[hit.event_id]
            damage = anchor_damage(hit)
            relevant_event_ids.add(hit.event_id)
            statuses.append(ratio.status)
            gaps.extend(ratio.gaps)
            if ratio.status == "complete":
                fully_quantified_damage += damage
            elif ratio.status == "partial":
                partially_quantified_damage += damage
            elif ratio.status == "unavailable":
                unavailable_damage += damage
            if ratio.quantified_ratio is not None and ratio.status in {
                "complete", "partial",
            }:
                known_increment += damage * (ratio.quantified_ratio - 1.0)

        for hit in topple_hits:
            ratio = topple_ratios.get(hit.event_id)
            if ratio is None or hit.event_id in relevant_event_ids:
                continue
            damage = anchor_damage(hit)
            relevant_event_ids.add(hit.event_id)
            statuses.append("complete")
            fully_quantified_damage += damage
            known_increment += damage * (ratio - 1.0)

        quantified_basis = (
            fully_quantified_damage
            + partially_quantified_damage
            + unavailable_damage
        )
        basis_damage = max(0.0, float(role_damage), quantified_basis)
        proven_unchanged_damage = max(0.0, basis_damage - quantified_basis)
        active_statuses = tuple(
            status for status in statuses if status != "not_applicable"
        )
        has_known = any(
            status in {"complete", "partial"} for status in active_statuses
        )
        has_unavailable = "unavailable" in active_statuses
        has_partial = "partial" in active_statuses
        if not active_statuses:
            status: QuantificationStatus = "not_applicable"
            quantified_increment: float | None = 0.0
        elif has_partial or (has_known and has_unavailable):
            status = "partial"
            quantified_increment = known_increment
        elif has_unavailable:
            status = "unavailable"
            quantified_increment = None
        else:
            status = "complete"
            quantified_increment = known_increment
        unique_gaps = tuple(dict.fromkeys(gaps))
        return (
            BattleDamageQuantification(
                status=status,
                basis_damage=basis_damage,
                fully_quantified_damage=fully_quantified_damage,
                partially_quantified_damage=partially_quantified_damage,
                unavailable_damage=unavailable_damage,
                proven_unchanged_damage=proven_unchanged_damage,
                quantified_increment=quantified_increment,
                gaps=unique_gaps,
            ),
            quantified_increment,
        )

    @staticmethod
    def _supports(
        property_id: str,
        hit: BattleAnalysisHit,
        *,
        replay: BattleHitReplayResult | None,
        character_id: int,
    ) -> bool:
        if property_id == "UnbalIntensityBase":
            return (
                BattleMarginalCalculationService._topple_ratio(
                    replay,
                    character_id=character_id,
                    unit=0.0,
                )
                is not None
            )
        if property_id in {"CritBase", "CritDamageBase"}:
            if replay is None and hit.classification == "weave":
                return False
            policy = BattleMarginalCalculationService._critical_policy(replay)
            if property_id == "CritBase":
                return policy in {"character", "unknown"}
            return policy in {"character", "fixed", "unknown"}
        if property_id == "MagBase":
            return hit.classification == "weave"
        if property_id == "DefIgnore":
            return hit.classification in {"direct", "direct_follow_up", "weave"}
        if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            expected_attribute = next(
                damage_type
                for damage_type, candidate in _DAMAGE_PENETRATION_PROPERTY.items()
                if candidate == property_id
            )
            return (
                hit.damage_attribute.casefold() == expected_attribute
                and hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        if property_id in _ELEMENT_PROPERTIES:
            return (
                _ATTRIBUTE_ELEMENT_PROPERTY.get(hit.damage_attribute.casefold())
                == property_id
                and hit.classification in {"direct", "direct_follow_up", "weave"}
            )
        return hit.classification in {"direct", "direct_follow_up", "weave"}

    @staticmethod
    def _critical_policy(
        replay: BattleHitReplayResult | None,
    ) -> str:
        # Direct unit callers without replay evidence keep the historical
        # character-expectation fallback. Real battle-detail loads always carry
        # the structured policy produced by the shared hit replay.
        if replay is None:
            return "character"
        policy = str(getattr(replay, "critical_policy", "unknown"))
        return policy if policy in {"character", "fixed", "disabled"} else "unknown"

    @staticmethod
    def _topple_ratio(
        replay: BattleHitReplayResult | None,
        *,
        character_id: int,
        unit: float,
    ) -> float | None:
        """Scale one retained team-topple cell without rebuilding its formula."""

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
        if not any(
            term.property_id == "UnbalIntensityBase" for term in source.terms
        ):
            return None

        def term_total(*property_ids: str) -> float:
            accepted = set(property_ids)
            return sum(
                float(term.value)
                for term in source.terms
                if term.property_id in accepted
            )

        base = max(0.0, term_total("UnbalIntensityBase"))
        up = term_total("UnbalIntensityUp")
        add = term_total("UnbalIntensityAdd")
        damage_up = term_total("UnbalDamageUp", "ToppleDamageUp")
        strength = base * (1.0 + up) + add
        changed_strength = max(0.0, base + unit) * (1.0 + up) + add
        current_zone = 1.0 + strength / 300.0 + damage_up
        changed_zone = 1.0 + changed_strength / 300.0 + damage_up
        if current_zone <= 0.0 or changed_zone < 0.0:
            return None
        changed_source = max(0.0, float(source.value)) * changed_zone / current_zone
        changed_total = total - max(0.0, float(source.value)) + changed_source
        return changed_total / total

    @staticmethod
    def _label(property_id: str, baseline: BattleCharacterBaseline) -> str:
        if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
            return next(
                (row.label for row in baseline.stats if row.property_id == property_id),
                "属性抗性穿透",
            )
        if property_id in _ELEMENT_PROPERTIES:
            return next(
                (row.label for row in baseline.stats if row.property_id == property_id),
                "属性伤害增强",
            )
        return _MARGINAL_LABELS.get(property_id, property_id)

    @staticmethod
    def _assumption(
        property_id: str,
        status: QuantificationStatus,
        *,
        applied_count: int,
        excluded_count: int,
        critical_policies: tuple[str, ...],
    ) -> str:
        if status == "unavailable":
            if property_id == "DefIgnore":
                return (
                    "当前相关逐击缺少可靠冻结敌方防御画像；"
                    "本项未量化，不表示没有收益。"
                )
            if property_id in _DAMAGE_PENETRATION_PROPERTY.values():
                return (
                    "当前相关逐击缺少可靠冻结敌方分属性抗性画像；"
                    "本项未量化，不表示没有收益。"
                )
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
            basis = f"按逐击 {policies} 暴击策略使用期望伤害，不拟合本场暴击结果。"
        elif property_id == "MagBase":
            basis = "仅重放已识别的覆纹追加攻击，复用统一环合强度公式。"
        elif property_id == "UnbalIntensityBase":
            return (
                "复用团队倾陷逐角色贡献，单位只改变当前角色倾陷强度格；"
                "命中时倾陷 Buff 已保留在该角色公式因子中。"
            )
        else:
            basis = "以真实逐击伤害为锚点，仅替换角色属性相关乘区。"
        return (
            f"{basis}已将 {applied_count} 个动态 Buff 区间按击投影；"
            f"{excluded_count} 个区间因常驻重复或证据不足未进入数值。"
        )
