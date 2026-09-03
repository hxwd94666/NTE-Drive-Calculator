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
    "critbase": frozenset(("crit",)),
    "critdamagebase": frozenset(("critdamage",)),
    "hpmaxup": frozenset(("hpmax", "hp")),
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
    """Match a calculated property to the star-curve parameter that supplies it."""

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
    """Return whether the modifier has no application or tag gate."""

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
    """Resolve complete refinement curves and audit every fork without guessing."""

    all_rows: dict[str, list[Mapping[str, Any]]] = {}
    named_matches: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        fork_id = str(row["fork_id"])
        all_rows.setdefault(fork_id, []).append(row)
        if is_fork_permanent_property_parameter(
            row.get("property_id"), row.get("name_id")
        ):
            named_matches.setdefault(fork_id, []).append(row)

    resolved_all: list[ForkPermanentProperty] = []
    audits: list[ForkPermanentAudit] = []
    for fork_id in sorted(expected_levels):
        expected = tuple(sorted({int(level) for level in expected_levels[fork_id]}))
        binding_method = "property_parameter_identity"
        selected_rows = named_matches.get(fork_id, [])
        if not selected_rows:
            binding_method = "primary_parameter_structure"
            selected_rows = [
                row
                for row in all_rows.get(fork_id, ())
                if int(row.get("parameter_ordinal", -1)) == 0
            ]
        fork_matches = [
            (_candidate(row), is_unconditional_fork_modifier(row))
            for row in selected_rows
        ]
        direct = [value for value, unconditional in fork_matches if unconditional]
        by_level: dict[int, set[ForkPermanentProperty]] = {}
        for value in direct:
            by_level.setdefault(value.refinement_level, set()).add(value)
        unique = {
            level: next(iter(values))
            for level, values in by_level.items()
            if len(values) == 1
        }
        candidate_count = sum(len(values) for values in by_level.values())
        status = "missing_calculation_evidence"
        detail = "未找到属性与精炼参数相互匹配的计算证据"
        if fork_matches and not direct:
            status = "conditional_only"
            detail = "匹配项均含施加条件或标签条件"
        elif any(len(values) > 1 for values in by_level.values()):
            status = "ambiguous"
            detail = "同一精炼等级存在多个直接候选"
        elif tuple(sorted(unique)) != expected:
            if direct:
                status = "incomplete_curve"
                detail = "直接候选未覆盖完整精炼曲线"
        elif unique:
            identities = {
                (value.property_id, value.parameter_name_id)
                for value in unique.values()
            }
            if len(identities) != 1:
                status = "ambiguous"
                detail = "不同精炼等级解析到不同属性或参数"
            else:
                status = "resolved_permanent"
                detail = "完整无条件直接属性曲线"
                resolved_all.extend(unique[level] for level in expected)
        audits.append(
            ForkPermanentAudit(
                fork_id=fork_id,
                status=status,
                expected_levels=expected,
                resolved_levels=tuple(sorted(unique)),
                candidate_count=candidate_count,
                binding_method=binding_method,
                detail=detail,
            )
        )
    return tuple(resolved_all), tuple(audits)
