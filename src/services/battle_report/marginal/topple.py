# 按冻结逐角色倾陷贡献批量计算单项强度变化，不重建整场公式。
from __future__ import annotations

from src.domain.native_analysis import BattleComputeBackend


def topple_ratio(replay, *, character_id: int, unit: float) -> float | None:
    if replay is None or replay.critical_state == "unreplayable":
        return None
    contributions = tuple(factor for factor in replay.factors if factor.factor_id.startswith("topple_character:"))
    source = next((factor for factor in contributions if factor.factor_id == f"topple_character:{character_id}"), None)
    total = sum(max(0.0, float(factor.value)) for factor in contributions)
    if source is None or total <= 0.0:
        return None

    def term_total(*property_ids):
        accepted = set(property_ids)
        return sum(float(term.value) for term in source.terms if term.property_id in accepted)

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


def topple_ratio_batch(replays, *, character_id, units, backend: BattleComputeBackend | None = None, checkpoint=None):
    if backend is None:
        return tuple(tuple(topple_ratio(row, character_id=character_id, unit=unit) for row in replays) for unit in units)
    inputs = [{
        "character_id": character_id, "unit": unit,
        "replay": None if replay is None else {
            "critical_state": replay.critical_state,
            "factors": [{
                "factor_id": factor.factor_id, "value": factor.value,
                "terms": [{"property_id": term.property_id, "value": term.value} for term in factor.terms],
            } for factor in replay.factors if factor.factor_id.startswith("topple_character:")],
        },
    } for unit in units for replay in replays]
    values = backend.compute_batch("topple_marginal_v1", inputs, checkpoint=checkpoint)
    if len(values) != len(inputs):
        raise ValueError("Native topple marginal result count mismatch")
    return tuple(tuple(row["ratio"] for row in values[index * len(replays):(index + 1) * len(replays)])
                 for index in range(len(units)))
