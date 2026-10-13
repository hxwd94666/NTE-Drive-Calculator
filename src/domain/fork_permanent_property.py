# 依据静态效果证据解析弧盘无条件常驻面板属性。
"""Evidence rules for unconditional fork panel properties."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Any, Iterable, Mapping


_PROPERTY_SUFFIX_ALIASES: dict[str, frozenset[str]] = {
    "atkup": frozenset(("atk",)),
    "chargegetefficiencybase": frozenset(("chargegetefficiency",)),
    "critbase": frozenset(("crit", "critup")),
    "critdamagebase": frozenset(("critdamage",)),
    "damageuppsychebase": frozenset(("psycheup",)),
    "hpmaxup": frozenset(("hpmax", "hp")),
    "magbase": frozenset(("magup",)),
    "unbalintensitybase": frozenset(("unbalintensity", "unbal")),
}
_TAG_COLUMNS = (
    "source_require_tags_json",
    "source_ignore_tags_json",
    "target_require_tags_json",
    "target_ignore_tags_json",
)


@dataclass(frozen=True)
class ForkPermanentProperty:
    fork_id: str
    refinement_level: int
    property_id: str
    parameter_name_id: str
    property_value: float
    modifier_operation: str
    calculation_asset_path: str
    effect_definition_id: str
    source_row_id: int


@dataclass(frozen=True)
class ForkPermanentAudit:
    fork_id: str
    status: str
    expected_levels: tuple[int, ...]
    resolved_levels: tuple[int, ...]
    candidate_count: int
    binding_method: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact_identifier(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _property_suffixes(property_id: object) -> frozenset[str]:
    compact = _compact_identifier(property_id)
    suffixes = {compact}
    if compact.endswith("base") and len(compact) > 4:
        suffixes.add(compact[:-4])
    suffixes.update(_PROPERTY_SUFFIX_ALIASES.get(compact, ()))
    return frozenset(suffix for suffix in suffixes if len(suffix) >= 2)


def is_fork_permanent_property_parameter(
    property_id: object,
    parameter_name_id: object,
) -> bool:
    """按属性与精炼参数标识关联计算证据，不根据描述文本猜常驻属性。"""

    parameter = _compact_identifier(parameter_name_id)
    return any(parameter.endswith(suffix) for suffix in _property_suffixes(property_id))


def _has_tags(value: object) -> bool:
    if value in (None, "", "[]", "{}"):
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    try:
        return bool(json.loads(str(value)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return True


def is_unconditional_fork_modifier(row: Mapping[str, Any]) -> bool:
    """仅无施加条件且无标签门禁的修正可作常驻候选；损坏标签按有条件处理。"""

    if row.get("application_requirement_asset_path"):
        return False
    return not any(_has_tags(row.get(column)) for column in _TAG_COLUMNS)


def _candidate(row: Mapping[str, Any]) -> ForkPermanentProperty:
    return ForkPermanentProperty(
        fork_id=str(row["fork_id"]),
        refinement_level=int(row["star_level"]),
        property_id=str(row["property_id"]),
        parameter_name_id=str(row["name_id"]),
        property_value=float(row["value"]),
        modifier_operation=str(row["modifier_operation"]),
        calculation_asset_path=str(row["calculation_asset_path"]),
        effect_definition_id=str(row["effect_definition_id"]),
        source_row_id=int(row["source_row_id"]),
    )


def resolve_fork_permanent_properties(
    rows: Iterable[Mapping[str, Any]],
    expected_levels: Mapping[str, Iterable[int]],
) -> tuple[tuple[ForkPermanentProperty, ...], tuple[ForkPermanentAudit, ...]]:
    """逐弧盘审完整精炼曲线；歧义、缺级、仅条件或缺证据分别记录，不补零晋升。"""

    all_rows: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        fork_id = str(row["fork_id"])
        all_rows.setdefault(fork_id, []).append(row)

    resolved_all: list[ForkPermanentProperty] = []
    audits: list[ForkPermanentAudit] = []
    for fork_id in sorted(expected_levels):
        expected = tuple(sorted({int(level) for level in expected_levels[fork_id]}))
        fork_rows = all_rows.get(fork_id, [])
        direct_rows = [row for row in fork_rows if is_unconditional_fork_modifier(row)]
        properties = sorted({str(row["property_id"]) for row in direct_rows})
        selected: list[ForkPermanentProperty] = []
        methods: set[str] = set()
        ambiguous = False
        incomplete = False
        candidate_count = 0
        for property_id in properties:
            property_rows = [
                row for row in direct_rows if str(row["property_id"]) == property_id
            ]
            named = [
                row for row in property_rows
                if is_fork_permanent_property_parameter(property_id, row["name_id"])
            ]
            if named:
                methods.add("property_parameter_identity")
                property_rows = named
            elif len(properties) == 1:
                methods.add("primary_parameter_structure")
                property_rows = [
                    row for row in property_rows
                    if int(row.get("parameter_ordinal", -1)) == 0
                ]
            else:
                ambiguous = True
                continue
            by_level: dict[int, set[ForkPermanentProperty]] = {}
            for row in property_rows:
                value = _candidate(row)
                by_level.setdefault(value.refinement_level, set()).add(value)
            candidate_count += sum(len(values) for values in by_level.values())
            if any(len(values) > 1 for values in by_level.values()):
                ambiguous = True
            elif tuple(sorted(by_level)) != expected:
                incomplete = True
            else:
                selected.extend(next(iter(by_level[level])) for level in expected)
        binding_method = "+".join(sorted(methods)) or "none"
        resolved_levels = tuple(sorted({value.refinement_level for value in selected}))
        status = "missing_calculation_evidence"
        detail = "未找到属性与精炼参数相互匹配的计算证据"
        if not expected:
            status = "missing_refinement_levels"
            detail = "弧盘已入库，但未找到可审查的精炼等级定义"
        elif fork_rows and not direct_rows:
            status = "conditional_only"
            detail = "匹配项均含施加条件或标签条件"
        elif ambiguous:
            status = "ambiguous"
            detail = "属性与精炼参数无法一对一对应，或同级属性有多个来源"
        elif incomplete:
            status = "incomplete_curve"
            detail = "直接候选未覆盖完整精炼曲线"
        elif selected:
            status = "resolved_permanent"
            detail = "完整无条件直接属性曲线"
            resolved_all.extend(selected)
        audits.append(
            ForkPermanentAudit(
                fork_id=fork_id,
                status=status,
                expected_levels=expected,
                resolved_levels=resolved_levels,
                candidate_count=candidate_count,
                binding_method=binding_method,
                detail=detail,
            )
        )
    return tuple(resolved_all), tuple(audits)
