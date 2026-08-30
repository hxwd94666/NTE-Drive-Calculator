# 用正式 GameplayEffect 目录给单目标战报补充怪物身份。
"""Static-validated incoming-hit identity evidence for encounter inference."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from src.domain.battle_encounter import BattleObservedTarget
from src.services.battle_target_candidate_graph_service import normalized_monster_key


class BattleIncomingMonsterIdentityService:
    """Supplement one target only when every formal incoming GE owner agrees."""

    @staticmethod
    def supplement(
        observed: Sequence[BattleObservedTarget],
        evidence: Mapping[str, Any] | None,
        static_dao: Any,
    ) -> tuple[BattleObservedTarget, ...]:
        frozen = tuple(observed)
        if len(frozen) != 1 or frozen[0].monster_id:
            return frozen
        hits = evidence.get("hits") if isinstance(evidence, Mapping) else ()
        monster_keys: set[str] = set()
        for hit in hits or ():
            if (
                not isinstance(hit, Mapping)
                or str(hit.get("direction") or "").casefold() != "incoming"
            ):
                continue
            effect = BattleIncomingMonsterIdentityService._resolve_effect(
                hit, static_dao
            )
            if effect is None:
                continue
            class_path = str(effect.get("class_path") or "")
            if "/monster/" not in class_path.casefold():
                continue
            monster_key = normalized_monster_key(class_path)
            if monster_key:
                monster_keys.add(monster_key)
        if len(monster_keys) != 1:
            return frozen
        return (replace(frozen[0], monster_id=next(iter(monster_keys))),)

    @staticmethod
    def _resolve_effect(hit: Mapping[str, Any], static_dao: Any) -> dict | None:
        effect_name = str(hit.get("gameplay_effect_name") or "").strip()
        raw_index = hit.get("gameplay_effect_index")
        try:
            effect_index = int(raw_index) if raw_index is not None else None
        except (TypeError, ValueError):
            effect_index = None
        effect = (
            static_dao.get_gameplay_effect(gameplay_effect_index=effect_index)
            if effect_index is not None and effect_index > 0
            else static_dao.get_gameplay_effect(gameplay_effect_id=effect_name)
            if effect_name
            else None
        )
        if effect is None:
            return None
        formal_id = str(effect.get("gameplay_effect_id") or "").strip()
        if effect_name and formal_id.casefold() != effect_name.casefold():
            return None
        return effect
