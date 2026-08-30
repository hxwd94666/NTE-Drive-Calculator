# 角色图鉴发行注解与获取方式的 Qt 无关只读投影。
"""Data-backed release metadata for the player-facing character archive."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from src.domain.static_catalog_terminology import LocalizedTerm
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


_ACQUISITION_TERM_KIND = "character_acquisition_type"


class CharacterReleaseMetadataSource(Protocol):
    """Narrow v30 release-static source owned by the composition root."""

    def list_catalog_character_release_annotations(
        self,
    ) -> Iterable[Mapping[str, object]]: ...


@dataclass(frozen=True, slots=True)
class CharacterReleaseMetadata:
    character_id: int
    quality: str | None
    quality_source_kind: str | None
    acquisition_type: str | None
    acquisition_term: LocalizedTerm | None
    acquisition_source_kind: str | None
    release_date: str | None
    release_source_kind: str | None
    evidence_keys: tuple[str, ...]


class CharacterReleaseMetadataService:
    """Project v30 annotations and public terminology without Qt or seed fallback."""

    def __init__(
        self,
        source: CharacterReleaseMetadataSource,
        terminology: StaticCatalogTerminologyService,
        *,
        locale: str = "zh-CN",
    ) -> None:
        self._source = source
        self._terminology = terminology
        self._locale = str(locale or "").strip().replace("_", "-")
        if not self._locale:
            raise ValueError("locale 不能为空")
        self._metadata: dict[int, CharacterReleaseMetadata] | None = None
        self._acquisition_terms: tuple[LocalizedTerm, ...] | None = None

    def metadata(self, character_id: int) -> CharacterReleaseMetadata | None:
        self._load()
        assert self._metadata is not None
        return self._metadata.get(int(character_id))

    def acquisition_terms(self) -> tuple[LocalizedTerm, ...]:
        """Return DB-present stable filters with names from public terminology."""

        self._load()
        assert self._acquisition_terms is not None
        return self._acquisition_terms

    def _load(self) -> None:
        if self._metadata is not None:
            return
        rows = tuple(self._source.list_catalog_character_release_annotations())
        ordered_types = tuple(dict.fromkeys(
            value
            for row in rows
            if (value := _optional_text(row.get("acquisition_type"))) is not None
        ))
        terms = {
            stable_key: self._terminology.resolve(
                _ACQUISITION_TERM_KIND,
                stable_key,
                locale=self._locale,
            )
            for stable_key in ordered_types
        }
        metadata: dict[int, CharacterReleaseMetadata] = {}
        for row in rows:
            character_id = int(row["character_id"])
            acquisition_type = _optional_text(row.get("acquisition_type"))
            evidence = row.get("evidence_keys")
            evidence_keys = tuple(dict.fromkeys(
                str(value).strip()
                for value in (
                    evidence
                    if isinstance(evidence, (tuple, list))
                    else ()
                )
                if str(value).strip()
            ))
            metadata[character_id] = CharacterReleaseMetadata(
                character_id=character_id,
                quality=_optional_text(row.get("quality")),
                quality_source_kind=_optional_text(
                    row.get("quality_source_kind")
                ),
                acquisition_type=acquisition_type,
                acquisition_term=(
                    terms.get(acquisition_type)
                    if acquisition_type is not None else None
                ),
                acquisition_source_kind=_optional_text(
                    row.get("acquisition_source_kind")
                ),
                release_date=_optional_text(row.get("mainland_release_date")),
                release_source_kind=_optional_text(
                    row.get("release_source_kind")
                ),
                evidence_keys=evidence_keys,
            )
        self._metadata = metadata
        self._acquisition_terms = tuple(terms.values())


def _optional_text(value: object) -> str | None:
    normalized = str(value).strip() if value is not None else ""
    return normalized or None


__all__ = [
    "CharacterReleaseMetadata",
    "CharacterReleaseMetadataService",
    "CharacterReleaseMetadataSource",
]
