# 将装备、效果、资源和来源资料映射到公共资料库。
"""Adapt equipment, effect, asset, and source DTOs to the public catalog."""

from __future__ import annotations

from pathlib import Path

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogItem,
    CatalogPage,
    CatalogReference,
    CatalogRelationGroup,
    CatalogRelationPage,
    CatalogSection,
    CatalogValueSource,
    StaticCatalogRelease,
)
from src.features.static_catalog.providers._adapter_common import (
    decode_typed_record_id,
    encode_typed_record_id,
    source_for,
    validate_release_identity,
    validate_release_path,
)
from src.services.static_catalog_misc_models import (
    CatalogDetail as MiscDetail,
    CatalogSearchItem,
    SourceTrace,
)
from src.services.static_catalog_misc_service import StaticCatalogMiscService


_DOMAIN_ORDER = {
    "equipment": 40,
    "skills": 50,
    "effects": 60,
    "assets": 70,
    "sources": 80,
}
_ENTITY_DOMAIN = {
    "equipment_item": "equipment",
    "equipment_suit": "equipment",
    "equipment_shape": "equipment",
    "equipment_attribute": "equipment",
    "equipment_curve": "equipment",
    "equipment_buff_curve": "equipment",
    "equipment_modify_pack": "equipment",
    "equipment_plan": "equipment",
    "graduation_template": "equipment",
    "gameplay_ability": "skills",
    "skill_damage": "skills",
    "gameplay_effect": "effects",
    "buff": "effects",
    "combat_effect": "effects",
    "combat_curve": "effects",
    "combat_level_curve": "effects",
    "reaction": "effects",
    "combat_constant": "effects",
    "gameplay_tag": "effects",
    "roguelike_modifier": "effects",
    "blueprint": "assets",
    "montage": "assets",
    "source_file": "sources",
    "source_row": "sources",
}
_RELATION_LABELS = {
    "references": "资源引用",
    "tags": "Gameplay Tag",
    "properties": "语义属性",
    "ability_effects": "GA → GE",
    "ability_montages": "GA → Montage",
    "notifies": "Montage Notify",
}
_ENTITY_RELATIONS = {
    "blueprint": frozenset({
        "references", "tags", "properties", "ability_effects", "ability_montages",
    }),
    "montage": frozenset({"notifies"}),
}


