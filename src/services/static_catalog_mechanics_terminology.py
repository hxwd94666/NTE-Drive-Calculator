# 把既有杂项目录适配为公共正式术语 Service 的只读来源。
"""Terminology adapter for combat-mechanics catalog projections."""

from __future__ import annotations

import re

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.services.static_catalog_misc_models import CatalogDetail
from src.services.static_catalog_misc_service import StaticCatalogMiscService
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_NAME_LABELS = ("中文名", "显示名", "名称")
_TEXT_TABLE_LABELS = ("名称文本表", "文本表")
_TEXT_KEY_LABELS = ("名称文本键", "文本键")


class MechanicsCatalogTerminologySource:
    """Read canonical names from normalized static-catalog details only."""

    def __init__(self, misc_service: StaticCatalogMiscService) -> None:
        self._misc = misc_service

    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None:
        del context
        try:
            detail = self._misc.detail(entity_kind, stable_id)
        except LookupError:
            return None
        if not isinstance(detail, CatalogDetail):
            return None
        fields = {
            field.label: str(field.value).strip()
            for section in detail.sections
            for field in section.fields
        }
        display_name = next(
            (
                fields[label]
                for label in _NAME_LABELS
                if fields.get(label) and fields[label] != "—"
            ),
            None,
        )
        if display_name is None and re.search(r"[\u3400-\u9fff]", detail.title):
            display_name = detail.title.strip()
        if not display_name:
            return None
        return LocalizedTermRecord(
            entity_kind=entity_kind,
            canonical_id=stable_id,
            names={"zh-CN": display_name},
            text_table=next(
                (fields[label] for label in _TEXT_TABLE_LABELS if fields.get(label)),
                None,
            ),
            text_key=next(
                (fields[label] for label in _TEXT_KEY_LABELS if fields.get(label)),
                None,
            ),
        )


def build_mechanics_terminology_service(
    misc_service: StaticCatalogMiscService,
) -> StaticCatalogTerminologyService:
    return StaticCatalogTerminologyService(
        MechanicsCatalogTerminologySource(misc_service)
    )


__all__ = ["MechanicsCatalogTerminologySource", "build_mechanics_terminology_service"]
