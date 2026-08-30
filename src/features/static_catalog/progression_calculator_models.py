# 统一角色、技能与弧盘养成请求，并编排公共体力 Service。
"""Qt-free request adapters for the shared progression calculator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import logging
from typing import Any, Callable

from src.domain.progression_stamina import (
    IdentificationLevelProjection,
    MaterialRequirement,
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.services.progression_stamina_service import ProgressionStaminaService
from src.services.static_catalog_fork_release_metadata import ForkProgressionRequest
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


UNKNOWN_NAME = "名称暂未提供"
CALLBACK_ERROR_TEXT = "结果已计算，但未能返回原页面。请关闭后重新打开。"
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProgressionUpstreamGap:
    """One source-side gap retained outside the stamina solver."""

    code: str
    source_ref: str | None = None
    item_id: str | None = None
    raw_value: str | None = None


@dataclass(frozen=True, slots=True)
class ProgressionMaterialInput:
    """One player-facing material card with its identity kept collapsible."""

    key: str
    display_name: str
    requested_ids: tuple[str, ...]
    canonical_id: str
    required_quantity: int | None
    known_quantity: int
    text_table: str | None
    text_key: str | None
    resolved_locale: str | None

    @property
    def planning_quantity(self) -> int:
        """Exact total, or only the positive known portion of an unknown total."""

        if self.required_quantity is not None:
            return self.required_quantity
        return self.known_quantity

    @property
    def requirement_text(self) -> str:
        if self.required_quantity is not None:
            return f"需要 {self.required_quantity}"
        if self.known_quantity > 0:
            return f"完整需求量不可用 · 已知至少 {self.known_quantity}"
        return "需求量不可用"

    @property
    def more_info(self) -> tuple[tuple[str, str], ...]:
        rows: list[tuple[str, str]] = []
        requested = "、".join(self.requested_ids)
        if requested:
            rows.append(("请求材料 ID", requested))
        if self.canonical_id and (
            len(self.requested_ids) > 1
            or self.canonical_id not in self.requested_ids
        ):
            rows.append(("规范材料 ID", self.canonical_id))
        if self.text_table and self.text_key:
            rows.append(("本地化索引", f"{self.text_table} / {self.text_key}"))
        if self.resolved_locale:
            rows.append(("本地化语言", self.resolved_locale))
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class ProgressionCalculatorSession:
    """Frozen page request presented by one calculator opening."""

    kind: str
    entity_id: str
    owner_id: str
    skill_id: str | None
    title: str
    materials: tuple[ProgressionMaterialInput, ...]
    upstream_status: StaminaPlanStatus
    upstream_gaps: tuple[ProgressionUpstreamGap, ...]

    @property
    def more_info(self) -> tuple[tuple[str, str], ...]:
        rows: list[tuple[str, str]] = []
        for index, gap in enumerate(self.upstream_gaps, start=1):
            values = [gap.code]
            if gap.source_ref:
                values.append(gap.source_ref)
            if gap.item_id:
                values.append(gap.item_id)
            if gap.raw_value:
                values.append(gap.raw_value)
            rows.append((f"上游未完整项 {index}", " · ".join(values)))
        return tuple(rows)


@dataclass(frozen=True, slots=True)
class ProgressionCalculatorOutcome:
    """Result plus stable routing identity for the composition root callback."""

    kind: str
    entity_id: str
    owner_id: str
    skill_id: str | None
    result: ProgressionStaminaResult


def deliver_progression_outcome(
    callback: Callable[[ProgressionCalculatorOutcome], None],
    outcome: ProgressionCalculatorOutcome,
) -> str | None:
    """Keep page callback failures from escaping a Qt signal handler."""

    try:
        callback(outcome)
    except Exception as exc:
        _LOGGER.error(
            "static catalog progression result projection failed",
            extra={
                "progression_kind": outcome.kind,
                "error_type": type(exc).__name__,
            },
        )
        return CALLBACK_ERROR_TEXT
    return None


@dataclass(slots=True)
class _MaterialAggregate:
    requested_ids: list[str]
    display_name: str
    canonical_id: str
    required_quantity: int
    known_quantity: int
    unknown_total: bool
    text_table: str | None
    text_key: str | None
    resolved_locale: str | None


class ProgressionCalculatorOrchestrator:
    """Adapt page DTOs and call the single public stamina Service."""

    def __init__(
        self,
        *,
        service: ProgressionStaminaService,
        terminology_service: StaticCatalogTerminologyService,
    ) -> None:
        self._service = service
        self._terminology = terminology_service

    def prepare(
        self,
        request: Mapping[str, object] | ForkProgressionRequest,
    ) -> ProgressionCalculatorSession:
        if isinstance(request, ForkProgressionRequest):
            return self._prepare_fork(request)
        if isinstance(request, Mapping):
            return self._prepare_character(request)
        raise TypeError("公共养成计算器只接受角色字典或 ForkProgressionRequest")

    def identification_level(
        self,
        hunter_level: int,
        *,
        effective_level: int | None = None,
    ) -> IdentificationLevelProjection:
        return self._service.identification_level(
            hunter_level,
            effective_level=effective_level,
        )

    def calculate(
        self,
        session: ProgressionCalculatorSession,
        *,
        hunter_level: int,
        effective_identification_level: int | None,
        owned_quantities: Mapping[str, int],
    ) -> ProgressionCalculatorOutcome:
        requirements = tuple(
            MaterialRequirement(
                item_id=material.canonical_id,
                required_quantity=material.planning_quantity,
                owned_quantity=_owned_quantity(owned_quantities, material.key),
            )
            for material in session.materials
            if material.planning_quantity > 0
        )
        request = ProgressionStaminaRequest(
            hunter_level=int(hunter_level),
            effective_identification_level=effective_identification_level,
            requirements=requirements,
            stages=(),
        )
        result = self._service.calculate(request)
        result = _merge_upstream_status(session, result, bool(requirements))
        return ProgressionCalculatorOutcome(
            kind=session.kind,
            entity_id=session.entity_id,
            owner_id=session.owner_id,
            skill_id=session.skill_id,
            result=result,
        )

    def _prepare_character(
        self,
        request: Mapping[str, object],
    ) -> ProgressionCalculatorSession:
        kind = str(request.get("kind") or "").strip()
        if kind not in {"character_level", "skill"}:
            raise ValueError(f"不支持的角色养成请求：{kind or '<empty>'}")
        character_id = str(request.get("character_id") or "").strip()
        if not character_id:
            raise ValueError("角色养成请求缺少 character_id")
        entity_id = character_id
        if kind == "skill":
            skill_id = str(request.get("skill_id") or "").strip()
            if not skill_id:
                raise ValueError("技能养成请求缺少 skill_id")
            entity_id = f"{character_id}:{skill_id}"
        status = _status(request.get("requirement_status"), default=None)
        raw_requirements = _tuple_value(request.get("requirements"), "requirements")
        gaps = _gaps(request.get("requirement_gaps"))
        materials = self._materials(raw_requirements, fork=False)
        status = _coherent_upstream_status(status, gaps, materials)
        return ProgressionCalculatorSession(
            kind=kind,
            entity_id=entity_id,
            owner_id=character_id,
            skill_id=skill_id if kind == "skill" else None,
            title="技能养成" if kind == "skill" else "角色等级养成",
            materials=materials,
            upstream_status=status,
            upstream_gaps=gaps,
        )

    def _prepare_fork(
        self,
        request: ForkProgressionRequest,
    ) -> ProgressionCalculatorSession:
        gaps = tuple(
            ProgressionUpstreamGap(
                code=gap.code,
                source_ref=gap.source_ref,
                item_id=gap.item_id,
                raw_value=gap.raw_value,
            )
            for gap in request.requirement_gaps
        )
        materials = self._materials(request.requirements, fork=True)
        status = (
            StaminaPlanStatus.PARTIAL
            if gaps and any(item.planning_quantity > 0 for item in materials)
            else StaminaPlanStatus.UNAVAILABLE
            if gaps
            else StaminaPlanStatus.COMPLETE
        )
        return ProgressionCalculatorSession(
            kind=request.kind,
            entity_id=request.fork_id,
            owner_id=request.fork_id,
            skill_id=None,
            title="弧盘养成",
            materials=materials,
            upstream_status=status,
            upstream_gaps=gaps,
        )

    def _materials(
        self,
        requirements: tuple[object, ...],
        *,
        fork: bool,
    ) -> tuple[ProgressionMaterialInput, ...]:
        aggregated: dict[str, _MaterialAggregate] = {}
        for raw in requirements:
            item_id = str(_field(raw, "item_id") or "").strip()
            if not item_id:
                raise ValueError("材料需求缺少稳定 item_id")
            term = self._terminology.resolve(
                "item",
                item_id,
                context="progression_cost",
            )
            canonical = str(term.canonical_id or item_id)
            required = _optional_nonnegative_int(_field(raw, "required_quantity"))
            known = (
                _nonnegative_int(_field(raw, "known_quantity"), "known_quantity")
                if fork
                else required
            )
            if known is None:
                known = 0
            entry = aggregated.get(canonical)
            if entry is None:
                aggregated[canonical] = _MaterialAggregate(
                    requested_ids=[item_id],
                    display_name=term.display_name or UNKNOWN_NAME,
                    canonical_id=canonical,
                    required_quantity=required or 0,
                    known_quantity=known,
                    unknown_total=required is None,
                    text_table=term.text_table,
                    text_key=term.text_key,
                    resolved_locale=term.resolved_locale,
                )
                continue
            if item_id not in entry.requested_ids:
                entry.requested_ids.append(item_id)
            entry.required_quantity += required or 0
            entry.known_quantity += known
            entry.unknown_total = entry.unknown_total or required is None
        return tuple(
            ProgressionMaterialInput(
                key=canonical,
                display_name=entry.display_name,
                requested_ids=tuple(entry.requested_ids),
                canonical_id=canonical,
                required_quantity=(
                    None if entry.unknown_total else entry.required_quantity
                ),
                known_quantity=entry.known_quantity,
                text_table=entry.text_table,
                text_key=entry.text_key,
                resolved_locale=entry.resolved_locale,
            )
            for canonical, entry in sorted(aggregated.items())
        )


def _merge_upstream_status(
    session: ProgressionCalculatorSession,
    result: ProgressionStaminaResult,
    has_planning_requirements: bool,
) -> ProgressionStaminaResult:
    upstream_gaps = tuple(f"upstream:{gap.code}" for gap in session.upstream_gaps)
    gaps = tuple(dict.fromkeys((*result.gaps, *upstream_gaps)))
    if session.upstream_status == StaminaPlanStatus.COMPLETE:
        return replace(result, gaps=gaps)
    if session.upstream_status == StaminaPlanStatus.UNAVAILABLE:
        status = StaminaPlanStatus.UNAVAILABLE
    elif not has_planning_requirements or result.status == StaminaPlanStatus.UNAVAILABLE:
        status = StaminaPlanStatus.UNAVAILABLE
    else:
        status = StaminaPlanStatus.PARTIAL
    return replace(
        result,
        status=status,
        total_stamina=None,
        gaps=gaps,
    )


def _coherent_upstream_status(
    status: StaminaPlanStatus,
    gaps: tuple[ProgressionUpstreamGap, ...],
    materials: tuple[ProgressionMaterialInput, ...],
) -> StaminaPlanStatus:
    """A request carrying formal gaps cannot truthfully remain complete."""

    if status != StaminaPlanStatus.COMPLETE or not gaps:
        return status
    if any(material.planning_quantity > 0 for material in materials):
        return StaminaPlanStatus.PARTIAL
    return StaminaPlanStatus.UNAVAILABLE


def _status(value: object, *, default: StaminaPlanStatus | None) -> StaminaPlanStatus:
    if value is None and default is not None:
        return default
    try:
        return StaminaPlanStatus(str(value))
    except ValueError as exc:
        raise ValueError(f"无效的材料需求状态：{value!r}") from exc


def _tuple_value(value: object, label: str) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        raise TypeError(f"{label} 必须是 DTO 序列")
    try:
        return tuple(value)  # type: ignore[arg-type] # validated generic DTO iterable
    except TypeError as exc:
        raise TypeError(f"{label} 必须是 DTO 序列") from exc


def _gaps(value: object) -> tuple[ProgressionUpstreamGap, ...]:
    rows = _tuple_value(value, "requirement_gaps")
    result: list[ProgressionUpstreamGap] = []
    for row in rows:
        code = str(_field(row, "code") or _field(row, "reason_code") or "").strip()
        if not code:
            raise ValueError("上游未完整项缺少稳定原因码")
        result.append(ProgressionUpstreamGap(
            code=code,
            source_ref=_optional_text(_field(row, "source_ref")),
            item_id=_optional_text(_field(row, "item_id")),
            raw_value=_optional_text(_field(row, "raw_value")),
        ))
    return tuple(result)


def _field(value: object, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, "required_quantity")


def _nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必须是非负整数")
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} 必须是非负整数") from exc
    if parsed < 0 or str(value).strip() != str(parsed):
        raise ValueError(f"{label} 必须是非负整数")
    return parsed


def _owned_quantity(values: Mapping[str, int], key: str) -> int:
    return _nonnegative_int(values.get(key, 0), "owned_quantity")


__all__ = [
    "CALLBACK_ERROR_TEXT",
    "ProgressionCalculatorOrchestrator",
    "ProgressionCalculatorOutcome",
    "ProgressionCalculatorSession",
    "ProgressionMaterialInput",
    "ProgressionUpstreamGap",
    "deliver_progression_outcome",
]
