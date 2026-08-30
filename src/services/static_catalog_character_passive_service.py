"""Project the two official cultivation passives for a logical character."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)
from src.services.static_catalog_character_models import (
    CharacterPassive,
    SkillDescription,
)


class CharacterPassiveQueries(Protocol):
    def list_catalog_characters(
        self, *, query: str = "", limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]: ...

    def list_catalog_ability_details(
        self, ability_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]: ...


def _text(value: object) -> str | None:
    result = str(value or "").strip()
    return result or None


class StaticCatalogCharacterPassiveService:
    """Join audited PassiveAbilityList ownership with formal localized text."""

    def __init__(self, queries: CharacterPassiveQueries) -> None:
        self._queries = queries

    def list_passives(
        self,
        character_row: Mapping[str, Any],
    ) -> tuple[CharacterPassive, ...]:
        character_ids = {int(character_row["character_id"])}
        logical_key = _text(character_row.get("logical_character_key"))
        if logical_key is not None:
            character_ids.update(
                int(row["character_id"])
                for row in self._queries.list_catalog_characters(limit=200)
                if _text(row.get("logical_character_key")) == logical_key
            )
        definitions = tuple(
            row for row in BattleCharacterPassiveService.catalog()
            if row.character_id in character_ids
        )
        details = {
            str(row["ability_id"]): row
            for row in self._queries.list_catalog_ability_details(tuple(
                definition.ability_id for definition in definitions
            ))
        }
        return tuple(
            self._project(definition.ability_id, definition.unlock_stage, details)
            for definition in sorted(
                definitions, key=lambda row: (row.unlock_stage, row.ability_id),
            )
        )

    @staticmethod
    def _project(
        ability_id: str,
        unlock_stage: int,
        details: Mapping[str, Mapping[str, Any]],
    ) -> CharacterPassive:
        row = details.get(ability_id, {})
        return CharacterPassive(
            ability_id=ability_id,
            name_zh=_text(row.get("name_zh")),
            unlock_stage=unlock_stage,
            descriptions=tuple(
                SkillDescription(
                    ordinal=int(item["ordinal"]),
                    description_type=_text(item.get("description_type")),
                    title_zh=_text(item.get("title_zh")),
                    description_zh=_text(item.get("description_zh")),
                    short_description_zh=_text(item.get("short_description_zh")),
                    unlock_id=_text(item.get("unlock_id")),
                    unlock_description_zh=_text(item.get("unlock_description_zh")),
                )
                for item in row.get("descriptions", ())
            ),
        )


__all__ = ["StaticCatalogCharacterPassiveService"]
