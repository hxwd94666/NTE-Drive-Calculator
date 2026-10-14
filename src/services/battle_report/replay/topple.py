# 将一次团队倾陷事件拆成逐角色独立格子并求和，不把触发者当作唯一伤害来源。

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.build.inputs import BattleCharacterBaseline
from src.domain.battle_report.buffs.models import BattleHitBuffProjection
from src.domain.battle_report.replay.models import BattleHitReplayFactor, BattleHitReplayResult, BattleHitReplayTerm
from src.services.battle_report.buffs.attribute_projection import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_report.replay.special_numeric import (
    mitigation_input, numeric_replay,
)


@dataclass(frozen=True, slots=True)
class BattleToppleCharacterConfig:
    """Static inputs not owned by the frozen role panel."""

    character_id: int
    damage_attribute: str
    level_multiplier: float


def _term(
    *,
    term_id: str,
    property_id: str,
    label: str,
    value: float,
    source_group: str,
    source_name: str,
    is_percent: bool,
    basis: str,
) -> BattleHitReplayTerm:
    return BattleHitReplayTerm(
        term_id=term_id,
        property_id=property_id,
        label=label,
        value=float(value),
        source_group=source_group,
        source_name=source_name,
        is_percent=is_percent,
        evidence_basis=basis,
    )


def _baseline_terms(
    baseline: BattleCharacterBaseline,
    property_ids: Sequence[str],
) -> tuple[BattleHitReplayTerm, ...]:
    selected = tuple(property_ids)
    terms = tuple(
        _term(
            term_id=f"{row.source_group}:{row.property_id}",
            property_id=row.property_id,
            label=row.label,
            value=row.value,
            source_group=row.source_group,
            source_name=row.source_name,
            is_percent=row.is_percent,
            basis=f"{baseline.source} 角色属性来源快照",
        )
        for row in baseline.source_stats
        if row.property_id in selected and row.value != 0.0
    )
    if terms:
        return terms
    return tuple(
        _term(
            term_id=f"resolved:{row.property_id}",
            property_id=row.property_id,
            label=row.label,
            value=row.value,
            source_group="resolved",
            source_name="冻结合计",
            is_percent=row.is_percent,
            basis=f"{baseline.source} 合计值；历史来源未拆分",
        )
        for row in baseline.stats
        if row.property_id in selected and row.value != 0.0
    )


def _buff_terms(projection, property_ids: Sequence[str]) -> tuple[BattleHitReplayTerm, ...]:
    selected = set(property_ids)
    return tuple(
        _term(
            term_id=f"buff:{row.property_id}:{':'.join(row.interval_ids)}",
            property_id=row.property_id,
            label="、".join(row.buff_names) or row.property_id,
            value=row.additive_value,
            source_group="buff",
            source_name=f"Buff：{'、'.join(row.buff_names) or row.property_id}",
            is_percent=row.property_id in {"UnbalIntensityUp", "UnbalDamageUp"},
            basis=f"命中时 Buff 投影（置信度{row.confidence}）",
        )
        for row in projection.modifiers
        if row.property_id in selected and row.additive_value != 0.0
    )


def _half_label(scope_half: str) -> str:
    return {"upper": "上半场", "lower": "下半场"}.get(scope_half, scope_half)


def _baselines_for_hit(
    hit: BattleAnalysisHit,
    analysis: BattleAnalysisSnapshot,
) -> tuple[tuple[BattleCharacterBaseline, ...], tuple[str, ...]]:
    """Resolve the observed same-half party without borrowing the other half."""

    scope_half = hit.scope_half.strip().lower()
    if not scope_half:
        return analysis.baselines, ()

    observed_names: dict[int, str] = {}
    for row in analysis.timeline_hits:
        character_id = row.character_id
        if (
            row.scope_half.strip().lower() != scope_half
            or row.direction != "outgoing"
            or character_id is None
            or character_id <= 0
        ):
            continue
        observed_names.setdefault(character_id, row.character_name)

    half_name = _half_label(scope_half)
    if len(observed_names) != 4:
        return (), (
            f"本击属于{half_name}，但正式逐击确认到 "
            f"{len(observed_names)} 名同半场角色（预期 4 名），"
            "无法完整重放团队倾陷",
        )

    baselines_by_id = {row.character_id: row for row in analysis.baselines}
    missing_baselines = tuple(
        observed_names[character_id]
        for character_id in observed_names
        if character_id not in baselines_by_id
    )
    if missing_baselines:
        return (), (
            f"{half_name}角色缺少冻结面板：{'、'.join(missing_baselines)}",
        )

    return (
        tuple(
            baseline
            for baseline in analysis.baselines
            if baseline.character_id in observed_names
        ),
        (),
    )


