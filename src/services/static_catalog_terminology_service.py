# 游戏资料库只读正式术语与本地化名称投影。
"""Qt-free terminology projection for player-facing static catalog pages."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from src.domain.static_catalog_terminology import (
    ForkCampaignRecord,
    LocalizedForkCampaign,
    LocalizedTerm,
    LocalizedTermRecord,
)


class StaticCatalogTerminologySource(Protocol):
    """Narrow provider implemented by the release-static read-only DAO."""

    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None: ...

    def list_fork_campaigns(self) -> tuple[ForkCampaignRecord, ...]: ...


def _locale_key(value: str) -> str:
    return str(value or "").strip().replace("_", "-").casefold()


def _clean_names(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    cleaned: list[tuple[str, str]] = []
    for locale, name in values.items():
        normalized_locale = str(locale or "").strip().replace("_", "-")
        normalized_name = str(name or "").strip()
        if normalized_locale and normalized_name:
            cleaned.append((normalized_locale, normalized_name))
    return tuple(cleaned)


def _select_name(
    names: tuple[tuple[str, str], ...],
    requested_locale: str,
    fallback_locales: tuple[str, ...],
) -> tuple[str, str] | None:
    by_locale = {_locale_key(locale): (locale, name) for locale, name in names}
    candidates = (requested_locale, *fallback_locales)
    for locale in candidates:
        match = by_locale.get(_locale_key(locale))
        if match is not None:
            return match
    return None


class StaticCatalogTerminologyService:
    """Resolve stable IDs without leaking raw fields into player-facing names.

    Alias and capitalization semantics belong to ``source``.  In particular,
    the progression token ``gold`` must not be case-folded to the canonical
    capital item ``Gold``.  The source resolves that context-qualified alias to
    its canonical item first; this service only selects localized text.
    """

    def __init__(
        self,
        source: StaticCatalogTerminologySource,
        *,
        fallback_locales: tuple[str, ...] = ("zh-CN",),
    ) -> None:
        self._source = source
        self._fallback_locales = tuple(
            str(locale).strip().replace("_", "-")
            for locale in fallback_locales
            if str(locale).strip()
        )

    def resolve(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        locale: str = "zh-CN",
        context: str | None = None,
    ) -> LocalizedTerm:
        normalized_kind = str(entity_kind or "").strip()
        normalized_id = str(stable_id or "").strip()
        normalized_locale = str(locale or "").strip().replace("_", "-")
        if not normalized_kind:
            raise ValueError("entity_kind 不能为空")
        if not normalized_id:
            raise ValueError("stable_id 不能为空")
        if not normalized_locale:
            raise ValueError("locale 不能为空")

        record = self._source.lookup_localized_term(
            normalized_kind,
            normalized_id,
            context=str(context).strip() if context is not None else None,
        )
        if record is None:
            return LocalizedTerm(
                entity_kind=normalized_kind,
                requested_id=normalized_id,
                canonical_id=None,
                requested_locale=normalized_locale,
                resolved_locale=None,
                display_name=None,
                status="name_missing",
                source_kind="name_missing",
            )
        if record.entity_kind != normalized_kind:
            raise ValueError(
                "术语来源返回了不一致的 entity_kind："
                f"{record.entity_kind!r} != {normalized_kind!r}"
            )
        if not str(record.canonical_id or "").strip():
            raise ValueError("术语来源返回的 canonical_id 不能为空")

        selected = _select_name(
            _clean_names(record.names),
            normalized_locale,
            self._fallback_locales,
        )
        if selected is None:
            return LocalizedTerm(
                entity_kind=normalized_kind,
                requested_id=normalized_id,
                canonical_id=record.canonical_id,
                requested_locale=normalized_locale,
                resolved_locale=None,
                display_name=None,
                status="name_missing",
                text_table=record.text_table,
                text_key=record.text_key,
                source_kind=record.source_kind,
            )
        resolved_locale, display_name = selected
        return LocalizedTerm(
            entity_kind=normalized_kind,
            requested_id=normalized_id,
            canonical_id=record.canonical_id,
            requested_locale=normalized_locale,
            resolved_locale=resolved_locale,
            display_name=display_name,
            status="complete",
            text_table=record.text_table,
            text_key=record.text_key,
            source_kind=record.source_kind,
        )

    def resolve_many(
        self,
        entity_kind: str,
        stable_ids: tuple[str, ...],
        *,
        locale: str = "zh-CN",
        context: str | None = None,
    ) -> tuple[LocalizedTerm, ...]:
        return tuple(
            self.resolve(
                entity_kind,
                stable_id,
                locale=locale,
                context=context,
            )
            for stable_id in stable_ids
        )

    def list_fork_campaigns(
        self,
        *,
        locale: str = "zh-CN",
    ) -> tuple[LocalizedForkCampaign, ...]:
        return tuple(
            LocalizedForkCampaign(
                pool_id=record.pool_id,
                featured_fork_id=record.featured_fork_id,
                release_ordinal=record.release_ordinal,
                title=self.resolve(
                    "fork_campaign",
                    record.pool_id,
                    locale=locale,
                ),
            )
            for record in self._source.list_fork_campaigns()
        )
