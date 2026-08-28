# 把静态正式 GameplayTag 投影到本次内存逐击证据，不改写原始战报。
"""Formal damage-tag projection for immutable battle-axis analysis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class BattleFormalDamageTagService:
    """Attach imported damage semantics before hit-channel classification."""

    @staticmethod
    def project(
        evidence: dict[str, Any] | None,
        static_database_path: Path | None,
    ) -> None:
        if evidence is None or static_database_path is None:
            return
        hits = tuple(evidence.get("hits") or ())
        effect_ids = tuple(
            str(hit.get("gameplay_effect_name") or "").strip()
            for hit in hits
            if str(hit.get("gameplay_effect_name") or "").strip()
        )
        with StaticGameDataDao(static_database_path) as static_dao:
            tags_by_effect = static_dao.list_gameplay_effect_tags(effect_ids)
        for hit in hits:
            effect_id = str(hit.get("gameplay_effect_name") or "").strip()
            hit["formal_gameplay_tags"] = tags_by_effect.get(effect_id, ())
