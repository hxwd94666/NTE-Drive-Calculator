# 将完整冻结逐击反事实输入整批送入 Rust，保留公开 Python 比较作为差分基准。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import isfinite

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
    BattleQuantificationGap,
)
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_report.damage.composition import classify_battle_hit_channel
from src.services.battle_report.damage.counterfactual import (
    BattleHitCounterfactualRatioService,
)


def _baseline(value):
    if value is None:
        return None
    return {
        "character_level": value.character_level,
        "stats": [(row.property_id, row.value) for row in value.stats],
    }


def _projection(value):
    if value is None:
        return []
    return [{
        "property_id": row.property_id,
        "additive_value": row.additive_value,
        "target_scope": row.target_scope,
        "confidence": row.confidence,
    } for row in value.modifiers]


def _factors(values):
    return [{
        "factor_id": factor.factor_id, "label": factor.label, "value": factor.value,
        "term_properties": [term.property_id for term in factor.terms],
    } for factor in values]


def _replay(value, tables=None):
    if value is None:
        return None
    return {
        "critical_state": value.critical_state,
        "critical_policy": value.critical_policy,
        "critical_rate": value.critical_rate,
        "non_critical_damage": _prediction(value.non_critical_damage),
        "expected_damage": _prediction(value.expected_damage),
        "formula_damage_attribute": value.formula_damage_attribute,
        "factors": (_factors(value.factors) if tables is None else
                    tables.add("factors", value.factors, _factors)),
    }


def _prediction(value):
    # 公开公式对照排除非有限预测；JSON null 保持相同缺失语义，不补零。
    return value if value is None or isfinite(value) else None


def _target(value):
    return None if value is None else {
        "scene": value.scene, "enemy_level": value.enemy_level,
        "enemy_defense_base": value.enemy_defense_base,
        "enemy_defense_up": value.enemy_defense_up,
        "enemy_defense_add": value.enemy_defense_add,
        "defense_reduction": value.defense_reduction, "resistances": value.resistances,
    }


def _key(value):
    if isinstance(value, dict):
        return tuple((key, _key(item)) for key, item in value.items())
    if isinstance(value, (tuple, list)):
        return tuple(map(_key, value))
    return value


class _SharedInputs:
    def __init__(self):
        self.values = {name: [] for name in ("baselines", "projections", "replays", "factors", "targets")}
        self.identities = {name: {} for name in self.values}
        self.keys = {name: {} for name in self.values}

    def add(self, name, source, encode):
        cached = self.identities[name].get(id(source))
        if cached is not None:
            return cached[1]
        value = encode(source)
        key = _key(value)
        index = self.keys[name].get(key)
        if index is None:
            index = len(self.values[name])
            self.values[name].append(value)
            self.keys[name][key] = index
        # 请求寿命内持有原对象，防止复用 id 指向不同冻结值。
        self.identities[name][id(source)] = (source, index)
        return index


def _input(job: Mapping, tables=None):
    hit = job["hit"]
    target = job.get("target_condition")
    evidence = job.get("skill_evidence")
    def encode(name, value, convert):
        return convert(value) if tables is None else tables.add(name, value, convert)

    return {
        "channel_id": classify_battle_hit_channel(hit)[0],
        "gameplay_effect_id": hit.gameplay_effect_id,
        "damage_attribute": hit.damage_attribute,
        "original_baseline": encode("baselines", job.get("original_baseline"), _baseline),
        "candidate_baseline": encode("baselines", job.get("candidate_baseline"), _baseline),
        "original_projection": encode("projections", job.get("original_projection"), _projection),
        "candidate_projection": encode("projections", job.get("candidate_projection"), _projection),
        "original_replay": encode("replays", job.get("original_replay"), lambda v: _replay(v, tables)),
        "candidate_replay": encode("replays", job.get("candidate_replay"), lambda v: _replay(v, tables)),
        "scaling_property_id": "" if evidence is None else evidence.scaling_property_id,
        "target_condition": encode("targets", target, _target),
    }


def compare_counterfactual_batch(
    jobs: Sequence[Mapping],
    *,
    backend: BattleComputeBackend | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[BattleCounterfactualRatio, ...]:
    """Compare ordered complete hit requests in one backend batch per chunk."""
    if backend is None or not backend.supports_battle_compute:
        results = []
        for job in jobs:
            if checkpoint is not None:
                checkpoint()
            results.append(BattleHitCounterfactualRatioService.compare(**job))
        return tuple(results)
    results = []
    for start in range(0, len(jobs), 2048):
        if checkpoint is not None:
            checkpoint()
        inputs = []
        tables = _SharedInputs()
        for job in jobs[start:start + 2048]:
            if checkpoint is not None:
                checkpoint()
            inputs.append(_input(job, tables))
        responses = backend.compute_batch(
            "counterfactual_ratios_shared_v1", ({**tables.values, "hits": inputs},),
            checkpoint=checkpoint,
        )
        if len(responses) != 1 or not isinstance(responses[0].get("results"), list):
            raise ValueError("counterfactual shared response mismatch")
        responses = responses[0]["results"]
        if len(responses) != len(inputs):
            raise ValueError("counterfactual response count mismatch")
        for response in responses:
            response = dict(response)
            response["gaps"] = tuple(
                BattleQuantificationGap(
                    **{**gap, "property_ids": tuple(gap["property_ids"])},
                )
                for gap in response["gaps"]
            )
            for key in ("included_dimension_ids", "cancelled_dimension_ids"):
                response[key] = tuple(response[key])
            results.append(BattleCounterfactualRatio(**response))
    return tuple(results)
