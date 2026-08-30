# 将怪物与玩法资料映射到公共只读提供器契约。
"""Adapt monster and encounter DTOs to the public static-catalog contract."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.features.static_catalog.providers._adapter_common import (
    source_for,
    validate_release_identity,
    validate_release_path,
)
from src.services.static_catalog_monster_service import (
    CatalogDetail as MonsterDetail,
    CatalogEntry,
    CatalogFilter,
    StaticCatalogMonsterService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


class StaticCatalogMonsterProvider:
    """One public provider for formal monster identities and encounter data."""

    domain = CatalogDomain(
        key="monsters",
        label="怪物与玩法",
        description="图鉴、模板画像、争锋、轨外、副本、异象追猎与高危委托",
        order=30,
    )

    def __init__(
        self,
        database_path: str | Path,
        *,
        terminology_service: StaticCatalogTerminologyService,
        close_callbacks: tuple[Callable[[], None], ...] = (),
    ) -> None:
        self._database_path = Path(database_path).expanduser().resolve()
        self._service = StaticCatalogMonsterService.from_database(
            self._database_path,
            terminology_service=terminology_service,
            mainland_now=datetime.now(timezone(timedelta(hours=8))).replace(
                tzinfo=None
            ),
        )
        self._close_callbacks = close_callbacks
        self._closed = False

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            errors: list[Exception] = []
            try:
                self._service.close()
            except Exception as exc:
                errors.append(exc)
            for callback in self._close_callbacks:
                try:
                    callback()
                except Exception as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError(
                    f"关闭怪物资料提供器时有 {len(errors)} 个组件失败"
                ) from errors[0]

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        self._validate_release(release)
        page = self._service.list_entries(
            CatalogFilter(
                search=query,
                offset=max(0, int(offset)),
                page_size=max(1, min(int(limit), 200)),
            )
        )
        return CatalogPage(
            items=tuple(self._item(entry) for entry in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.page_size,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        self._validate_release(release)
        detail = self._service.get_detail(record_id)
        return None if detail is None else self._detail(detail)

    def _validate_release(self, release: StaticCatalogRelease) -> None:
        if self._closed:
            raise RuntimeError("怪物资料适配器已关闭")
        validate_release_path(release, self._database_path)
        dataset = self._service.dataset()
        validate_release_identity(
            release,
            dataset_id=dataset.dataset_id,
            schema_version=dataset.schema_version,
            importer_version=dataset.importer_version,
            built_at_utc=dataset.built_at_utc,
        )

    @classmethod
    def _item(cls, entry: CatalogEntry) -> CatalogItem:
        return CatalogItem(
            domain_key=cls.domain.key,
            record_id=entry.key,
            title=entry.title,
            subtitle=entry.subtitle,
            source=CatalogValueSource.OFFICIAL_STATIC,
        )

    @classmethod
    def _detail(cls, detail: MonsterDetail) -> CatalogDetail:
        sections: list[CatalogSection] = []
        for section in detail.sections:
            values: list[CatalogField] = []
            for value in section.values:
                values.append(
                    CatalogField(
                        label=value.label,
                        value=value.value,
                        source=source_for(value.provenance),
                        copyable=value.copyable,
                    )
                )
                if value.note:
                    values.append(
                        CatalogField(
                            label=f"{value.label}说明",
                            value=value.note,
                            source=CatalogValueSource.PROJECT_ANNOTATION,
                        )
                    )
            references = tuple(
                CatalogReference(
                    label=relation.label,
                    domain_key=cls.domain.key,
                    record_id=relation.target_key,
                )
                for relation in detail.relations
                if relation.target_key
            )
            sections.append(
                CatalogSection(
                    title=section.title,
                    fields=tuple(values),
                    references=references if not sections else (),
                )
            )
            if section.note:
                detail_note = CatalogSection(
                    title=f"{section.title}说明",
                    fields=(
                        CatalogField(
                            label="说明",
                            value=section.note,
                            source=CatalogValueSource.PROJECT_ANNOTATION,
                        ),
                    ),
                )
                sections.append(detail_note)
        if not sections and detail.relations:
            sections.append(
                CatalogSection(
                    title="关联资料",
                    fields=(),
                    references=tuple(
                        CatalogReference(
                            label=relation.label,
                            domain_key=cls.domain.key,
                            record_id=relation.target_key,
                        )
                        for relation in detail.relations
                        if relation.target_key
                    ),
                )
            )
        return CatalogDetail(
            item=cls._item(detail.entry),
            sections=tuple(sections),
            notes=detail.notices,
        )
