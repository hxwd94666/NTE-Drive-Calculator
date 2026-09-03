# 将发行静态库弧盘行投影为 Qt 无关资料库 DTO。
"""Qt-free application service for the read-only fork catalog."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from src.storage.sqlite.static_catalog_fork_queries import StaticCatalogForkDao


class CatalogOrigin(StrEnum):
    """How a displayed catalog value entered the application."""

    OFFICIAL_STATIC = "official_static"
    PROJECT_PROJECTION = "project_projection"
    DERIVED_DISPLAY = "derived_display"


@dataclass(frozen=True, slots=True)
class CatalogSourceTrace:
    source_row_id: int | None
    row_key: str | None
    content_sha256: str | None
    relative_path: str | None
    source_file_sha256: str | None
    payload_preserved: bool = False


@dataclass(frozen=True, slots=True)
class CatalogRelation:
    kind: str
    target_id: str
    label: str
    copy_value: str
    origin: CatalogOrigin
    available: bool = True


@dataclass(frozen=True, slots=True)
class CatalogResource:
    kind: str
    path: str
    origin: CatalogOrigin = CatalogOrigin.OFFICIAL_STATIC


@dataclass(frozen=True, slots=True)
class ForkCatalogType:
    fork_type_id: int
    name_zh: str
    description_zh: str | None
    icon_path: str | None
    fork_count: int


@dataclass(frozen=True, slots=True)
class ForkCatalogSummary:
    fork_id: str
    name_zh: str
    description_zh: str | None
    quality: str
    fork_type_id: int | None
    fork_type_name_zh: str | None
    raw_group_type: str | None
    max_breakthrough: int | None
    max_refinement: int | None
    icon_path: str | None
    exclusive_character_count: int
    recommendation_count: int


@dataclass(frozen=True, slots=True)
class ForkCatalogPage:
    items: tuple[ForkCatalogSummary, ...]
    page: int
    page_size: int
    total_items: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class ForkModifier:
    ordinal: int | None
    property_id: str
    property_name_zh: str | None
    raw_value: float
    display_value: str
    operation: str
    sort_key: int | None
    origin: CatalogOrigin = CatalogOrigin.OFFICIAL_STATIC


@dataclass(frozen=True, slots=True)
class ForkGrowthLevel:
    level: int
    need_exp: int
    modify_pack_id: str
    conditions: tuple[Any, ...]
    modifiers: tuple[ForkModifier, ...]
    source: CatalogSourceTrace


@dataclass(frozen=True, slots=True)
class ForkCost:
    item_id: str
    amount: int | None
    raw_value: str


@dataclass(frozen=True, slots=True)
class ForkBreakthrough:
    stage: int
    max_fork_level: int
    need_items_raw: str | None
    need_gold_raw: str | None
    item_costs: tuple[ForkCost, ...]
    gold_costs: tuple[ForkCost, ...]
    modify_pack_id: str | None
    conditions: tuple[Any, ...]
    modifiers: tuple[ForkModifier, ...]
    source: CatalogSourceTrace


@dataclass(frozen=True, slots=True)
class ForkCriticalLevelState:
    level: int
    stage: int
    state: str
    growth: ForkGrowthLevel
    breakthrough: ForkBreakthrough
    origin: CatalogOrigin = CatalogOrigin.DERIVED_DISPLAY


@dataclass(frozen=True, slots=True)
class ForkRefinementParameter:
    ordinal: int
    name_id: str
    is_percent: bool
    raw_value: float | None
    display_value: str
    source: CatalogSourceTrace


@dataclass(frozen=True, slots=True)
class ForkRefinementLevel:
    level: int
    title_zh: str | None
    description_zh: str | None
    need_gold_raw: str | None
    parameters: tuple[ForkRefinementParameter, ...]
    buff_asset_paths: tuple[str, ...]
    projected_effect_definition_id: str | None
    projected_effect_kind: str | None
    projected_activation_kind: str | None
    projected_formula_version: int | None
    source: CatalogSourceTrace


@dataclass(frozen=True, slots=True)
class ForkBuffModifier:
    root_asset_path: str
    ordinal: int
    property_id: str
    property_name_zh: str | None
    modifier_operation: str | None
    magnitude_kind: str | None
    magnitude_value: float | None
    calculation_asset_path: str | None
    application_requirement_asset_path: str | None
    gameplay_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ForkBuffTrigger:
    root_asset_path: str
    ordinal: int
    event_type: str | None
    effect_type: str | None
    target_effect_asset_path: str | None
    target_definition_id: str | None
    target_gameplay_effect_id: str | None
    stack_count: int | None
    by_self: bool
    target_trigger: bool
    application_requirement_asset_path: str | None


@dataclass(frozen=True, slots=True)
class ForkBuffDefinition:
    refinement_level: int
    asset_path: str
    definition_id: str | None
    definition_kind: str | None
    target_available: bool
    duration_policy: str | None
    duration_magnitude: Any
    period: Any
    stacking_type: str | None
    stack_limit_count: int | None
    gameplay_effect_id: str | None
    gameplay_effect_class_path: str | None
    modifiers: tuple[ForkBuffModifier, ...]
    triggers: tuple[ForkBuffTrigger, ...]
    source_file_path: str | None
    source_file_sha256: str | None


@dataclass(frozen=True, slots=True)
class ForkCatalogMetadata:
    dataset_id: str
    schema_version: int
    importer_version: int
    built_at_utc: str
    counts: tuple[tuple[str, int], ...]
    has_fork_skill_tables: bool
    source_payloads_preserved: int
    projected_effect_definitions: int
    projected_buff_links: int
    audit_notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ForkCatalogDetail:
    summary: ForkCatalogSummary
    name_text_table: str | None
    name_text_key: str | None
    fork_type_description_zh: str | None
    upgrade_pack_id: str | None
    breakthrough_pack_id: str | None
    star_pack_id: str | None
    resources: tuple[CatalogResource, ...]
    relations: tuple[CatalogRelation, ...]
    growth_levels: tuple[ForkGrowthLevel, ...]
    breakthroughs: tuple[ForkBreakthrough, ...]
    critical_level_states: tuple[ForkCriticalLevelState, ...]
    refinement_levels: tuple[ForkRefinementLevel, ...]
    buff_definitions: tuple[ForkBuffDefinition, ...]
    source: CatalogSourceTrace
    audit_notes: tuple[str, ...]


class ForkCatalogQueries(Protocol):
    def close(self) -> None: ...
    def fork_catalog_metadata(self) -> dict[str, Any]: ...
    def list_fork_catalog_types(self) -> list[dict[str, Any]]: ...
    def count_fork_catalog_items(self, **filters: Any) -> int: ...
    def list_fork_catalog_items(self, **filters: Any) -> list[dict[str, Any]]: ...
    def get_fork_catalog_item(self, fork_id: str) -> dict[str, Any] | None: ...
    def list_fork_character_relations(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_growth_rows(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_breakthrough_rows(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_refinement_rows(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_refinement_parameters(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_buff_links(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_buff_modifiers(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_buff_triggers(self, fork_id: str) -> list[dict[str, Any]]: ...
    def list_fork_gameplay_abilities(self, fork_id: str) -> list[dict[str, Any]]: ...


def _json_value(raw: object, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return default


def _trace(row: Mapping[str, Any], *, prefix: str = "") -> CatalogSourceTrace:
    def field(name: str) -> Any:
        return row.get(f"{prefix}{name}")

    source_row_id = field("source_row_id")
    return CatalogSourceTrace(
        source_row_id=int(source_row_id) if source_row_id is not None else None,
        row_key=str(field("row_key")) if field("row_key") is not None else None,
        content_sha256=(
            str(field("content_sha256")) if field("content_sha256") is not None else None
        ),
        relative_path=(
            str(field("relative_path")) if field("relative_path") is not None else None
        ),
        source_file_sha256=(
            str(field("source_file_sha256"))
            if field("source_file_sha256") is not None else None
        ),
        payload_preserved=bool(field("payload_preserved")),
    )


def _display_number(value: float | None, *, percent: bool) -> str:
    if value is None:
        return "未保留"
    if percent:
        return f"{value * 100:g}%"
    return f"{value:g}"


def _costs(raw: object) -> tuple[ForkCost, ...]:
    text = str(raw or "").strip()
    costs: list[ForkCost] = []
    for part in filter(None, (item.strip() for item in text.split(","))):
        item_id, separator, amount_text = part.partition(":")
        amount: int | None = None
        if separator:
            try:
                amount = int(amount_text)
            except ValueError:
                amount = None
        costs.append(ForkCost(item_id=item_id, amount=amount, raw_value=part))
    return tuple(costs)


def _tags(row: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in (
        "source_require_tags_json",
        "source_ignore_tags_json",
        "target_require_tags_json",
        "target_ignore_tags_json",
    ):
        parsed = _json_value(row.get(key), [])
        if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes)):
            values.extend(str(item) for item in parsed if str(item).strip())
    return tuple(dict.fromkeys(values))


class StaticCatalogForkService:
    """Build immutable catalog DTOs without importing Qt or mutating SQLite."""

    def __init__(self, queries: ForkCatalogQueries) -> None:
        self._queries = queries

    @classmethod
    def from_database(
        cls, database_path: str | Path | None = None,
    ) -> "StaticCatalogForkService":
        return cls(StaticCatalogForkDao(database_path))

    def close(self) -> None:
        self._queries.close()

    def metadata(self) -> ForkCatalogMetadata:
        raw = self._queries.fork_catalog_metadata()
        dataset = raw.get("dataset") or {}
        capabilities = raw.get("capabilities") or {}
        has_skill_tables = bool(capabilities.get("has_fork_skill")) and bool(
            capabilities.get("has_fork_skill_level")
        )
        payload_count = int(capabilities.get("preserved_source_payloads") or 0)
        notes: list[str] = []
        if not has_skill_tables:
            notes.append(
                "schema v30 没有弧盘独立技能表；可展示的技能等级为混频 1–5 级描述、参数与 Buff。"
            )
        if payload_count == 0:
            notes.append(
                "source_row 只保留行键与 SHA-256，原始 payload 已在发行库省略。"
            )
        notes.append(
            "combat_effect_definition / combat_effect_buff_link 是 importer 生成的项目投影，不冒充独立官方字段。"
        )
        return ForkCatalogMetadata(
            dataset_id=str(dataset.get("dataset_id") or ""),
            schema_version=int(dataset.get("schema_version") or 0),
            importer_version=int(dataset.get("importer_version") or 0),
            built_at_utc=str(dataset.get("built_at_utc") or ""),
            counts=tuple(
                sorted(
                    (str(key), int(value))
                    for key, value in (raw.get("counts") or {}).items()
                )
            ),
            has_fork_skill_tables=has_skill_tables,
            source_payloads_preserved=payload_count,
            projected_effect_definitions=int(
                capabilities.get("projected_effect_definitions") or 0
            ),
            projected_buff_links=int(capabilities.get("projected_buff_links") or 0),
            audit_notes=tuple(notes),
        )
    def list_types(self) -> tuple[ForkCatalogType, ...]:
        return tuple(
            ForkCatalogType(
                fork_type_id=int(row["fork_type_id"]),
                name_zh=str(row["name_zh"]),
                description_zh=row.get("description_zh"),
                icon_path=row.get("icon_path"),
                fork_count=int(row.get("fork_count") or 0),
            )
            for row in self._queries.list_fork_catalog_types()
        )

    def list_forks(
        self,
        *,
        query: str = "",
        quality: str | None = None,
        fork_type_id: int | None = None,
        character_id: int | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> ForkCatalogPage:
        size = max(1, min(200, int(page_size)))
        total = self._queries.count_fork_catalog_items(
            query=query,
            quality=quality,
            fork_type_id=fork_type_id,
            character_id=character_id,
        )
        pages = max(1, (total + size - 1) // size)
        selected_page = max(1, min(int(page), pages))
        rows = self._queries.list_fork_catalog_items(
            query=query,
            quality=quality,
            fork_type_id=fork_type_id,
            character_id=character_id,
            limit=size,
            offset=(selected_page - 1) * size,
        )
        return ForkCatalogPage(
            items=tuple(self._summary(row) for row in rows),
            page=selected_page,
            page_size=size,
            total_items=total,
            total_pages=pages,
        )

    @staticmethod
    def _summary(row: Mapping[str, Any]) -> ForkCatalogSummary:
        type_id = row.get("fork_type_id")
        return ForkCatalogSummary(
            fork_id=str(row["fork_id"]),
            name_zh=str(row["name_zh"]),
            description_zh=row.get("description_zh"),
            quality=str(row["quality"]),
            fork_type_id=int(type_id) if type_id is not None else None,
            fork_type_name_zh=row.get("fork_type_name_zh"),
            raw_group_type=row.get("raw_group_type"),
            max_breakthrough=(
                int(row["max_breakthrough"])
                if row.get("max_breakthrough") is not None else None
            ),
            max_refinement=(
                int(row["max_star"]) if row.get("max_star") is not None else None
            ),
            icon_path=row.get("icon_path"),
            exclusive_character_count=int(row.get("exclusive_character_count") or 0),
            recommendation_count=int(row.get("recommendation_count") or 0),
        )

    @staticmethod
    def _modifier(row: Mapping[str, Any]) -> ForkModifier | None:
        property_id = row.get("property_id")
        value = row.get("value")
        if property_id is None or value is None:
            return None
        raw_value = float(value)
        return ForkModifier(
            ordinal=(
                int(row["modifier_ordinal"])
                if row.get("modifier_ordinal") is not None else None
            ),
            property_id=str(property_id),
            property_name_zh=row.get("property_name_zh"),
            raw_value=raw_value,
            display_value=_display_number(
                raw_value, percent=bool(row.get("show_percent"))
            ),
            operation=str(row.get("operation") or ""),
            sort_key=int(row["sort_key"]) if row.get("sort_key") is not None else None,
        )

    def _growth(self, fork_id: str) -> tuple[ForkGrowthLevel, ...]:
        grouped: dict[int, list[Mapping[str, Any]]] = {}
        for row in self._queries.list_fork_growth_rows(fork_id):
            grouped.setdefault(int(row["level"]), []).append(row)
        result: list[ForkGrowthLevel] = []
        for level, rows in sorted(grouped.items()):
            first = rows[0]
            conditions = _json_value(first.get("conditions_json"), [])
            result.append(ForkGrowthLevel(
                level=level,
                need_exp=int(first.get("need_exp") or 0),
                modify_pack_id=str(first.get("modify_pack_id") or ""),
                conditions=tuple(conditions if isinstance(conditions, list) else [conditions]),
                modifiers=tuple(
                    modifier for row in rows
                    if (modifier := self._modifier(row)) is not None
                ),
                source=_trace(first),
            ))
        return tuple(result)

    def _breakthroughs(self, fork_id: str) -> tuple[ForkBreakthrough, ...]:
        grouped: dict[int, list[Mapping[str, Any]]] = {}
        for row in self._queries.list_fork_breakthrough_rows(fork_id):
            grouped.setdefault(int(row["stage"]), []).append(row)
        result: list[ForkBreakthrough] = []
        for stage, rows in sorted(grouped.items()):
            first = rows[0]
            conditions = _json_value(first.get("conditions_json"), [])
            result.append(ForkBreakthrough(
                stage=stage,
                max_fork_level=int(first["max_fork_level"]),
                need_items_raw=first.get("need_items"),
                need_gold_raw=first.get("need_gold"),
                item_costs=_costs(first.get("need_items")),
                gold_costs=_costs(first.get("need_gold")),
                modify_pack_id=first.get("modify_pack_id"),
                conditions=tuple(conditions if isinstance(conditions, list) else [conditions]),
                modifiers=tuple(
                    modifier for row in rows
                    if (modifier := self._modifier(row)) is not None
                ),
                source=_trace(first),
            ))
        return tuple(result)

    @staticmethod
    def _critical_states(
        growth: tuple[ForkGrowthLevel, ...],
        breakthroughs: tuple[ForkBreakthrough, ...],
    ) -> tuple[ForkCriticalLevelState, ...]:
        growth_by_level = {row.level: row for row in growth}
        breakthrough_by_stage = {row.stage: row for row in breakthroughs}
        stage_by_cap = {row.max_fork_level: row.stage for row in breakthroughs}
        states: list[ForkCriticalLevelState] = []
        for level in (20, 30, 40, 50, 60, 70):
            growth_row = growth_by_level.get(level)
            pre_stage = stage_by_cap.get(level)
            if growth_row is None or pre_stage is None:
                continue
            for state, stage in (("突破前", pre_stage), ("突破后", pre_stage + 1)):
                breakthrough = breakthrough_by_stage.get(stage)
                if breakthrough is not None:
                    states.append(ForkCriticalLevelState(
                        level=level,
                        stage=stage,
                        state=state,
                        growth=growth_row,
                        breakthrough=breakthrough,
                    ))
        return tuple(states)

    def _refinements(self, fork_id: str) -> tuple[ForkRefinementLevel, ...]:
        parameters: dict[int, list[ForkRefinementParameter]] = {}
        for row in self._queries.list_fork_refinement_parameters(fork_id):
            level = int(row["star_level"])
            value = float(row["value"]) if row.get("value") is not None else None
            parameters.setdefault(level, []).append(ForkRefinementParameter(
                ordinal=int(row["ordinal"]),
                name_id=str(row["name_id"]),
                is_percent=bool(row["is_percent"]),
                raw_value=value,
                display_value=_display_number(value, percent=bool(row["is_percent"])),
                source=_trace(row),
            ))
        levels: list[ForkRefinementLevel] = []
        for row in self._queries.list_fork_refinement_rows(fork_id):
            level = int(row["star_level"])
            buffs = _json_value(row.get("buffs_json"), [])
            paths = tuple(
                str(buff.get("BuffObject", {}).get("AssetPathName"))
                for buff in buffs if isinstance(buff, Mapping)
                and isinstance(buff.get("BuffObject"), Mapping)
                and buff.get("BuffObject", {}).get("AssetPathName")
            )
            levels.append(ForkRefinementLevel(
                level=level,
                title_zh=row.get("title_zh"),
                description_zh=row.get("description_zh"),
                need_gold_raw=row.get("need_gold"),
                parameters=tuple(parameters.get(level, [])),
                buff_asset_paths=paths,
                projected_effect_definition_id=row.get("effect_definition_id"),
                projected_effect_kind=row.get("effect_kind"),
                projected_activation_kind=row.get("activation_kind"),
                projected_formula_version=(
                    int(row["formula_version"])
                    if row.get("formula_version") is not None else None
                ),
                source=_trace(row),
            ))
        return tuple(levels)

    def _buffs(self, fork_id: str) -> tuple[ForkBuffDefinition, ...]:
        modifiers: dict[tuple[int, str], list[ForkBuffModifier]] = {}
        for row in self._queries.list_fork_buff_modifiers(fork_id):
            key = (int(row["star_level"]), str(row["asset_path"]))
            modifiers.setdefault(key, []).append(ForkBuffModifier(
                root_asset_path=key[1],
                ordinal=int(row["ordinal"]),
                property_id=str(row["property_id"]),
                property_name_zh=row.get("property_name_zh"),
                modifier_operation=row.get("modifier_operation"),
                magnitude_kind=row.get("magnitude_kind"),
                magnitude_value=(
                    float(row["magnitude_value"])
                    if row.get("magnitude_value") is not None else None
                ),
                calculation_asset_path=row.get("calculation_asset_path"),
                application_requirement_asset_path=(
                    row.get("application_requirement_asset_path")
                ),
                gameplay_tags=_tags(row),
            ))
        triggers: dict[tuple[int, str], list[ForkBuffTrigger]] = {}
        for row in self._queries.list_fork_buff_triggers(fork_id):
            key = (int(row["star_level"]), str(row["asset_path"]))
            triggers.setdefault(key, []).append(ForkBuffTrigger(
                root_asset_path=key[1],
                ordinal=int(row["ordinal"]),
                event_type=row.get("event_type"),
                effect_type=row.get("effect_type"),
                target_effect_asset_path=row.get("target_effect_asset_path"),
                target_definition_id=row.get("target_definition_id"),
                target_gameplay_effect_id=row.get("target_gameplay_effect_id"),
                stack_count=(
                    int(row["stack_count"])
                    if row.get("stack_count") is not None else None
                ),
                by_self=bool(row.get("by_self")),
                target_trigger=bool(row.get("target_trigger")),
                application_requirement_asset_path=(
                    row.get("application_requirement_asset_path")
                ),
            ))
        result: list[ForkBuffDefinition] = []
        seen: set[tuple[int, str]] = set()
        for row in self._queries.list_fork_buff_links(fork_id):
            key = (int(row["star_level"]), str(row["target_asset_path"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(ForkBuffDefinition(
                refinement_level=key[0],
                asset_path=key[1],
                definition_id=row.get("definition_id"),
                definition_kind=row.get("definition_kind"),
                target_available=bool(row.get("target_available")),
                duration_policy=row.get("duration_policy"),
                duration_magnitude=_json_value(row.get("duration_magnitude_json"), None),
                period=_json_value(row.get("period_json"), None),
                stacking_type=row.get("stacking_type"),
                stack_limit_count=(
                    int(row["stack_limit_count"])
                    if row.get("stack_limit_count") is not None else None
                ),
                gameplay_effect_id=row.get("gameplay_effect_id"),
                gameplay_effect_class_path=row.get("gameplay_effect_class_path"),
                modifiers=tuple(modifiers.get(key, [])),
                triggers=tuple(triggers.get(key, [])),
                source_file_path=row.get("relative_path"),
                source_file_sha256=row.get("source_file_sha256"),
            ))
        return tuple(result)

    def get_fork(self, fork_id: str) -> ForkCatalogDetail | None:
        row = self._queries.get_fork_catalog_item(str(fork_id))
        if row is None:
            return None
        summary_row = dict(row)
        exclusive_ids = _json_value(row.get("exclusive_character_ids_json"), [])
        summary_row["exclusive_character_count"] = len(exclusive_ids)
        relations_rows = self._queries.list_fork_character_relations(str(fork_id))
        summary_row["recommendation_count"] = sum(
            item.get("relation_kind") == "cultivation_recommendation"
            for item in relations_rows
        )
        growth = self._growth(str(fork_id))
        breakthroughs = self._breakthroughs(str(fork_id))
        refinements = self._refinements(str(fork_id))
        buffs = self._buffs(str(fork_id))
        relations = [
            CatalogRelation(
                kind="character",
                target_id=str(item.get("character_id") or ""),
                label=(
                    f"{item.get('name_zh') or '未知角色'} "
                    f"({item.get('character_id')}) · {item.get('relation_kind')}"
                ),
                copy_value=str(item.get("character_id") or ""),
                origin=CatalogOrigin.OFFICIAL_STATIC,
            )
            for item in relations_rows
        ]
        for buff in buffs:
            relations.append(CatalogRelation(
                kind="buff",
                target_id=buff.asset_path,
                label=f"混频 {buff.refinement_level} · {buff.definition_id or buff.asset_path}",
                copy_value=buff.asset_path,
                origin=CatalogOrigin.PROJECT_PROJECTION,
                available=buff.target_available,
            ))
            if buff.target_available and buff.gameplay_effect_id:
                relations.append(CatalogRelation(
                    kind="gameplay_effect",
                    target_id=buff.gameplay_effect_id,
                    label=f"GE · {buff.gameplay_effect_id}",
                    copy_value=buff.gameplay_effect_id,
                    origin=CatalogOrigin.OFFICIAL_STATIC,
                ))
            for trigger in buff.triggers:
                if buff.target_available and trigger.target_gameplay_effect_id:
                    relations.append(CatalogRelation(
                        kind="gameplay_effect",
                        target_id=trigger.target_gameplay_effect_id,
                        label=f"GE · {trigger.target_gameplay_effect_id}",
                        copy_value=trigger.target_gameplay_effect_id,
                        origin=CatalogOrigin.OFFICIAL_STATIC,
                    ))
        ability_rows = self._queries.list_fork_gameplay_abilities(str(fork_id))
        relations.extend(
            CatalogRelation(
                kind="gameplay_ability",
                target_id=str(item["ability_id"]),
                label=f"GA · {item.get('name_zh') or item['ability_id']}",
                copy_value=str(item["ability_id"]),
                origin=CatalogOrigin.OFFICIAL_STATIC,
            )
            for item in ability_rows
        )
        resource_values: list[CatalogResource] = []
        for kind, value in (
            ("icon", row.get("icon_path")),
            ("card", row.get("card_path")),
            ("painting", row.get("painting_path")),
            ("fork_type_icon", row.get("fork_type_icon_path")),
        ):
            if value:
                resource_values.append(CatalogResource(kind, str(value)))
        for buff in buffs:
            resource_values.append(CatalogResource("buff", buff.asset_path))
            if buff.gameplay_effect_class_path:
                resource_values.append(CatalogResource("gameplay_effect", buff.gameplay_effect_class_path))
            for modifier in buff.modifiers:
                for kind, value in (
                    ("calculation", modifier.calculation_asset_path),
                    ("application_requirement", modifier.application_requirement_asset_path),
                ):
                    if value:
                        resource_values.append(CatalogResource(kind, value))
                for tag in modifier.gameplay_tags:
                    relations.append(CatalogRelation(
                        kind="gameplay_tag",
                        target_id=tag,
                        label=f"Gameplay Tag · {tag}",
                        copy_value=tag,
                        origin=CatalogOrigin.OFFICIAL_STATIC,
                    ))
            for trigger in buff.triggers:
                for kind, value in (
                    ("trigger_effect", trigger.target_effect_asset_path),
                    ("application_requirement", trigger.application_requirement_asset_path),
                ):
                    if value:
                        resource_values.append(CatalogResource(kind, value))
        resources = tuple(dict.fromkeys(resource_values))
        relations = list(dict.fromkeys(relations))
        audit_notes = [
            "等级面板展示的是官方 modify pack 行；不从中文描述推测额外数值。",
            "临界等级的“突破前/后”是等级行与官方突破阶段行的派生组合，原始库没有独立面板行。",
            "弧盘根 Buff 可精确关联 Buff/GE 资产；只有资产路径精确匹配时才返回 GA，不从描述文字猜技能。",
        ]
        if not ability_rows:
            audit_notes.append("schema v30 未保留该弧盘到 GA 的结构化精确关系。")
        if not relations_rows:
            audit_notes.append("该弧盘没有独占角色 ID 或养成推荐关系，角色归属保持未解析。")
        return ForkCatalogDetail(
            summary=self._summary(summary_row),
            name_text_table=row.get("name_text_table"),
            name_text_key=row.get("name_text_key"),
            fork_type_description_zh=row.get("fork_type_description_zh"),
            upgrade_pack_id=row.get("upgrade_pack_id"),
            breakthrough_pack_id=row.get("breakthrough_pack_id"),
            star_pack_id=row.get("star_pack_id"),
            resources=resources,
            relations=tuple(relations),
            growth_levels=growth,
            breakthroughs=breakthroughs,
            critical_level_states=self._critical_states(growth, breakthroughs),
            refinement_levels=refinements,
            buff_definitions=buffs,
            source=_trace(row),
            audit_notes=tuple(audit_notes),
        )
