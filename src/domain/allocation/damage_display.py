# 共用伤害结果的有限数、覆盖和本队相对收益展示口径，不把联合归一指数当伤害。
from collections.abc import Mapping
from math import isclose, isfinite


def finite_nonnegative(value):
    try:
        return type(value) in (int, float) and isfinite(value) and value >= 0
    except OverflowError:
        return False


def team_gain_ratio(baseline, candidate):
    if not finite_nonnegative(baseline) or baseline <= 0 or not finite_nonnegative(candidate):
        raise ValueError("本队 B0 或最终目标值无效")
    ratio = candidate / baseline - 1
    if not isfinite(ratio):
        raise ValueError("本队相对收益超出有效范围")
    return ratio


def fixed_coverage_note(value):
    if not isinstance(value, Mapping) or value.get("policy") != "available_fixed_domain_v1":
        return ""
    keys = ("basis_damage", "quantified_damage", "estimated_damage", "excluded_damage")
    if not all(finite_nonnegative(value.get(key)) for key in keys) or value["basis_damage"] <= 0:
        return ""
    total = value["basis_damage"]
    if not isclose(sum(value[key] for key in keys[1:]), total, rel_tol=1e-9, abs_tol=1e-9):
        return ""
    strict, estimated, excluded = (value[key] / total * 100 for key in keys[1:])
    return (localized_text(None, 'auto.domain.allocation.damage_display.fixed_coverage_note.f74e0a2b1', '计分覆盖 {value}%：已量化（含已证实不变）{strict}%、估算 {estimated}%；未计入 {excluded}%。', value=f'{strict + estimated:.1f}', strict=f'{strict:.1f}', estimated=f'{estimated:.1f}', excluded=f'{excluded:.1f}'))


def search_is_complete(value):
    return (value.get("neighborhood_complete") is True
            and value.get("termination") == "local_optimum"
            and all(value.get(key) is not False for key in ("blueprint_complete", "enumeration_complete", "quantification_complete"))
            and (value.get("blueprint_usage") or {}).get("complete") is not False
            and value.get("blueprint_enumeration_complete") is not False)
from src.domain.localization.catalog import localized_text