class BattleToppleHitReplayService:
    """Replay one observed topple event as a sum of all configured role cells."""

    @staticmethod
    def projection_hits(
        hit: BattleAnalysisHit, analysis: BattleAnalysisSnapshot,
        character_configs: Mapping[int, BattleToppleCharacterConfig], *,
        source_character_id: int | None = None,
    ) -> tuple[BattleAnalysisHit, ...]:
        """Prepare all role cells together before entering per-hit replay."""
        if source_character_id is None:
            baselines, errors = _baselines_for_hit(hit, analysis)
            if errors:
                return ()
        else:
            baselines = tuple(row for row in analysis.baselines
                              if row.character_id == source_character_id)
        return tuple(
            replace(hit, character_id=baseline.character_id,
                    character_name=baseline.character_name,
                    damage_attribute=character_configs[baseline.character_id].damage_attribute)
            for baseline in baselines if baseline.character_id in character_configs
        )

    @classmethod
    @numeric_replay
    def replay(
        cls,
        *,
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        character_configs: Mapping[int, BattleToppleCharacterConfig],
        source_character_id: int | None = None,
        formula_type: str = "倾陷伤害（逐角色求和）",
        projection_for_hit: Callable[[BattleAnalysisHit], BattleHitBuffProjection] | None = None,
    ) -> BattleHitReplayResult:
        condition = analysis.target_condition
        if condition is None:
            return cls._unreplayable(
                hit,
                "尚未保存用户确认的单目标防御与抗性",
                formula_type=formula_type,
            )
        factors: list[BattleHitReplayFactor] = []
        missing: list[str] = []
        prepared = []
        if source_character_id is None:
            baselines, roster_errors = _baselines_for_hit(hit, analysis)
        else:
            baselines = tuple(
                baseline
                for baseline in analysis.baselines
                if baseline.character_id == source_character_id
            )
            roster_errors = (
                ()
                if baselines
                else (f"缺少角色 {source_character_id} 的冻结面板",)
            )
        if roster_errors:
            return cls._unreplayable(
                hit,
                *roster_errors,
                formula_type=formula_type,
            )
        for baseline in baselines:
            config = character_configs.get(baseline.character_id)
            if config is None:
                missing.append(
                    f"{baseline.character_name} 缺少静态属性或倾陷等级曲线"
                )
                continue
            projection, cell_inputs = cls._character_inputs(
                hit=hit, analysis=analysis, baseline=baseline, config=config,
                projection_for_hit=projection_for_hit,
            )
            prepared.append((baseline, config, projection, cell_inputs))

        if not prepared or missing:
            return cls._unreplayable(
                hit,
                *missing or ("没有可计算的出场角色格子",),
                formula_type=formula_type,
            )
        numbers = yield ("special_topple_v1", {
            "observed": hit.damage, "enemy_topple_limit": condition.enemy_topple_limit,
            "feast": condition.environment_kind == "feast",
            "cells": [cell for _, _, _, cell in prepared],
        })
        target_multiplier = numbers["target_multiplier"]
        target_formula = (
            "争锋高阶 Boss 实测档位 2500%"
            if condition.environment_kind == "feast" and condition.enemy_topple_limit >= 70.0
            else "普通档 max(1, UnbalMax ÷ 3)"
        )
        factors.extend(
            cls._character_contribution(
                baseline=baseline, config=config, projection=projection,
                cell_inputs=inputs, numbers=cell_numbers, target_multiplier=target_multiplier,
            )
            for (baseline, config, projection, inputs), cell_numbers
            in zip(prepared, numbers["cells"], strict=True)
        )
        predicted = numbers["selected"]
        signed_error, absolute_error = numbers["signed_error"], numbers["absolute_error"]
        confidence = (
            "高" if absolute_error is not None and absolute_error <= 0.5
            else "中" if absolute_error is not None and absolute_error <= 2.0
            else "低"
        )
        factors.insert(0, BattleHitReplayFactor(
            factor_id="topple_target",
            label="敌方倾陷上限区",
            value=target_multiplier,
            evidence_basis=(
                f"用户确认目标属性包 UnbalMax={condition.enemy_topple_limit:g}；"
                "高阶争锋 Boss 档位由真实逐击校验"
            ),
            formula=target_formula,
            terms=(_term(
                term_id="target:UnbalMax",
                property_id="UnbalMax",
                label="敌方倾陷上限",
                value=condition.enemy_topple_limit,
                source_group="target",
                source_name="敌方",
                is_percent=False,
                basis="用户确认的目标属性包",
            ),),
        ))
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=predicted,
            critical_damage=None,
            selected_damage=predicted,
            selected_error_percent=absolute_error,
            critical_state="not_applicable",
            confidence=confidence,
            factors=tuple(factors),
            missing_evidence=((
                "达芙蒂尔额外倾陷结算只重放达芙蒂尔本人的倾陷贡献；"
                "静态 TRUE 标签不改变倾陷的防御与抗性规则"
            ),) if source_character_id is not None else (
                "倾陷事件的逐角色分量来自公式重放；nte-core 当前仅上报团队合计",
            ),
            formula_type=formula_type,
            critical_rate=0.0,
            expected_damage=predicted,
            corrected_expected_damage=hit.damage if predicted > 0.0 else None,
            signed_error_percent=signed_error,
            critical_policy="disabled",
        )

    @staticmethod
    def _character_inputs(
        *,
        hit: BattleAnalysisHit,
        analysis: BattleAnalysisSnapshot,
        baseline: BattleCharacterBaseline,
        config: BattleToppleCharacterConfig,
        projection_for_hit: Callable[[BattleAnalysisHit], BattleHitBuffProjection] | None = None,
    ) -> tuple:
        condition = analysis.target_condition
        assert condition is not None
        role_hit = replace(
            hit,
            character_id=baseline.character_id,
            character_name=baseline.character_name,
            damage_attribute=config.damage_attribute,
        )
        projection = (
            BattleBuffAttributeProjectionService.project_hit(role_hit, analysis.buff_intervals)
            if projection_for_hit is None else projection_for_hit(role_hit)
        )
        frozen = {row.property_id: row.value for row in baseline.stats}
        values = BattleBuffAttributeProjectionService.apply_additive(
            frozen,
            projection,
        )
        return projection, {
            "strength_base": float(values.get("UnbalIntensityBase", 0.0)),
            "strength_up": float(values.get("UnbalIntensityUp", 0.0)),
            "strength_add": float(values.get("UnbalIntensityAdd", 0.0)),
            "damage_up": float(values.get("UnbalDamageUp", 0.0)),
            "level_multiplier": config.level_multiplier,
            "mitigation": mitigation_input(condition, baseline.character_level, values,
                                           projection, config.damage_attribute,
                                           clamp_defense=False),
        }

    @staticmethod
    def _character_contribution(
        *, baseline, config, projection, cell_inputs, numbers, target_multiplier,
    ) -> BattleHitReplayFactor:
        strength, topple_damage_up = numbers["strength"], cell_inputs["damage_up"]
        attribute = config.damage_attribute
        strength_terms = (
            *_baseline_terms(
                baseline,
                ("UnbalIntensityBase", "UnbalIntensityUp", "UnbalIntensityAdd"),
            ),
            *_buff_terms(
                projection,
                ("UnbalIntensityBase", "UnbalIntensityUp", "UnbalIntensityAdd"),
            ),
        )
        damage_up_terms = (
            *_baseline_terms(baseline, ("UnbalDamageUp", "ToppleDamageUp")),
            *_buff_terms(projection, ("UnbalDamageUp",)),
        )
        terms = (
            _term(
                term_id=f"character:{baseline.character_id}:level_multiplier",
                property_id="ToppleLevelMultiplier",
                label="等级基础值",
                value=config.level_multiplier,
                source_group="static",
                source_name="官方倾陷等级曲线",
                is_percent=False,
                basis=f"角色等级 {baseline.character_level:g}",
            ),
            *strength_terms,
            *damage_up_terms,
            _term(
                term_id=f"character:{baseline.character_id}:defense",
                property_id="DefenseMultiplier",
                label="防御区",
                value=numbers["defense"],
                source_group="calculated",
                source_name="角色与敌方",
                is_percent=False,
                basis="该角色等级、穿防及用户确认 DefBase/6",
            ),
            _term(
                term_id=f"character:{baseline.character_id}:resistance",
                property_id=f"ResistanceMultiplier:{attribute}",
                label=f"{attribute} 抗性区",
                value=numbers["resistance"],
                source_group="calculated",
                source_name="角色与敌方",
                is_percent=False,
                basis="该角色固有伤害属性、属性穿透及目标抗性",
            ),
        )
        return BattleHitReplayFactor(
            factor_id=f"topple_character:{baseline.character_id}",
            label=f"{baseline.character_name}倾陷贡献",
            value=numbers["damage"],
            evidence_basis=(
                f"{baseline.source} 面板 + 官方 {config.damage_attribute} 属性 + "
                "命中时 Buff"
            ),
            formula=(
                f"{config.level_multiplier:g} × "
                f"(1 + {strength:g}/300 + {topple_damage_up:g}) × "
                f"{target_multiplier:g} × {numbers['defense']:.6f} × "
                f"{numbers['resistance']:.6f}"
            ),
            terms=terms,
        )

    @staticmethod
    def _unreplayable(
        hit: BattleAnalysisHit,
        *reasons: str,
        formula_type: str = "倾陷伤害（逐角色求和）",
    ) -> BattleHitReplayResult:
        return BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=None,
            critical_damage=None,
            selected_damage=None,
            selected_error_percent=None,
            critical_state="unreplayable",
            confidence="未解析",
            factors=(),
            missing_evidence=tuple(reasons),
            formula_type=formula_type,
        )