class StaticCatalogMiscProvider:
    """A parameterized provider for one bounded miscellaneous domain."""

    def __init__(
        self,
        database_path: str | Path,
        manifest_path: str | Path,
        *,
        domain_key: str,
    ) -> None:
        if domain_key not in _DOMAIN_ORDER:
            raise ValueError(f"不支持的杂项资料域：{domain_key!r}")
        self._database_path = Path(database_path).expanduser().resolve()
        self._service = StaticCatalogMiscService(
            self._database_path,
            manifest_path=manifest_path,
        )
        domain = next(
            item for item in self._service.domains() if item.key == domain_key
        )
        self._domain_key = domain_key
        self._domain = CatalogDomain(
            key=domain.key,
            label=domain.title,
            description=domain.description,
            order=_DOMAIN_ORDER[domain_key],
        )
        self._closed = False

    @property
    def domain(self) -> CatalogDomain:
        return self._domain

    def close(self) -> None:
        self._closed = True

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        self._validate_release(release)
        page = self._service.search(
            self._domain_key,
            query,
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 100)),
        )
        return CatalogPage(
            items=tuple(self._item(item) for item in page.items),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        self._validate_release(release)
        entity_kind, entity_key = decode_typed_record_id(record_id)
        if _ENTITY_DOMAIN.get(entity_kind) != self._domain_key:
            return None
        try:
            detail = self._service.detail(entity_kind, entity_key)
        except LookupError:
            return None
        if isinstance(detail, SourceTrace):
            return self._source_detail(record_id, detail)
        return self._detail(detail)

    def relations(
        self,
        release: StaticCatalogRelease,
        record_id: str,
        relation_kind: str,
        *,
        offset: int,
        limit: int,
    ) -> CatalogRelationPage:
        """Load one bounded asset-relation page without expanding the detail."""

        self._validate_release(release)
        entity_kind, entity_key = decode_typed_record_id(record_id)
        if (
            self._domain_key != "assets"
            or relation_kind not in _ENTITY_RELATIONS.get(entity_kind, ())
        ):
            raise ValueError("该资料不支持此分页关系")
        page = self._service.asset_relations(
            entity_kind,
            entity_key,
            relation_kind,
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 100)),
        )
        return CatalogRelationPage(
            relation_kind=relation_kind,
            rows=tuple(self._relation_section(relation_kind, row) for row in page.rows),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        )

    def _validate_release(self, release: StaticCatalogRelease) -> None:
        if self._closed:
            raise RuntimeError("杂项资料适配器已关闭")
        validate_release_path(release, self._database_path)
        metadata = self._service.release_metadata()
        validate_release_identity(
            release,
            dataset_id=metadata.dataset_id,
            schema_version=metadata.schema_version,
            importer_version=metadata.importer_version,
            built_at_utc=metadata.generated_at_utc,
        )
        if release.source_payloads_omitted != metadata.source_payloads_omitted:
            raise RuntimeError("资料库请求期间来源 payload 发行策略已变化")

    def _item(self, item: CatalogSearchItem) -> CatalogItem:
        return CatalogItem(
            domain_key=self._domain.key,
            record_id=encode_typed_record_id(item.entity_kind, item.entity_key),
            title=item.title,
            subtitle=item.subtitle,
            source=source_for(item.origin_kind),
        )

    def _detail(self, detail: MiscDetail) -> CatalogDetail:
        references = tuple(
            reference
            for relation in detail.relations
            if (reference := self._reference(relation)) is not None
        )
        sections = [
            CatalogSection(
                title=section.title,
                fields=tuple(
                    CatalogField(
                        label=value.label,
                        value=value.value,
                        source=source_for(value.origin_kind),
                        copyable=value.copy_kind is not None,
                    )
                    for value in section.fields
                ),
                references=references if index == 0 else (),
            )
            for index, section in enumerate(detail.sections)
        ]
        if not sections:
            sections.append(
                CatalogSection("关联资料", fields=(), references=references)
            )
        trace_references = list(sections[0].references)
        if detail.source_file_id is not None:
            trace_references.append(
                CatalogReference(
                    label="查看来源文件",
                    domain_key="sources",
                    record_id=encode_typed_record_id(
                        "source_file", detail.source_file_id
                    ),
                )
            )
        if detail.source_row_id is not None:
            trace_references.append(
                CatalogReference(
                    label="查看来源行",
                    domain_key="sources",
                    record_id=encode_typed_record_id(
                        "source_row", detail.source_row_id
                    ),
                )
            )
        if trace_references:
            sections[0] = CatalogSection(
                title=sections[0].title,
                fields=sections[0].fields,
                references=tuple(trace_references),
            )
        return CatalogDetail(
            item=CatalogItem(
                domain_key=self._domain.key,
                record_id=encode_typed_record_id(
                    detail.entity_kind, detail.entity_key
                ),
                title=detail.title,
                subtitle=detail.subtitle,
                source=source_for(detail.origin_kind),
            ),
            sections=tuple(sections),
            relation_groups=self._relation_groups(detail),
        )

    @staticmethod
    def _relation_groups(detail: MiscDetail) -> tuple[CatalogRelationGroup, ...]:
        supported = _ENTITY_RELATIONS.get(detail.entity_kind, ())
        counts = next(
            (section for section in detail.sections if section.title == "分页关系规模"),
            None,
        )
        if counts is None:
            return ()
        return tuple(
            CatalogRelationGroup(field.label, _RELATION_LABELS[field.label], int(field.value))
            for field in counts.fields
            if field.label in supported and int(field.value) > 0
        )

    @classmethod
    def _relation_section(cls, relation_kind: str, row: object) -> CatalogSection:
        fields = tuple(
            CatalogField(
                label=value.label,
                value=value.value,
                source=source_for(value.origin_kind),
                copyable=value.copy_kind is not None,
            )
            for value in getattr(row, "fields", ())
        )
        reference = cls._relation_reference(relation_kind, fields)
        return CatalogSection(
            title=str(getattr(row, "title", _RELATION_LABELS[relation_kind])),
            fields=fields,
            references=(reference,) if reference is not None else (),
        )

    @staticmethod
    def _relation_reference(
        relation_kind: str,
        fields: tuple[CatalogField, ...],
    ) -> CatalogReference | None:
        values = {field.label: field.value for field in fields}
        if relation_kind == "references":
            if (
                values.get("target_available") != "是"
                or values.get("catalog_detail_available") != "是"
            ):
                return None
            target_kind, target_key, label = (
                "blueprint", values.get("target_asset_path"), "查看目标资源"
            )
        elif relation_kind == "tags":
            source_asset_path = values.get("来源资源路径")
            tag_name = values.get("Gameplay Tag")
            target_kind, target_key, label = (
                "gameplay_tag",
                (
                    f"{source_asset_path}{chr(31)}{tag_name}"
                    if source_asset_path and tag_name else None
                ),
                "查看 Gameplay Tag",
            )
        elif relation_kind == "ability_effects":
            if values.get("target_available") != "是":
                return None
            target_kind, target_key, label = (
                "gameplay_effect", values.get("effect_id"), "查看 GE"
            )
        elif relation_kind == "ability_montages":
            if values.get("target_available") != "是":
                return None
            target_kind, target_key, label = (
                "montage", values.get("montage_asset_path"), "查看 Montage"
            )
        else:
            return None
        target_domain = _ENTITY_DOMAIN.get(target_kind)
        if not target_domain or not target_key:
            return None
        return CatalogReference(
            label=label,
            domain_key=target_domain,
            record_id=encode_typed_record_id(target_kind, target_key),
        )

    @staticmethod
    def _reference(relation: object) -> CatalogReference | None:
        target_kind = str(getattr(relation, "target_kind", ""))
        target_key = str(getattr(relation, "target_key", ""))
        target_domain = _ENTITY_DOMAIN.get(target_kind)
        if not target_domain or not target_key:
            if target_kind == "fork" and target_key:
                return CatalogReference(
                    label=str(getattr(relation, "label", "查看弧盘")),
                    domain_key="fork",
                    record_id=target_key,
                )
            return None
        return CatalogReference(
            label=str(getattr(relation, "label", "查看关联资料")),
            domain_key=target_domain,
            record_id=encode_typed_record_id(target_kind, target_key),
        )

    def _source_detail(
        self,
        record_id: str,
        trace: SourceTrace,
    ) -> CatalogDetail:
        fields = (
            CatalogField(
                "相对路径",
                trace.relative_path,
                CatalogValueSource.OFFICIAL_STATIC,
                True,
            ),
            CatalogField(
                "来源文件 SHA-256",
                trace.source_file_sha256,
                CatalogValueSource.OFFICIAL_STATIC,
                True,
            ),
            CatalogField(
                "声明行数",
                str(trace.declared_row_count),
                CatalogValueSource.OFFICIAL_STATIC,
            ),
            CatalogField(
                "来源行 key",
                trace.row_key or "不可用",
                CatalogValueSource.OFFICIAL_STATIC,
                trace.row_key is not None,
            ),
            CatalogField(
                "内容 SHA-256",
                trace.content_sha256 or "不可用",
                CatalogValueSource.OFFICIAL_STATIC,
                trace.content_sha256 is not None,
            ),
            CatalogField(
                "原始 payload",
                "已保留" if trace.payload_present else "发行包未提供",
                CatalogValueSource.DERIVED_DISPLAY,
            ),
        )
        return CatalogDetail(
            item=CatalogItem(
                domain_key=self._domain.key,
                record_id=record_id,
                title=trace.row_key or trace.relative_path,
                subtitle="来源追溯",
                source=CatalogValueSource.OFFICIAL_STATIC,
            ),
            sections=(CatalogSection("来源证据", fields),),
            notes=(trace.explanation,),
        )
