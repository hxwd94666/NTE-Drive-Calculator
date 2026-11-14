# 将弧盘资料域映射到公共只读提供器契约。
"""Adapt the fork catalog domain to the common read-only provider contract."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import resolve_text_catalog
from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.features.static_catalog.providers._shared import (
    annotation,
    derived,
    ensure_release_metadata,
    ensure_release_path,
    lines,
    official,
)
from src.services.static_catalog.forks.service import (
    CatalogOrigin,
    ForkCatalogDetail,
    ForkCatalogSummary,
    StaticCatalogForkService,
)


FORK_DOMAIN = CatalogDomain(
    key="fork",
    label="弧盘数据",
    description="升级经验与面板、突破消耗、混频技能、Buff 和角色关系",
    order=20,
)


class ForkCatalogProvider:
    """Own one read-only fork service and adapt it without Qt or SQL."""

    def __init__(self, database_path: str | Path, *, text_catalog=None) -> None:
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._database_path = Path(database_path).resolve()
        self._service = StaticCatalogForkService.from_database(self._database_path)
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        # 领域名与说明属于界面文案；key 与 order 保持稳定，不随译文变化。
        text_catalog = getattr(self, 'text_catalog', None)
        return CatalogDomain(
            key=FORK_DOMAIN.key,
            label=ui_enum_text(text_catalog, FORK_DOMAIN.label),
            description=ui_enum_text(text_catalog, FORK_DOMAIN.description),
            order=FORK_DOMAIN.order,
        )

    def close(self) -> None:
        if not self._closed:
            self._service.close()
            self._closed = True

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        ensure_release_path(release, self._database_path)
        metadata = self._service.metadata(text_catalog=getattr(self, 'text_catalog', None))
        self._ensure_metadata(release, metadata)
        page = self._service.list_forks(query=query, page=1, page_size=200)
        end = offset + limit
        return CatalogPage(
            items=tuple(
                self._item(item, text_catalog=getattr(self, 'text_catalog', None))
                for item in page.items[offset:end]
            ),
            total=page.total_items,
            offset=offset,
            limit=limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str,
    ) -> CatalogDetail | None:
        ensure_release_path(release, self._database_path)
        metadata = self._service.metadata(text_catalog=getattr(self, 'text_catalog', None))
        self._ensure_metadata(release, metadata)
        detail = self._service.get_fork(str(record_id), text_catalog=getattr(self, 'text_catalog', None))
        if detail is None:
            return None
        sections = [
            self._identity(detail, text_catalog=getattr(self, 'text_catalog', None)),
            self._capabilities(detail, metadata, text_catalog=getattr(self, 'text_catalog', None)),
            self._growth(detail, text_catalog=getattr(self, 'text_catalog', None)),
            self._breakthroughs(detail, text_catalog=getattr(self, 'text_catalog', None)),
            self._refinements(detail, text_catalog=getattr(self, 'text_catalog', None)),
        ]
        if detail.buff_definitions:
            sections.append(self._buffs(detail, text_catalog=getattr(self, 'text_catalog', None)))
        if detail.resources or detail.relations:
            sections.append(self._relations(detail, text_catalog=getattr(self, 'text_catalog', None)))
        return CatalogDetail(
            item=self._item(detail.summary, text_catalog=getattr(self, 'text_catalog', None)),
            sections=tuple(sections),
            notes=tuple((*detail.audit_notes, *metadata.audit_notes)),
        )

    @staticmethod
    def _ensure_metadata(release: StaticCatalogRelease, metadata: object) -> None:
        ensure_release_metadata(
            release,
            dataset_id=str(getattr(metadata, "dataset_id")),
            schema_version=int(getattr(metadata, "schema_version")),
            importer_version=int(getattr(metadata, "importer_version")),
            built_at_utc=str(getattr(metadata, "built_at_utc")),
        )

    @staticmethod
    def _item(summary: ForkCatalogSummary, *, text_catalog=None) -> CatalogItem:
        subtitle = localized_text(
            text_catalog,
            "static_catalog.fork.item.subtitle",
            "{quality} · {type_name} · 突破 {breakthrough} · 混频 {refinement}",
            quality=summary.quality,
            type_name=summary.fork_type_name_zh or summary.raw_group_type
            or localized_text(text_catalog, "static_catalog.fork.value.unclassified", "未分类"),
            breakthrough=f"{summary.max_breakthrough}" if summary.max_breakthrough is not None
            else localized_text(text_catalog, "static_catalog.fork.value.unknown", "未知"),
            refinement=f"{summary.max_refinement}" if summary.max_refinement is not None
            else localized_text(text_catalog, "static_catalog.fork.value.unknown", "未知"),
        )
        return CatalogItem(
            domain_key=FORK_DOMAIN.key,
            record_id=summary.fork_id,
            title=summary.name_zh,
            subtitle=subtitle,
        )

    @staticmethod
    def _identity(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        item = detail.summary
        missing = localized_text(text_catalog, "static_catalog.fork.value.unavailable", "未保留")
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.identity.caption_2'),
            fields=(
                official("fork_id", item.fork_id, copyable=True),
                official(ui_enum_text(text_catalog, "中文名"), item.name_zh),
                official(ui_enum_text(text_catalog, "品质"), item.quality),
                official(
                    ui_enum_text(text_catalog, "类型"),
                    f"{item.fork_type_name_zh or missing} ({item.fork_type_id})",
                ),
                official(ui_enum_text(text_catalog, "说明"), item.description_zh),
                official(ui_enum_text(text_catalog, "升级包"), detail.upgrade_pack_id, copyable=True),
                official(ui_enum_text(text_catalog, "突破包"), detail.breakthrough_pack_id, copyable=True),
                official(ui_enum_text(text_catalog, "混频数据包"), detail.star_pack_id, copyable=True),
                official(
                    ui_enum_text(text_catalog, "名称文本键"),
                    f"{detail.name_text_table or missing}:{detail.name_text_key or missing}",
                ),
            ),
        )

    @staticmethod
    def _capabilities(detail: ForkCatalogDetail, metadata: object, *, text_catalog=None) -> CatalogSection:
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.availability.caption'),
            fields=(
                derived(ui_enum_text(text_catalog, "等级成长行"), len(detail.growth_levels)),
                derived(ui_enum_text(text_catalog, "突破阶段"), len(detail.breakthroughs)),
                derived(ui_enum_text(text_catalog, "混频等级"), len(detail.refinement_levels)),
                derived(ui_enum_text(text_catalog, "Buff 定义"), len(detail.buff_definitions)),
                derived(
                    ui_enum_text(text_catalog, "独立弧盘技能表"),
                    "available" if bool(getattr(metadata, "has_fork_skill_tables"))
                    else localized_text(
                        text_catalog,
                        "static_catalog.fork.capabilities.fork_skills_missing",
                        "unavailable：schema v30 没有 fork_skill / fork_skill_level；以混频 1–5 级描述、参数和 Buff 展示技能",
                    ),
                ),
                derived(
                    ui_enum_text(text_catalog, "原始 source_row payload"),
                    "available" if int(getattr(metadata, "source_payloads_preserved")) > 0
                    else localized_text(
                        text_catalog,
                        "static_catalog.fork.capabilities.source_payload_missing",
                        "unavailable：发行库省略原始 payload，只保留来源路径、行键和哈希",
                    ),
                ),
            ),
        )

    @staticmethod
    def _growth(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.growth.caption'),
            fields=tuple(
                official(
                    f"Lv.{row.level} · NeedExp {row.need_exp}",
                    lines([
                        f"{modifier.property_name_zh or modifier.property_id} "
                        f"{modifier.display_value} ({modifier.operation})"
                        for modifier in row.modifiers
                    ]),
                )
                for row in detail.growth_levels
            ) or (
                derived(
                    ui_enum_text(text_catalog, "成长数据"),
                    localized_text(
                        text_catalog,
                        "static_catalog.fork.growth.unavailable",
                        "unavailable：该弧盘没有正式成长行",
                    ),
                ),
            ),
        )

    @staticmethod
    def _breakthroughs(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        fields = []
        empty = localized_text(text_catalog, "static_catalog.fork.breakthroughs.field_empty", "正式字段为空")
        for row in detail.breakthroughs:
            item_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.item_costs
            ) or empty
            gold_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.gold_costs
            ) or empty
            modifiers = "; ".join(
                f"{modifier.property_name_zh or modifier.property_id} {modifier.display_value}"
                for modifier in row.modifiers
            ) or localized_text(text_catalog, "static_catalog.fork.breakthroughs.no_panel", "无面板修改")
            fields.append(official(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.breakthroughs.stage",
                    "阶段 {stage} · 上限 Lv.{max_level}",
                    stage=f"{row.stage}",
                    max_level=f"{row.max_fork_level}",
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.breakthroughs.costs",
                    "材料：{items}；货币消耗：{gold}；面板：{panel}",
                    items=item_costs,
                    gold=gold_costs,
                    panel=modifiers,
                ),
            ))
        for state in detail.critical_level_states:
            fields.append(derived(
                f"Lv.{state.level} {state.state}",
                localized_text(
                    text_catalog,
                    "static_catalog.fork.breakthroughs.critical",
                    "突破阶段 {stage} / NeedExp {need_exp}",
                    stage=f"{state.stage}",
                    need_exp=f"{state.growth.need_exp}",
                ),
            ))
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.breakthroughs.caption'),
            fields=tuple(fields) or (
                derived(
                    ui_enum_text(text_catalog, "突破数据"),
                    localized_text(
                        text_catalog,
                        "static_catalog.fork.breakthroughs.unavailable",
                        "unavailable：该弧盘没有正式突破行",
                    ),
                ),
            ),
        )

    @staticmethod
    def _refinements(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        none_word = ui_enum_text(text_catalog, "无")
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.refinements.caption'),
            fields=tuple(
                official(
                    localized_text(
                        text_catalog,
                        "static_catalog.fork.refinements.title",
                        "混频 {level} · {title}",
                        level=f"{row.level}",
                        title=row.title_zh or ui_enum_text(text_catalog, "未保留标题"),
                    ),
                    lines([
                        row.description_zh or localized_text(
                            text_catalog,
                            "static_catalog.fork.refinements.no_description",
                            "无正式说明",
                        ),
                        localized_text(
                            text_catalog,
                            "static_catalog.fork.refinements.parameters",
                            "参数：{parameters}",
                            parameters=", ".join(
                                f"{parameter.name_id}={parameter.display_value}"
                                for parameter in row.parameters
                            ) or none_word,
                        ),
                        localized_text(
                            text_catalog,
                            "static_catalog.fork.refinements.need_gold",
                            "原始 need_gold 字段：{value}",
                            value=row.need_gold_raw or ui_enum_text(text_catalog, "空"),
                        ),
                        localized_text(
                            text_catalog,
                            "static_catalog.fork.refinements.buffs",
                            "Buff：{assets}",
                            assets=", ".join(row.buff_asset_paths) or none_word,
                        ),
                    ]),
                )
                for row in detail.refinement_levels
            ) or (
                derived(
                    ui_enum_text(text_catalog, "混频技能"),
                    localized_text(
                        text_catalog,
                        "static_catalog.fork.refinements.unavailable",
                        "unavailable：没有混频等级行",
                    ),
                ),
            ),
        )

    @staticmethod
    def _buffs(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        fields = []
        none_word = ui_enum_text(text_catalog, "无")
        unresolved = localized_text(text_catalog, "static_catalog.fork.value.unresolved", "未解析")
        retained = localized_text(text_catalog, "static_catalog.fork.value.unavailable", "未保留")
        for buff in detail.buff_definitions:
            source = (
                CatalogValueSource.OFFICIAL_STATIC
                if buff.target_available else CatalogValueSource.PROJECT_ANNOTATION
            )
            values = [
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.asset",
                    "资产={path}",
                    path=buff.asset_path,
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.definition",
                    "定义={value}",
                    value=buff.definition_id or unresolved,
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.available",
                    "可用={value}",
                    value=f"{buff.target_available}",
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.duration",
                    "持续={value}",
                    value=buff.duration_policy or retained,
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.stacking",
                    "叠层={stacking} / {limit}",
                    stacking=buff.stacking_type or retained,
                    limit=f"{buff.stack_limit_count}",
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.gameplay_effect",
                    "GE={value}",
                    value=buff.gameplay_effect_id or unresolved,
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.modifiers",
                    "修改={modifiers}",
                    modifiers=", ".join(
                        f"{row.property_name_zh or row.property_id}:{row.magnitude_kind}={row.magnitude_value}"
                        for row in buff.modifiers
                    ) or none_word,
                ),
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.triggers",
                    "触发={triggers}",
                    triggers=", ".join(
                        f"{row.event_type or ui_enum_text(text_catalog, '未知')}->"
                        f"{row.target_gameplay_effect_id or row.target_effect_asset_path or unresolved}"
                        for row in buff.triggers
                    ) or none_word,
                ),
            ]
            fields.append(official(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.title",
                    "混频 {level} · {definition}",
                    level=f"{buff.refinement_level}",
                    definition=buff.definition_id or buff.asset_path,
                ),
                lines(values),
                copyable=True,
            ) if source is CatalogValueSource.OFFICIAL_STATIC else annotation(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.buffs.title_missing",
                    "混频 {level} · 未导入目标",
                    level=f"{buff.refinement_level}",
                ),
                lines(values),
                copyable=True,
            ))
        return CatalogSection(title=resolve_text_catalog(text_catalog).render('ui.static_catalog.buffs.caption'), fields=tuple(fields))

    @staticmethod
    def _relations(detail: ForkCatalogDetail, *, text_catalog=None) -> CatalogSection:
        references = tuple(
            CatalogReference(ui_enum_text(text_catalog, "查看关联角色"), "character", relation.target_id)
            for relation in detail.relations
            if relation.available and relation.kind == "character" and relation.target_id
        )
        fields = [
            official(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.relations.resource",
                    "资源 · {kind}",
                    kind=resource.kind,
                ),
                resource.path,
                copyable=True,
            )
            if resource.origin is CatalogOrigin.OFFICIAL_STATIC
            else annotation(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.relations.resource",
                    "资源 · {kind}",
                    kind=resource.kind,
                ),
                resource.path,
                copyable=True,
            )
            for resource in detail.resources
        ]
        fields.extend(
            official(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.relations.relation",
                    "关系 · {kind}",
                    kind=relation.kind,
                ),
                f"{relation.label} · {relation.copy_value}",
                copyable=True,
            ) if relation.origin is CatalogOrigin.OFFICIAL_STATIC else annotation(
                localized_text(
                    text_catalog,
                    "static_catalog.fork.relations.relation",
                    "关系 · {kind}",
                    kind=relation.kind,
                ),
                f"{relation.label} · {relation.copy_value}"
                + ("" if relation.available else " · " + localized_text(
                    text_catalog,
                    "static_catalog.fork.relations.target_missing",
                    "unavailable：目标未导入",
                )),
                copyable=True,
            )
            for relation in detail.relations
        )
        return CatalogSection(
            title=resolve_text_catalog(text_catalog).render('ui.static_catalog.relations.caption'),
            fields=tuple(fields),
            references=references,
        )
