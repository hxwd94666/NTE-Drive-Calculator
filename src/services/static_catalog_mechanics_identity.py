# 用正式关联对象名称投影机制身份，不把 raw key 当作玩家名称。
"""Readable mechanics identities and typed owner/relation projections."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from pathlib import Path
import re

from src.domain.static_catalog import CatalogLink
from src.services.static_catalog_misc_models import CatalogDetail, CatalogSearchItem
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_mechanics_queries import (
    StaticCatalogMechanicsQueries,
)


@dataclass(frozen=True, slots=True)
class ReadableMechanicsIdentity:
    display_name: str | None
    provider_kind: str


@dataclass(frozen=True, slots=True)
class MechanicsIdentityCandidate:
    item: CatalogSearchItem
    family_hint: str
    provider_kind: str


class StaticCatalogMechanicsIdentityProvider:
    """Resolve formal or relationship-backed names without guessing from IDs."""

    def __init__(
        self,
        database_path: str | Path,
        terminology_service: StaticCatalogTerminologyService,
    ) -> None:
        self._database_path = Path(database_path).expanduser().resolve()
        self._terminology = terminology_service
        self._candidate_cache: dict[int, tuple[MechanicsIdentityCandidate, ...]] = {}
        self._identity_cache: dict[
            tuple[str, str], ReadableMechanicsIdentity
        ] = {}
        self._owner_cache: dict[tuple[str, str], tuple[str, CatalogLink | None]] = {}
        self._link_cache: dict[
            tuple[str, str], tuple[tuple[str, CatalogLink], ...]
        ] = {}

    def candidates(self, *, per_kind_limit: int = 24) -> tuple[MechanicsIdentityCandidate, ...]:
        if per_kind_limit in self._candidate_cache:
            return self._candidate_cache[per_kind_limit]
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            rows = queries.list_identity_candidates(per_kind_limit=per_kind_limit)
        candidates = []
        for row in rows:
            name = self._clean_name(row.get("display_name"))
            candidates.append(MechanicsIdentityCandidate(
                item=CatalogSearchItem(
                    entity_kind=str(row["entity_kind"]),
                    entity_key=str(row["entity_key"]),
                    title=name or "名称暂未提供",
                    subtitle="",
                    origin_kind="formal_static",
                    origin_label="正式静态",
                ),
                family_hint=str(row.get("family_hint") or ""),
                provider_kind=str(row["provider_kind"]),
            ))
        result = tuple(candidates)
        self._candidate_cache[per_kind_limit] = result
        return result

    def resolve(self, raw: CatalogDetail) -> ReadableMechanicsIdentity:
        cache_key = (raw.entity_kind, raw.entity_key)
        if cache_key in self._identity_cache:
            return self._identity_cache[cache_key]
        term = self._terminology.resolve(raw.entity_kind, raw.entity_key)
        if term.name_available and term.display_name:
            name = self._clean_name(term.display_name)
            if name:
                result = ReadableMechanicsIdentity(name, "formal_localization")
                self._identity_cache[cache_key] = result
                return result
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            related = queries.resolve_related_identity(raw.entity_kind, raw.entity_key)
            if related is not None:
                name = self._clean_name(related.get("display_name"))
                if name:
                    result = ReadableMechanicsIdentity(
                        name,
                        str(related.get("provider_kind") or "related_ability"),
                    )
                    self._identity_cache[cache_key] = result
                    return result
            values = self._values(raw)
            owner = queries.resolve_owner(
                str(values.get("所有者类型") or ""),
                str(values.get("所有者 ID") or ""),
                str(values.get("效果定义 key") or ""),
            )
        if owner is not None:
            name = self._clean_name(owner.get("display_name"))
            if name:
                result = ReadableMechanicsIdentity(name, "formal_owner")
                self._identity_cache[cache_key] = result
                return result
        if raw.entity_kind == "reaction":
            result = ReadableMechanicsIdentity(
                "异能环合规则", "mechanism_collection"
            )
        else:
            result = ReadableMechanicsIdentity(None, "name_missing")
        self._identity_cache[cache_key] = result
        return result

    def owner(self, raw: CatalogDetail) -> tuple[str, CatalogLink | None]:
        cache_key = (raw.entity_kind, raw.entity_key)
        if cache_key in self._owner_cache:
            return self._owner_cache[cache_key]
        values = self._values(raw)
        character_id = str(values.get("所属角色 ID") or "")
        if character_id and character_id not in {"—", "0"}:
            try:
                term = self._terminology.resolve("character", character_id)
            except ValueError:
                term = None
            label = (
                term.display_name
                if term is not None and term.name_available and term.display_name
                else None
            )
            if label is None:
                with StaticCatalogMechanicsQueries(self._database_path) as queries:
                    target = queries.resolve_owner(
                        "character_awaken",
                        f"{character_id}:catalog",
                    )
                label = (
                    self._clean_name(target.get("display_name"))
                    if target is not None
                    else None
                )
            result = (label or "名称暂未提供", CatalogLink(
                "character", character_id, "owner"
            ))
            self._owner_cache[cache_key] = result
            return result
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            target = queries.resolve_owner(
                str(values.get("所有者类型") or ""),
                str(values.get("所有者 ID") or ""),
                str(values.get("效果定义 key") or ""),
            )
        if target is None:
            result = ("公共机制", None)
            self._owner_cache[cache_key] = result
            return result
        label = self._clean_name(target.get("display_name")) or "名称暂未提供"
        result = (label, CatalogLink(
            str(target["domain_key"]),
            str(target["record_id"]),
            str(target["relation_kind"]),
            str(target.get("anchor") or ""),
        ))
        self._owner_cache[cache_key] = result
        return result

    def additional_links(
        self,
        raw: CatalogDetail,
    ) -> tuple[tuple[str, CatalogLink], ...]:
        cache_key = (raw.entity_kind, raw.entity_key)
        if cache_key in self._link_cache:
            return self._link_cache[cache_key]
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            rows = queries.list_additional_relations(raw.entity_kind, raw.entity_key)
        result = tuple(
            (
                str(row["label"]),
                CatalogLink(
                    "combat_mechanics",
                    self._record_id(str(row["target_kind"]), str(row["target_key"])),
                    "related",
                ),
            )
            for row in rows
        )
        self._link_cache[cache_key] = result
        return result

    def owner_resolution_counts(self) -> tuple[tuple[str, int], ...]:
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            counts = queries.owner_resolution_counts()
        return tuple((kind, counts[kind]) for kind in (
            "character_awaken", "fork_star", "equipment_suit",
        ))

    def identity_provider_counts(self) -> tuple[tuple[str, int], ...]:
        counts = Counter(candidate.provider_kind for candidate in self.candidates())
        return tuple(sorted(counts.items()))

    def skill_relation_counts(self) -> tuple[tuple[str, int], ...]:
        with StaticCatalogMechanicsQueries(self._database_path) as queries:
            counts = queries.skill_relation_counts()
        return tuple(sorted(counts.items()))

    @staticmethod
    def _record_id(entity_kind: str, entity_key: str) -> str:
        from src.services.static_catalog_mechanics_models import encode_record

        return encode_record("effect", f"{entity_kind}{chr(31)}{entity_key}")

    @staticmethod
    def _values(raw: CatalogDetail) -> dict[str, str]:
        return {
            field.label: str(field.value)
            for section in raw.sections
            for field in section.fields
        }

    @staticmethod
    def _clean_name(value: object) -> str | None:
        text = re.sub(r"<[^>]+>", "", str(value or "")).strip()
        if len(text) <= 1 or not re.search(r"[\u3400-\u9fff]", text):
            return None
        return text


__all__ = [
    "MechanicsIdentityCandidate",
    "ReadableMechanicsIdentity",
    "StaticCatalogMechanicsIdentityProvider",
]
