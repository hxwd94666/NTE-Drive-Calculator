# 以现有 Buff 反事实契约展示创生生命周期、空间与资源被动。
"""Conservative fixed-axis evaluations for creation lifecycle passives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.domain.battle_buff_counterfactual import (
    BattleBuffCounterfactualResult,
    BattleDamageCoverage,
)
from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
    EnabledCharacterPassive,
)
from src.services.battle_creation_passive_beneficiary_service import (
    BattleCreationPassiveBeneficiaryService,
)
from src.services.battle_creation_passive_result_support import (
    build_creation_passive_result,
)


CREATION_PASSIVE_EVALUATION_VERSION = "battle-creation-passive-evaluation-v4"

_SUPPORTED_ADAPTERS = frozenset({
    "creation-volley",
    "creation-radius",
    "creation-time-stop",
    "creation-cap",
    "edgar-charge-reaction",
})


def _hit_damage_coverage(
    all_hits: Sequence[BattleAnalysisHit],
    covered_hits: Sequence[BattleAnalysisHit],
    unresolved_hits: Sequence[BattleAnalysisHit] = (),
) -> BattleDamageCoverage:
    basis = sum(
        max(0.0, float(hit.damage))
        for hit in all_hits
        if hit.direction == "outgoing"
    )
    covered_ids = {hit.event_id for hit in covered_hits}
    covered = min(basis, sum(
        max(0.0, float(hit.damage)) for hit in covered_hits
    ))
    unresolved = min(
        max(0.0, basis - covered),
        sum(
            max(0.0, float(hit.damage))
            for hit in unresolved_hits
            if hit.event_id not in covered_ids
        ),
    )
    return BattleDamageCoverage(basis, covered, unresolved)


_CREATION_EFFECT_IDS = frozenset({
    "ge_actorreaction_1_damage",
    "ge_actorreaction_1_1019_damage",
})
_CREATION_LABELS = frozenset({
    "创生",
    "创生花",
    "blossom damage",
    "replica vita pistil",
})


@dataclass(frozen=True, slots=True)
class BattleCreationPassiveAttribution:
    """Events a dedicated lifecycle model proved would disappear."""

    adapter_id: str
    event_ids: tuple[str, ...]
    complete: bool
    evidence_basis: str

    def __post_init__(self) -> None:
        if self.adapter_id not in _SUPPORTED_ADAPTERS:
            raise ValueError(f"unsupported creation passive adapter: {self.adapter_id}")
        if len(set(self.event_ids)) != len(self.event_ids):
            raise ValueError("event_ids must be unique")
        if not self.evidence_basis.strip():
            raise ValueError("evidence_basis is required")


@dataclass(frozen=True, slots=True)
class BattleCreationPassiveEvidence:
    """Capabilities frozen before evaluating lifecycle counterfactuals."""

    single_target_confirmed: bool = False
    time_stop_axis_complete: bool = False
    target_positions_complete: bool = False
    plant_identity_complete: bool = False
    volley_identity_complete: bool = False
    lifecycle_complete: bool = False
    creation_cap_order_complete: bool = False
    future_action_axis_complete: bool = False
    edgar_trigger_axis_complete: bool = False
    edgar_trigger_event_ids: tuple[str, ...] = ()
    attributions: tuple[BattleCreationPassiveAttribution, ...] = ()

    def __post_init__(self) -> None:
        adapter_ids = tuple(row.adapter_id for row in self.attributions)
        if len(set(adapter_ids)) != len(adapter_ids):
            raise ValueError("one attribution is allowed per adapter_id")
        if len(set(self.edgar_trigger_event_ids)) != len(
            self.edgar_trigger_event_ids
        ):
            raise ValueError("edgar_trigger_event_ids must be unique")


@dataclass(frozen=True, slots=True)
class _MechanicPolicy:
    gap_specs: tuple[tuple[str, str], ...]
    unavailable_explanation: str


_POLICIES = {
    "creation-volley": _MechanicPolicy(
        gap_specs=(
            (
                "creation_plant_identity_missing",
                "缺少创生株稳定身份，无法把花命中还原到各自株。",
            ),
            (
                "creation_volley_identity_missing",
                "缺少单次齐射身份，无法区分基础五朵与额外五朵。",
            ),
            (
                "creation_lifecycle_missing",
                "缺少生成、到期与覆盖顺序，无法重算一秒与两秒发射日程。",
            ),
        ),
        unavailable_explanation=(
            "娜娜莉 P1 改变队伍创生的花数量和发射间隔；没有正式创生 GE 时，"
            "不能把仅按展示标签识别的伤害纳入减半近似。"
        ),
    ),
    "creation-radius": _MechanicPolicy(
        gap_specs=((
            "target_position_missing",
            "缺少花命中点、基础范围与各目标位置，无法识别范围 +400 新增的命中。",
        ),),
        unavailable_explanation=(
            "薄荷 P1 只扩大创生花范围，不改单朵倍率；"
            "多目标缺位置时收益不可量化。"
        ),
    ),
    "creation-time-stop": _MechanicPolicy(
        gap_specs=(
            (
                "time_stop_axis_missing",
                "缺少完整时停区间轴，无法确认被动实际生效窗口。",
            ),
            (
                "creation_plant_identity_missing",
                "缺少株身份，无法连接时停内命中与同一株的后续命中。",
            ),
            (
                "creation_lifecycle_missing",
                "缺少时停期间剩余生命、发射日程与覆盖顺序。",
            ),
            (
                "future_action_axis_missing",
                "移除时停继续攻击会改变后续发射和新株覆盖，缺少未来动作轴。",
            ),
        ),
        unavailable_explanation=(
            "浔 P1 改变时停中的株生命周期；已观测时停命中不等于移除"
            "被动后必然消失的总伤害。"
        ),
    ),
    "creation-cap": _MechanicPolicy(
        gap_specs=(
            (
                "creation_plant_identity_missing",
                "缺少原株与九原 P1 额外株的稳定身份。",
            ),
            (
                "creation_cap_order_missing",
                "缺少三株/六株上限下的生成与覆盖顺序。",
            ),
            (
                "creation_lifecycle_missing",
                "缺少每株剩余生命和实际发射次数。",
            ),
        ),
        unavailable_explanation=(
            "九原 P1 既新增株又改变场上上限；缺株身份时不能把全部"
            "创生伤害粗暴乘二或除二。"
        ),
    ),
    "edgar-charge-reaction": _MechanicPolicy(
        gap_specs=(
            (
                "charge_trigger_axis_missing",
                "缺少援护技触发盈蓄与三十秒冷却的正式资源事件轴。",
            ),
            (
                "creation_energy_suppression_missing",
                "缺少冷却期间花命中迟缓目标时被抑制的普通盈蓄回能。",
            ),
            (
                "charge_efficiency_missing",
                "缺少实际获能角色在触发时点的充能效率。",
            ),
            (
                "future_action_axis_missing",
                "无法确认资源差额是否改变后续 Q 或其他伤害动作。",
            ),
        ),
        unavailable_explanation=(
            "埃德嘉 P1 先改变盈蓄能量与冷却内花命中回能，再可能改变"
            "未来动作；缺资源轴时不能把 120 基础能量换算成伤害。"
        ),
    ),
}


def _is_creation_hit(hit: BattleAnalysisHit) -> bool:
    if hit.direction != "outgoing" or hit.damage <= 0.0:
        return False
    if str(hit.gameplay_effect_id or "").casefold() in _CREATION_EFFECT_IDS:
        return True
    labels = (
        str(hit.attack_type or "").strip().casefold(),
        str(hit.damage_name or "").strip().casefold(),
        str(hit.damage_component or "").strip().casefold(),
    )
    return any(label in _CREATION_LABELS for label in labels)


def _is_formal_creation_hit(hit: BattleAnalysisHit) -> bool:
    return (
        hit.direction == "outgoing"
        and hit.damage > 0.0
        and str(hit.gameplay_effect_id or "").casefold()
        in _CREATION_EFFECT_IDS
    )


def _gap(code: str, explanation: str, adapter_id: str) -> BattleQuantificationGap:
    return BattleQuantificationGap(
        code=code,
        dimension_id=f"creation_passive:{adapter_id}",
        dependency_scope="mechanic_specific",
        property_ids=(),
        explanation=explanation,
    )


class BattleCreationPassiveEvaluationService:
    """Return merge-ready Buff results without inventing lifecycle state."""

    @classmethod
    def calculate(
        cls,
        analysis: BattleAnalysisSnapshot,
        build: Mapping[str, Any] | None,
        *,
        evidence: BattleCreationPassiveEvidence | None = None,
    ) -> tuple[BattleBuffCounterfactualResult, ...]:
        frozen_evidence = evidence or BattleCreationPassiveEvidence()
        creation_hits = tuple(hit for hit in analysis.hits if _is_creation_hit(hit))
        attributions = {
            row.adapter_id: row for row in frozen_evidence.attributions
        }
        hit_damage = sum(
            max(0.0, float(hit.damage))
            for hit in analysis.hits
            if hit.direction == "outgoing"
        )
        team_damage = max(
            max(0.0, float(analysis.effective_damage)),
            hit_damage,
        )
        results = []
        for enabled in BattleCharacterPassiveService.enabled_passives(build):
            adapter_id = enabled.definition.adapter_id
            if adapter_id not in _SUPPORTED_ADAPTERS:
                continue
            results.append(cls._calculate_one(
                analysis=analysis,
                enabled=enabled,
                creation_hits=creation_hits,
                team_damage=team_damage,
                hit_damage=hit_damage,
                evidence=frozen_evidence,
                attribution=attributions.get(adapter_id),
            ))
        return tuple(results)

    @classmethod
    def _calculate_one(
        cls,
        *,
        analysis: BattleAnalysisSnapshot,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
        hit_damage: float,
        evidence: BattleCreationPassiveEvidence,
        attribution: BattleCreationPassiveAttribution | None,
    ) -> BattleBuffCounterfactualResult:
        adapter_id = enabled.definition.adapter_id
        if adapter_id == "edgar-charge-reaction":
            return cls._calculate_edgar(
                enabled,
                team_damage,
                hit_damage,
                evidence,
            )
        if not creation_hits and analysis.axis_complete:
            return cls._zero(
                enabled,
                team_damage,
                (),
                method="no_observed_creation_hits",
                explanation=(
                    "当前固定轴没有创生花对敌逐击，移除该被动不删除任何"
                    "已观测伤害。"
                ),
                coverage_basis_damage=hit_damage,
            )
        if not creation_hits:
            return cls._unavailable(
                enabled,
                team_damage,
                (),
                (_gap(
                    "creation_hit_axis_incomplete",
                    "逐击轴不完整，不能把未观测到创生花逐击证明为零。",
                    adapter_id,
                ),),
                "当前未观测到创生逐击，但逐击轴不完整，因此不把缺失事件当作零收益。",
                coverage_basis_damage=hit_damage,
            )
        if adapter_id == "creation-radius" and evidence.single_target_confirmed:
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="confirmed_single_target_spatial_zero",
                explanation=(
                    "已确认单目标；范围 +400 不改变单朵倍率，也不会让同一"
                    "目标被重复命中，固定轴伤害收益为 0。"
                ),
                coverage_basis_damage=hit_damage,
            )
        has_time_stop = any(
            start is not None and end is not None and end > start
            for start, end in analysis.time_stop_intervals
        )
        if (
            adapter_id == "creation-time-stop"
            and evidence.time_stop_axis_complete
            and not has_time_stop
        ):
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="no_time_stop_zero",
                explanation=(
                    "本场没有完整时停区间，时停中继续攻击被动对当前固定轴"
                    "伤害为 0。"
                ),
                coverage_basis_damage=hit_damage,
            )
        if attribution is not None:
            return cls._from_attribution(
                enabled,
                creation_hits,
                analysis.hits,
                team_damage,
                hit_damage,
                attribution,
            )
        if adapter_id == "creation-volley":
            formal_hits = tuple(
                hit
                for hit in creation_hits
                if _is_formal_creation_hit(hit)
            )
            if formal_hits:
                return cls._nanally_half_approximation(
                    enabled,
                    creation_hits,
                    formal_hits,
                    analysis.hits,
                    team_damage,
                )
        policy = _POLICIES[adapter_id]
        gaps = cls._policy_gaps(adapter_id, policy, evidence)
        return cls._unavailable(
            enabled,
            team_damage,
            creation_hits,
            gaps,
            policy.unavailable_explanation,
            coverage_basis_damage=hit_damage,
        )

    @classmethod
    def _nanally_half_approximation(
        cls,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        formal_hits: tuple[BattleAnalysisHit, ...],
        all_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
    ) -> BattleBuffCounterfactualResult:
        formal_creation_damage = sum(
            max(0.0, float(hit.damage)) for hit in formal_hits
        )
        observed_creation_damage = sum(
            max(0.0, float(hit.damage)) for hit in creation_hits
        )
        approximate_gain = formal_creation_damage * 0.5
        unavailable_damage = max(
            0.0,
            observed_creation_damage - formal_creation_damage,
        )
        proven_unchanged_damage = max(
            0.0,
            team_damage - observed_creation_damage,
        )
        gaps = (
            _gap(
                "nanally_fire_interval_unmodeled",
                "近似只按每次发射花数从五朵增至十朵处理，未重放 2 秒至 1 秒的频率变化。",
                enabled.definition.adapter_id,
            ),
            _gap(
                "nanally_creation_lifecycle_unmodeled",
                "缺少株、齐射、生成、到期和覆盖顺序，无法建立正式精确生命周期反事实。",
                enabled.definition.adapter_id,
            ),
            _gap(
                "nanally_unattributed_damage_unquantified",
                "仅按展示标签识别、没有正式创生 GE 的命中保持未量化。",
                enabled.definition.adapter_id,
            ),
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="partial",
            partially_quantified_damage=formal_creation_damage,
            unavailable_damage=unavailable_damage,
            proven_unchanged_damage=proven_unchanged_damage,
            quantified_increment=approximate_gain,
            gaps=gaps,
        )
        return cls._result(
            enabled,
            team_damage,
            formal_hits,
            tuple(hit for hit in creation_hits if hit not in formal_hits),
            all_hits,
            quantification,
            confidence="低",
            method="approximate_nanally_creation_count_halving",
            explanation=(
                "低置信近似：对队伍正式 GE_ActorReaction_1_Damage 与"
                " GE_ActorReaction_1_1019_Damage 逐击采用花数翻倍假设，"
                "将候选有被动伤害减半作为无被动伤害，因此收益记为该部分"
                "观测伤害的 50%。此结果忽略 2 秒至 1 秒的频率变化及"
                "株/齐射生命周期，绝非正式精确反事实。"
            ),
        )

    @staticmethod
    def _policy_gaps(
        adapter_id: str,
        policy: _MechanicPolicy,
        evidence: BattleCreationPassiveEvidence,
    ) -> tuple[BattleQuantificationGap, ...]:
        available = {
            "creation_plant_identity_missing": evidence.plant_identity_complete,
            "creation_volley_identity_missing": evidence.volley_identity_complete,
            "creation_lifecycle_missing": evidence.lifecycle_complete,
            "creation_cap_order_missing": evidence.creation_cap_order_complete,
            "future_action_axis_missing": evidence.future_action_axis_complete,
            "time_stop_axis_missing": evidence.time_stop_axis_complete,
            "target_position_missing": evidence.target_positions_complete,
        }
        gaps = tuple(
            _gap(code, explanation, adapter_id)
            for code, explanation in policy.gap_specs
            if not available.get(code, False)
        )
        if gaps:
            return gaps
        return (_gap(
            f"{adapter_id.replace('-', '_')}_attribution_missing",
            "状态证据已声明完整，但调用方未传入移除被动后消失的正式事件集。",
            adapter_id,
        ),)

    @classmethod
    def _from_attribution(
        cls,
        enabled: EnabledCharacterPassive,
        creation_hits: tuple[BattleAnalysisHit, ...],
        all_hits: tuple[BattleAnalysisHit, ...],
        team_damage: float,
        hit_damage: float,
        attribution: BattleCreationPassiveAttribution,
    ) -> BattleBuffCounterfactualResult:
        by_event = {hit.event_id: hit for hit in creation_hits}
        unknown_ids = tuple(
            event_id
            for event_id in attribution.event_ids
            if event_id not in by_event
        )
        if unknown_ids:
            raise ValueError(
                f"attribution contains non-creation events: {unknown_ids!r}"
            )
        direct_hits = tuple(by_event[event_id] for event_id in attribution.event_ids)
        direct_damage = sum(float(hit.damage) for hit in direct_hits)
        creation_damage = sum(float(hit.damage) for hit in creation_hits)
        other_creation_damage = max(0.0, creation_damage - direct_damage)
        proven_unchanged = max(0.0, team_damage - creation_damage)
        evidence_basis = attribution.evidence_basis.strip()
        if attribution.complete and not direct_hits:
            return cls._zero(
                enabled,
                team_damage,
                creation_hits,
                method="complete_lifecycle_attribution_zero",
                explanation=(
                    "专用状态模型已证明当前固定轴没有因该被动新增的事件。 "
                    f"{evidence_basis}"
                ),
                coverage_basis_damage=hit_damage,
            )
        if attribution.complete:
            quantification = BattleDamageQuantification.from_buckets(
                status="complete",
                fully_quantified_damage=direct_damage,
                proven_unchanged_damage=team_damage - direct_damage,
                quantified_increment=direct_damage,
            )
            return cls._result(
                enabled,
                team_damage,
                direct_hits,
                (),
                all_hits,
                quantification,
                confidence="高",
                method="complete_lifecycle_event_attribution",
                explanation=(
                    "专用状态模型已给出移除被动时消失的完整事件集；只删除"
                    f"该集合，其他真实逐击不变。 {evidence_basis}"
                ),
            )
        if direct_damage > 0.0:
            gap = _gap(
                "creation_lifecycle_attribution_incomplete",
                "只确认了部分被动派生事件，其余创生花仍缺少完整生命周期归属。",
                enabled.definition.adapter_id,
            )
            quantification = BattleDamageQuantification.from_buckets(
                status="partial",
                partially_quantified_damage=direct_damage,
                unavailable_damage=other_creation_damage,
                proven_unchanged_damage=proven_unchanged,
                quantified_increment=direct_damage,
                gaps=(gap,),
            )
            return cls._result(
                enabled,
                team_damage,
                direct_hits,
                tuple(hit for hit in creation_hits if hit not in direct_hits),
                all_hits,
                quantification,
                confidence="中",
                method="partial_lifecycle_event_attribution",
                explanation=(
                    "只汇报正式事件归属已确认的直接伤害；未归属花伤保持"
                    f"不可量化。 {evidence_basis}"
                ),
            )
        return cls._unavailable(
            enabled,
            team_damage,
            creation_hits,
            (_gap(
                "creation_lifecycle_attribution_incomplete",
                "尚无可直接归属的正式事件，且事件集未证明完整。",
                enabled.definition.adapter_id,
            ),),
            f"未归属逐击保持原值，不伪造被动伤害。 {evidence_basis}",
            coverage_basis_damage=hit_damage,
        )

    @classmethod
    def _calculate_edgar(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        hit_damage: float,
        evidence: BattleCreationPassiveEvidence,
    ) -> BattleBuffCounterfactualResult:
        event_ids = evidence.edgar_trigger_event_ids
        if evidence.edgar_trigger_axis_complete and not event_ids:
            return cls._zero(
                enabled,
                team_damage,
                (),
                method="complete_resource_axis_without_trigger",
                explanation=(
                    "完整资源事件轴中没有援护技触发盈蓄；本时段不会发放"
                    " 120 基础能量或开启三十秒抑制窗口。"
                ),
                coverage_basis_damage=hit_damage,
            )
        policy = _POLICIES[enabled.definition.adapter_id]
        gaps = tuple(
            _gap(code, explanation, enabled.definition.adapter_id)
            for code, explanation in policy.gap_specs
            if not (
                code == "charge_trigger_axis_missing"
                and evidence.edgar_trigger_axis_complete
            )
            and not (
                code == "future_action_axis_missing"
                and evidence.future_action_axis_complete
            )
        )
        if not gaps:
            gaps = (_gap(
                "edgar_resource_consumer_missing",
                "仍缺充能效率、被抑制回能和资源差额的动作消费结果。",
                enabled.definition.adapter_id,
            ),)
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=team_damage,
            gaps=gaps,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            event_ids,
            quantification,
            affected_hits=len(event_ids),
            quantified_hits=0,
            confidence="低",
            method="resource_to_damage_unavailable",
            explanation=policy.unavailable_explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, hit_damage),
                unresolved_damage=max(0.0, hit_damage),
            ),
        )

    @classmethod
    def _zero(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        observed_hits: Sequence[BattleAnalysisHit],
        *,
        method: str,
        explanation: str,
        coverage_basis_damage: float,
    ) -> BattleBuffCounterfactualResult:
        quantification = BattleDamageQuantification.from_buckets(
            status="not_applicable",
            proven_unchanged_damage=team_damage,
            quantified_increment=0.0,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in observed_hits),
            quantification,
            affected_hits=len(observed_hits),
            quantified_hits=len(observed_hits),
            confidence="高",
            method=method,
            explanation=explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, coverage_basis_damage),
            ),
        )

    @classmethod
    def _unavailable(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        creation_hits: Sequence[BattleAnalysisHit],
        gaps: tuple[BattleQuantificationGap, ...],
        explanation: str,
        *,
        coverage_basis_damage: float,
    ) -> BattleBuffCounterfactualResult:
        creation_damage = min(
            team_damage,
            sum(max(0.0, float(hit.damage)) for hit in creation_hits),
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=creation_damage,
            proven_unchanged_damage=team_damage - creation_damage,
            gaps=gaps,
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in creation_hits),
            quantification,
            affected_hits=len(creation_hits),
            quantified_hits=0,
            confidence="低",
            method="creation_lifecycle_state_unavailable",
            explanation=explanation,
            damage_coverage=BattleDamageCoverage(
                basis_damage=max(0.0, coverage_basis_damage),
                unresolved_damage=min(
                    max(0.0, coverage_basis_damage),
                    creation_damage,
                ),
            ),
        )

    @classmethod
    def _result(
        cls,
        enabled: EnabledCharacterPassive,
        team_damage: float,
        direct_hits: Sequence[BattleAnalysisHit],
        unavailable_hits: Sequence[BattleAnalysisHit],
        all_hits: Sequence[BattleAnalysisHit],
        quantification: BattleDamageQuantification,
        *,
        confidence: str,
        method: str,
        explanation: str,
    ) -> BattleBuffCounterfactualResult:
        beneficiaries, unattributed, complete_unattributed = (
            BattleCreationPassiveBeneficiaryService.calculate(
                all_hits,
                direct_hits,
                unavailable_hits,
                team_damage=team_damage,
                quantification=quantification,
            )
        )
        return build_creation_passive_result(
            enabled,
            team_damage,
            tuple(hit.event_id for hit in direct_hits),
            quantification,
            affected_hits=len(direct_hits),
            quantified_hits=len(direct_hits),
            confidence=confidence,
            method=method,
            explanation=explanation,
            beneficiaries=beneficiaries,
            quantified_unattributed_damage_gain=unattributed,
            unattributed_damage_gain=complete_unattributed,
            damage_coverage=_hit_damage_coverage(
                all_hits,
                direct_hits,
                unavailable_hits,
            ),
        )

__all__ = [
    "CREATION_PASSIVE_EVALUATION_VERSION",
    "BattleCreationPassiveAttribution",
    "BattleCreationPassiveEvidence",
    "BattleCreationPassiveEvaluationService",
]
