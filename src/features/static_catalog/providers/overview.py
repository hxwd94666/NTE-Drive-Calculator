# 将全量静态表覆盖登记映射到公共资料库契约。
"""Public provider for the audited 125-table static release registry."""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import resolve_text_catalog
from src.domain.localization.catalog import localized_text
from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogPage,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.storage.sqlite.catalog.static_catalog_overview_queries import (
    STATIC_TABLE_CATALOG,
    StaticCatalogOverviewQueries,
    StaticTableOverview,
)


_STATE_LABELS = {
    "A": "完整正式目录",
    "B": "已公开，含高级证据",
    "C": "可展示，存在结构化缺口",
    "D": "仅有正式 ID 或有限证据",
    "E": "空表或发行 payload 明确省略",
}


class StaticCatalogOverviewProvider:
    """Expose every normalized table, including empty and omitted-payload facts."""

    @property
    def domain(self) -> CatalogDomain:
        catalog = getattr(self, "text_catalog", None)
        return CatalogDomain(
            key="coverage",
            label=localized_text(catalog, "static_catalog.overview.domain.label", "覆盖总览"),
            description=localized_text(
                catalog,
                "static_catalog.overview.domain.description",
                "发行静态库 125 张表的逐表行数、领域与可展示状态",
            ),
            order=0,
        )

    def __init__(self, database_path: str, *, text_catalog=None) -> None:
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._queries = StaticCatalogOverviewQueries(database_path)
        self._rows = self._queries.list_tables()

    def close(self) -> None:
        self._queries.close()

    @staticmethod
    def _ensure_release(release: StaticCatalogRelease, database_path: str) -> None:
        if release.database_path != Path(database_path).resolve():
            raise RuntimeError("覆盖总览与当前冻结发行路径不一致")

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        self._ensure_release(release, str(self._queries.database_path))
        needle = query.casefold()
        matched = tuple(
            row
            for row in self._rows
            if not needle
            or needle in row.name.casefold()
            or needle in row.domain.casefold()
            or needle in ui_enum_text(getattr(self, 'text_catalog', None), _STATE_LABELS)[row.coverage_state].casefold()
        )
        return CatalogPage(
            items=tuple(self._item(row) for row in matched[offset : offset + limit]),
            total=len(matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self, release: StaticCatalogRelease, record_id: str
    ) -> CatalogDetail | None:
        self._ensure_release(release, str(self._queries.database_path))
        row = next((item for item in self._rows if item.name == record_id), None)
        if row is None:
            return None
        notes = []
        catalog = getattr(self, "text_catalog", None)
        if row.name == "source_row" and release.source_payloads_omitted:
            notes.append(localized_text(
                catalog,
                "static_catalog.overview.note.source_row_omitted",
                "发行清单明确省略 source_row.payload_json；来源键和内容哈希仍可追溯。",
            ))
        if row.rows == 0:
            notes.append(localized_text(
                catalog,
                "static_catalog.overview.note.empty_table",
                "该表属于正式 schema，但本次发行没有记录；不会用 0 伪装未知业务值。",
            ))
        return CatalogDetail(
            item=self._item(row),
            sections=(
                CatalogSection(
                    title=resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.caption_3'),
                    fields=(
                        self._field(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self'), row.name, copyable=True),
                        self._field(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_2'), row.domain),
                        self._field(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_3'), f"{row.rows:,}"),
                        self._field(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_4'), f"{row.coverage_state} · {ui_enum_text(getattr(self, 'text_catalog', None), _STATE_LABELS)[row.coverage_state]}"),
                        self._field("Dataset", release.dataset_id, copyable=True),
                        self._field("Schema", f"v{release.schema_version}"),
                        self._field("Importer", f"v{release.importer_version}"),
                        self._field(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_5'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_6') if release.read_only else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.static_catalog.detail.self_7')),
                    ),
                ),
            ),
            notes=tuple(notes),
        )

    def _item(self, row: StaticTableOverview) -> CatalogItem:
        return CatalogItem(
            domain_key="coverage",
            record_id=row.name,
            title=row.name,
            subtitle=localized_text(
                getattr(self, "text_catalog", None),
                "static_catalog.overview.item.subtitle",
                "{domain} · {rows} 行 · 状态 {state}",
                domain=row.domain,
                rows=f"{row.rows:,}",
                state=row.coverage_state,
            ),
        )

    @staticmethod
    def _field(label: str, value: str, *, copyable: bool = False) -> CatalogField:
        return CatalogField(label, value, CatalogValueSource.OFFICIAL_STATIC, copyable)


def registered_static_table_count() -> int:
    """Executable gate used by tests and composition audits."""

    return len(STATIC_TABLE_CATALOG)
