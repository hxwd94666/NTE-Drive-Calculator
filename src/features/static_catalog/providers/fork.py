# 将弧盘资料域映射到公共只读提供器契约。
"""Adapt the fork catalog domain to the common read-only provider contract."""

from __future__ import annotations

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
from src.services.static_catalog_fork_service import (
    CatalogOrigin,
    ForkCatalogDetail,
    ForkCatalogSummary,
    StaticCatalogForkService,
)


FORK_DOMAIN = CatalogDomain(
    key="fork",
    label="弧盘数据",
    description="升级经验与面板、突破消耗、精炼技能、Buff、资源和角色关系",
    order=20,
)


class ForkCatalogProvider:
    """Own one read-only fork service and adapt it without Qt or SQL."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).resolve()
        self._service = StaticCatalogForkService.from_database(self._database_path)
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        return FORK_DOMAIN

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
        metadata = self._service.metadata()
        self._ensure_metadata(release, metadata)
        page = self._service.list_forks(query=query, page=1, page_size=200)
        end = offset + limit
        return CatalogPage(
            items=tuple(self._item(item) for item in page.items[offset:end]),
            total=page.total_items,
            offset=offset,
            limit=limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str,
    ) -> CatalogDetail | None:
        ensure_release_path(release, self._database_path)
        metadata = self._service.metadata()
        self._ensure_metadata(release, metadata)
        detail = self._service.get_fork(str(record_id))
        if detail is None:
            return None
        sections = [
            self._identity(detail),
            self._capabilities(detail, metadata),
            self._growth(detail),
            self._breakthroughs(detail),
            self._refinements(detail),
        ]
        if detail.buff_definitions:
            sections.append(self._buffs(detail))
        if detail.resources or detail.relations:
            sections.append(self._relations(detail))
        return CatalogDetail(
            item=self._item(detail.summary),
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
    def _item(summary: ForkCatalogSummary) -> CatalogItem:
        subtitle = (
            f"{summary.quality} · {summary.fork_type_name_zh or summary.raw_group_type or '未分类'}"
            f" · 突破 {summary.max_breakthrough if summary.max_breakthrough is not None else '未知'}"
            f" · 精炼 {summary.max_refinement if summary.max_refinement is not None else '未知'}"
        )
        return CatalogItem(
            domain_key=FORK_DOMAIN.key,
            record_id=summary.fork_id,
            title=summary.name_zh,
            subtitle=subtitle,
        )

    @staticmethod
    def _identity(detail: ForkCatalogDetail) -> CatalogSection:
        item = detail.summary
        return CatalogSection(
            title="弧盘身份与资源",
            fields=(
                official("fork_id", item.fork_id, copyable=True),
                official("中文名", item.name_zh),
                official("品质", item.quality),
                official("类型", f"{item.fork_type_name_zh or '未保留'} ({item.fork_type_id})"),
                official("说明", item.description_zh),
                official("升级包", detail.upgrade_pack_id, copyable=True),
                official("突破包", detail.breakthrough_pack_id, copyable=True),
                official("精炼包", detail.star_pack_id, copyable=True),
                official("名称文本键", f"{detail.name_text_table or '未保留'}:{detail.name_text_key or '未保留'}"),
            ),
        )

    @staticmethod
    def _capabilities(detail: ForkCatalogDetail, metadata: object) -> CatalogSection:
        return CatalogSection(
            title="数据可用性",
            fields=(
                derived("等级成长行", len(detail.growth_levels)),
                derived("突破阶段", len(detail.breakthroughs)),
                derived("精炼等级", len(detail.refinement_levels)),
                derived("Buff 定义", len(detail.buff_definitions)),
                derived(
                    "独立弧盘技能表",
                    "available" if bool(getattr(metadata, "has_fork_skill_tables"))
                    else "unavailable：schema v29 没有 fork_skill / fork_skill_level；以精炼 1–5 级描述、参数和 Buff 展示技能",
                ),
                derived(
                    "原始 source_row payload",
                    "available" if int(getattr(metadata, "source_payloads_preserved")) > 0
                    else "unavailable：发行库省略原始 payload，只保留来源路径、行键和哈希",
                ),
            ),
        )

    @staticmethod
    def _growth(detail: ForkCatalogDetail) -> CatalogSection:
        return CatalogSection(
            title="1–80 级升级经验与面板修改",
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
            ) or (derived("成长数据", "unavailable：该弧盘没有正式成长行"),),
        )

    @staticmethod
    def _breakthroughs(detail: ForkCatalogDetail) -> CatalogSection:
        fields = []
        for row in detail.breakthroughs:
            item_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.item_costs
            ) or "正式字段为空"
            gold_costs = ", ".join(
                f"{cost.item_id} × {cost.amount if cost.amount is not None else cost.raw_value}"
                for cost in row.gold_costs
            ) or "正式字段为空"
            modifiers = "; ".join(
                f"{modifier.property_name_zh or modifier.property_id} {modifier.display_value}"
                for modifier in row.modifiers
            ) or "无面板修改"
            fields.append(official(
                f"阶段 {row.stage} · 上限 Lv.{row.max_fork_level}",
                f"材料：{item_costs}；金币：{gold_costs}；面板：{modifiers}",
            ))
        for state in detail.critical_level_states:
            fields.append(derived(
                f"Lv.{state.level} {state.state}",
                f"突破阶段 {state.stage} / NeedExp {state.growth.need_exp}",
            ))
        return CatalogSection(
            title="突破阶段、正式消耗与临界状态",
            fields=tuple(fields) or (derived("突破数据", "unavailable：该弧盘没有正式突破行"),),
        )

    @staticmethod
    def _refinements(detail: ForkCatalogDetail) -> CatalogSection:
        return CatalogSection(
            title="弧盘技能 / 精炼 1–5 级",
            fields=tuple(
                official(
                    f"精炼 {row.level} · {row.title_zh or '未保留标题'}",
                    lines([
                        row.description_zh or "无正式说明",
                        "参数：" + (", ".join(
                            f"{parameter.name_id}={parameter.display_value}"
                            for parameter in row.parameters
                        ) or "无"),
                        "金币字段：" + (row.need_gold_raw or "空"),
                        "Buff：" + (", ".join(row.buff_asset_paths) or "无"),
                    ]),
                )
                for row in detail.refinement_levels
            ) or (derived("精炼技能", "unavailable：没有精炼等级行"),),
        )

    @staticmethod
    def _buffs(detail: ForkCatalogDetail) -> CatalogSection:
        fields = []
        for buff in detail.buff_definitions:
            source = (
                CatalogValueSource.OFFICIAL_STATIC
                if buff.target_available else CatalogValueSource.PROJECT_ANNOTATION
            )
            values = [
                f"资产={buff.asset_path}",
                f"定义={buff.definition_id or '未解析'}",
                f"可用={buff.target_available}",
                f"持续={buff.duration_policy or '未保留'}",
                f"叠层={buff.stacking_type or '未保留'} / {buff.stack_limit_count}",
                f"GE={buff.gameplay_effect_id or '未解析'}",
                "修改=" + (", ".join(
                    f"{row.property_name_zh or row.property_id}:{row.magnitude_kind}={row.magnitude_value}"
                    for row in buff.modifiers
                ) or "无"),
                "触发=" + (", ".join(
                    f"{row.event_type or '未知'}->{row.target_gameplay_effect_id or row.target_effect_asset_path or '未解析'}"
                    for row in buff.triggers
                ) or "无"),
            ]
            fields.append(official(
                f"精炼 {buff.refinement_level} · {buff.definition_id or buff.asset_path}",
                lines(values),
                copyable=True,
            ) if source is CatalogValueSource.OFFICIAL_STATIC else annotation(
                f"精炼 {buff.refinement_level} · 未导入目标",
                lines(values),
                copyable=True,
            ))
        return CatalogSection(title="Buff、GE、modifier 与触发", fields=tuple(fields))

    @staticmethod
    def _relations(detail: ForkCatalogDetail) -> CatalogSection:
        references = tuple(
            CatalogReference("查看关联角色", "character", relation.target_id)
            for relation in detail.relations
            if relation.available and relation.kind == "character" and relation.target_id
        )
        fields = [
            official(f"资源 · {resource.kind}", resource.path, copyable=True)
            if resource.origin is CatalogOrigin.OFFICIAL_STATIC
            else annotation(f"资源 · {resource.kind}", resource.path, copyable=True)
            for resource in detail.resources
        ]
        fields.extend(
            official(
                f"关系 · {relation.kind}",
                f"{relation.label} · {relation.copy_value}",
                copyable=True,
            ) if relation.origin is CatalogOrigin.OFFICIAL_STATIC else annotation(
                f"关系 · {relation.kind}",
                f"{relation.label} · {relation.copy_value}"
                + ("" if relation.available else " · unavailable：目标未导入"),
                copyable=True,
            )
            for relation in detail.relations
        )
        return CatalogSection(
            title="资源路径与结构化关系",
            fields=tuple(fields),
            references=references,
        )
