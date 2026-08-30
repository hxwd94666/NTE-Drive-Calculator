# 构造并冻结战报边际页的会话内存候选。
"""Pure session-state helpers for fixed-axis marginal candidates."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.services.battle_inferred_character_fact_service import (
    BattleInferredCharacterFactService,
)


@dataclass(frozen=True, slots=True)
class BattleMarginalCandidate:
    """One immutable worker request; it is never a persistence payload."""

    battle_record_id: int
    profiles: tuple[dict[str, Any], ...]
    equipment_editable: bool
    disabled_inferred_fact_ids: frozenset[str] = frozenset()


class BattleMarginalCandidateService:
    """Own candidate baselines and defensive copies outside Qt widgets."""

    @staticmethod
    def prepare_editor_data(
        editor_data: Mapping[str, Any],
        *,
        equipment_editable: bool,
    ) -> dict[str, Any]:
        prepared = deepcopy(dict(editor_data))
        use_active_edit = bool(prepared.get("is_active"))
        details: list[dict[str, Any]] = []
        for raw_detail in prepared.get("details") or ():
            detail = dict(raw_detail)
            active_profile = dict(detail.get("profile") or {})
            original_profile = dict(detail.get("original_profile") or {})
            profile = active_profile if use_active_edit else original_profile
            character = dict(detail.get("character") or {})
            profile.update({
                "character_id": int(character["character_id"]),
                "observed_name": active_profile.get("observed_name"),
                "ordinal": int(active_profile.get("ordinal") or 0),
            })
            for key in (
                "extra_shape_label",
                "extra_shape_buffs",
                "extra_shape_source",
            ):
                if key in active_profile:
                    profile[key] = deepcopy(active_profile[key])

            contexts = dict(detail.get("equipment_contexts") or {})
            baseline_key = (
                str(detail.get("selected_equipment_context_key") or "battle")
                if use_active_edit and equipment_editable
                else "battle"
            )
            if not equipment_editable:
                for key in (
                    "equipment_context_key",
                    "equipment_context_title",
                    "equipment_source_kind",
                    "equipment_override",
                ):
                    profile.pop(key, None)
            baseline_context = deepcopy(
                contexts.get(baseline_key) or contexts.get("battle") or {}
            )
            replacement_items: list[dict[str, Any]] = []
            seen_uids: set[tuple[int, int]] = set()
            candidate_pools = () if not equipment_editable else (
                detail.get("replacement_items") or (),
                *(
                    context.get("replacement_items") or ()
                    for context in contexts.values()
                    if isinstance(context, Mapping)
                ),
            )
            for source in candidate_pools:
                for item in source:
                    if not isinstance(item, Mapping):
                        continue
                    uid = (
                        int(item.get("uid_slot") or 0),
                        int(item.get("uid_serial") or 0),
                    )
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    replacement_items.append(deepcopy(dict(item)))
            baseline_context.update({
                "title": "当前临时候选配装",
                "source_title": str(
                    baseline_context.get("source_title")
                    or baseline_context.get("title")
                    or "本场候选基线"
                ),
                "source_kind": str(
                    baseline_context.get("source_kind") or "battle_frozen"
                ),
                "replacement_items": replacement_items,
            })
            detail.update({
                "profile": profile,
                "equipment_contexts": {"candidate": baseline_context},
                "selected_equipment_context_key": "candidate",
                "marginal_equipment_editable": bool(equipment_editable),
                "marginal_baseline_kind": (
                    "active_build_edit" if use_active_edit else "battle_frozen"
                ),
            })
            details.append(detail)
        prepared["details"] = details
        prepared["marginal_equipment_editable"] = bool(equipment_editable)
        prepared["marginal_baseline_kind"] = (
            "active_build_edit" if use_active_edit else "battle_frozen"
        )
        prepared["inferred_character_facts"] = (
            BattleInferredCharacterFactService.applicable_to_profiles(
                [detail["profile"] for detail in details],
                tuple(prepared.get("inferred_character_facts") or ()),
            )
        )
        return prepared

    @staticmethod
    def freeze(
        battle_record_id: int,
        profiles: Sequence[Mapping[str, Any]],
        *,
        equipment_editable: bool,
        disabled_inferred_fact_ids: Sequence[str] = (),
    ) -> BattleMarginalCandidate:
        if not profiles:
            raise ValueError("边际候选不能为空")
        copied = tuple(deepcopy(dict(profile)) for profile in profiles)
        character_ids = [int(profile.get("character_id") or 0) for profile in copied]
        if any(character_id <= 0 for character_id in character_ids):
            raise ValueError("边际候选包含无效角色")
        if len(set(character_ids)) != len(character_ids):
            raise ValueError("边际候选包含重复角色")
        return BattleMarginalCandidate(
            battle_record_id=int(battle_record_id),
            profiles=copied,
            equipment_editable=bool(equipment_editable),
            disabled_inferred_fact_ids=frozenset(
                str(value) for value in disabled_inferred_fact_ids if str(value)
            ),
        )

    @staticmethod
    def replace_equipment(
        context: dict[str, Any],
        target: Mapping[str, Any],
        replacement: Mapping[str, Any],
    ) -> None:
        target_uid = BattleMarginalCandidateService._uid(target)
        projected = deepcopy(dict(replacement))
        for key in ("target_row", "target_column"):
            if key in target:
                projected[key] = target[key]
        replaced = False
        for key in ("items", "calculation_items"):
            rows = []
            for item in context.get(key) or ():
                if BattleMarginalCandidateService._uid(item) == target_uid:
                    rows.append(deepcopy(projected))
                    replaced = True
                else:
                    rows.append(deepcopy(dict(item)))
            context[key] = rows
        if not replaced:
            raise ValueError("目标装备不属于当前边际候选")

    @staticmethod
    def as_build_edit(candidate: BattleMarginalCandidate) -> dict[str, Any]:
        profiles = []
        for source in candidate.profiles:
            profile = dict(source)
            if not candidate.equipment_editable:
                for key in (
                    "equipment_context_key",
                    "equipment_context_title",
                    "equipment_source_kind",
                    "equipment_override",
                ):
                    profile.pop(key, None)
            profiles.append(profile)
        return {
            "is_active": True,
            "characters": [
                {
                    **dict(profile),
                    "profile": dict(profile),
                    "skills": [
                        {"skill_id": skill_id, "skill_level": int(level)}
                        for skill_id, level in (
                            profile.get("skill_levels") or {}
                        ).items()
                    ],
                    "awakening_level": len(
                        profile.get("selected_awaken_effect_ids") or ()
                    ),
                }
                for profile in profiles
            ],
        }

    @staticmethod
    def _uid(item: Mapping[str, Any]) -> tuple[int, int]:
        uid = item.get("uid")
        if isinstance(uid, Mapping):
            return int(uid.get("slot") or 0), int(uid.get("serial") or 0)
        return int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)


__all__ = ["BattleMarginalCandidate", "BattleMarginalCandidateService"]
