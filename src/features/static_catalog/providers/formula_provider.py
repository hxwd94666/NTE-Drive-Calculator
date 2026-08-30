# 将公式证据和反事实支持矩阵映射到公共资料库。
"""Adapt formula evidence and counterfactual support to the public catalog."""

from __future__ import annotations

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
from src.features.static_catalog.providers._adapter_common import (
    validate_release_identity,
    validate_release_path,
)
from src.services.static_catalog_formula_service import (
    CounterfactualSupportEntry,
    FormulaEntry,
    StaticCatalogFormulaDomain,
    StaticCatalogFormulaService,
)


_NATIVE_CORE_NOTE = (
    "独立 C++ sidecar 仅用于与 Python 金标准做差分验证，不是生产执行入口；"
    "本页的生产能力状态仍按当前消费者证据报告，不能据此选择或启用执行器。"
)


class _FormulaProviderBase:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path).expanduser().resolve()
        self._service = StaticCatalogFormulaService(self._database_path)
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def _load(self, release: StaticCatalogRelease) -> StaticCatalogFormulaDomain:
        if self._closed:
            raise RuntimeError("公式资料适配器已关闭")
        validate_release_path(release, self._database_path)
        domain = self._service.load()
        snapshot = domain.evidence_snapshot
        validate_release_identity(
            release,
            dataset_id=snapshot.dataset_id,
            schema_version=snapshot.schema_version,
            importer_version=snapshot.importer_version,
        )
        return domain

    @staticmethod
    def _page(
        items: tuple[CatalogItem, ...],
        *,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(int(limit), 200))
        return CatalogPage(
            items=items[safe_offset : safe_offset + safe_limit],
            total=len(items),
            offset=safe_offset,
            limit=safe_limit,
        )


class StaticCatalogFormulaProvider(_FormulaProviderBase):
    domain = CatalogDomain(
        key="formulas",
        label="伤害公式",
        description="项目公式、正式静态输入边界、变量、限制与审计证据",
        order=90,
    )

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        formulas = self._load(release).formulas
        needle = query.strip().casefold()
        matched = tuple(
            formula
            for formula in formulas
            if not needle
            or needle
            in " ".join(
                (formula.key, formula.section, formula.title, formula.expression)
            ).casefold()
        )
        return self._page(
            tuple(self._item(formula) for formula in matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        formula = next(
            (
                entry
                for entry in self._load(release).formulas
                if entry.key == record_id
            ),
            None,
        )
        if formula is None:
            return None
        sections = [
            CatalogSection(
                "公式",
                (
                    CatalogField(
                        "表达式",
                        formula.expression,
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    ),
                    CatalogField(
                        "数据边界",
                        formula.boundary,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "适用条件",
                        "；".join(formula.applicable_when) or "不可用",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "限制",
                        "；".join(formula.limitations) or "无",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                ),
            ),
            CatalogSection(
                "变量",
                tuple(
                    CatalogField(
                        variable.symbol,
                        variable.meaning,
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    )
                    for variable in formula.variables
                ),
            ),
            self._evidence_section(formula),
        ]
        return CatalogDetail(
            item=self._item(formula),
            sections=tuple(sections),
            notes=("公式是项目规则或审计投影；正式 SQLite 字段仅作为明确标注的输入。",),
        )

    @classmethod
    def _item(cls, formula: FormulaEntry) -> CatalogItem:
        return CatalogItem(
            domain_key=cls.domain.key,
            record_id=formula.key,
            title=formula.title,
            subtitle=f"{formula.section} · {formula.expression}",
            source=CatalogValueSource.PROJECT_ANNOTATION,
        )

    @staticmethod
    def _evidence_section(formula: FormulaEntry) -> CatalogSection:
        return CatalogSection(
            "审计证据",
            tuple(
                CatalogField(
                    f"{evidence.kind} · {evidence.symbol}",
                    f"{evidence.path} · {evidence.note}",
                    CatalogValueSource.PROJECT_ANNOTATION,
                    True,
                )
                for evidence in formula.evidence
            ),
        )


class StaticCatalogCounterfactualProvider(_FormulaProviderBase):
    domain = CatalogDomain(
        key="counterfactual_models",
        label="反事实模型",
        description="角色被动、觉醒、弧盘、空幕等机制的证据覆盖与缺口",
        order=100,
    )

    def search(
        self,
        release: StaticCatalogRelease,
        *,
        query: str,
        offset: int,
        limit: int,
    ) -> CatalogPage:
        entries = self._load(release).counterfactual_support
        needle = query.strip().casefold()
        matched = tuple(
            entry
            for entry in entries
            if not needle
            or needle
            in " ".join(
                (
                    entry.key,
                    entry.category,
                    entry.mechanism,
                    entry.scope,
                    entry.status,
                )
            ).casefold()
        )
        return self._page(
            tuple(self._item(entry) for entry in matched),
            offset=offset,
            limit=limit,
        )

    def detail(
        self,
        release: StaticCatalogRelease,
        record_id: str,
    ) -> CatalogDetail | None:
        entry = next(
            (
                row
                for row in self._load(release).counterfactual_support
                if row.key == record_id
            ),
            None,
        )
        if entry is None:
            return None
        notes = list(entry.limitations)
        if entry.key == "native_counterfactual_core":
            notes.insert(0, _NATIVE_CORE_NOTE)
        sections = (
            CatalogSection(
                "支持状态",
                (
                    CatalogField(
                        "生产能力状态",
                        entry.status,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "范围",
                        entry.scope,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "建模方案",
                        entry.modeling_scheme,
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "覆盖 dataset",
                        entry.covered_dataset,
                        CatalogValueSource.OFFICIAL_STATIC,
                        True,
                    ),
                    CatalogField(
                        "覆盖对象",
                        "；".join(entry.covered_entities) or "无",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                    CatalogField(
                        "缺口代码",
                        "；".join(entry.gap_codes) or "无",
                        CatalogValueSource.PROJECT_ANNOTATION,
                        bool(entry.gap_codes),
                    ),
                    CatalogField(
                        "生产消费者",
                        "；".join(entry.consumer_entries) or "无生产入口",
                        CatalogValueSource.PROJECT_ANNOTATION,
                    ),
                ),
            ),
            CatalogSection(
                "审计证据",
                tuple(
                    CatalogField(
                        f"{evidence.kind} · {evidence.symbol}",
                        f"{evidence.path} · {evidence.note}",
                        CatalogValueSource.PROJECT_ANNOTATION,
                        True,
                    )
                    for evidence in entry.evidence
                ),
            ),
        )
        return CatalogDetail(
            item=self._item(entry),
            sections=sections,
            notes=tuple(notes),
        )

    @classmethod
    def _item(cls, entry: CounterfactualSupportEntry) -> CatalogItem:
        subtitle = f"{entry.category} · 生产状态 {entry.status}"
        if entry.key == "native_counterfactual_core":
            subtitle += " · C++ 独立差分验证，非生产入口"
        return CatalogItem(
            domain_key=cls.domain.key,
            record_id=entry.key,
            title=entry.mechanism,
            subtitle=subtitle,
            source=CatalogValueSource.PROJECT_ANNOTATION,
        )
