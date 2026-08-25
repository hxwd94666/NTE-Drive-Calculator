# 从完整原始逐击轴生成不改写冻结快照的角色生效事实。
"""Derived character facts backed by exact runtime evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from typing import Any

from src.domain.battle_report import BattleInferredCharacterFact


INFERRED_CHARACTER_FACT_MODEL_VERSION = "battle-inferred-character-fact-v1"
_LACRIMOSA_ID = 1004
_LACRIMOSA_LV6_EFFECT = "GE_Player_Lacrimosa_Blood_Damage_LV6"
_LACRIMOSA_EFFECT5_FACT_ID = "awaken-effect-active:1004:Effect5:Blood_Damage_LV6"


class BattleInferredCharacterFactService:
    @staticmethod
    def infer(evidence: Mapping[str, Any] | None) -> tuple[BattleInferredCharacterFact, ...]:
        event_ids = []
        for row in (evidence or {}).get("hits") or ():
            if str(row.get("direction") or "") != "outgoing":
                continue
            try:
                character_id = int(row.get("character_id") or 0)
            except (TypeError, ValueError):
                continue
            if (
                character_id != _LACRIMOSA_ID
                or str(row.get("gameplay_effect_name") or "")
                != _LACRIMOSA_LV6_EFFECT
            ):
                continue
            sequence = str(
                row.get("sequence_text")
                or row.get("sequence_order")
                or "0"
            )
            event_ids.append(f"{sequence}:primary")
        if not event_ids:
            return ()
        return (BattleInferredCharacterFact(
            fact_id=_LACRIMOSA_EFFECT5_FACT_ID,
            character_id=_LACRIMOSA_ID,
            fact_kind="awaken_effect_active",
            fact_value="Effect5",
            source_gameplay_effect_id=_LACRIMOSA_LV6_EFFECT,
            confidence="高",
            evidence_event_ids=tuple(dict.fromkeys(event_ids)),
            model_version=INFERRED_CHARACTER_FACT_MODEL_VERSION,
            inference_basis=(
                "完整原始轴精确捕获安魂曲 Blood_Damage_LV6；该 GE 只证明本场"
                " Effect5 已实际生效，不改写冻结觉醒或角色页选择。"
            ),
        ),)

    @staticmethod
    def apply_to_build(
        build: Mapping[str, Any] | None,
        facts: Sequence[BattleInferredCharacterFact],
        *,
        disabled_fact_ids: Set[str] | frozenset[str] = frozenset(),
    ) -> None:
        if build is None:
            return
        enabled = {
            (fact.character_id, fact.fact_value)
            for fact in facts
            if fact.fact_kind == "awaken_effect_active"
            and fact.fact_id not in disabled_fact_ids
        }
        for character in build.get("characters") or ():
            character_id = int(character.get("character_id") or 0)
            effect_ids = [
                value for cid, value in enabled if cid == character_id
            ]
            if not effect_ids:
                continue
            profile = dict(character.get("profile") or {})
            selected = list(profile.get("selected_awaken_effect_ids") or ())
            for effect_id in effect_ids:
                if effect_id not in selected:
                    selected.append(effect_id)
            profile["selected_awaken_effect_ids"] = selected
            profile["awakening_selection_initialized"] = True
            profile["inferred_awaken_effect_ids"] = effect_ids
            character["profile"] = profile
