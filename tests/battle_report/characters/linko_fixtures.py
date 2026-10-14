# 共享完整灵可动作、独立配对与时停来源夹具，避免测试模块间导入。
from __future__ import annotations

from src.domain.battle_report.timeline.events import BattleAnalysisHit, BattleInferredAction
from src.services.battle_report.actions.inference import BattleActionAnimationCandidate
from src.services.battle_report.timeline.stop_projection import BattleTimeStopProjection


def _hit(
    sequence: int,
    time_us: int,
    *,
    character_id: int,
    ability_id: str,
    effect_id: str,
    target_id: str = "target-1",
    damage_attribute: str = "unknown",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=character_id,
        character_name={1003: "早雾", 1036: "残虹", 1072: "灵可"}.get(
            character_id,
            "角色",
        ),
        skill_name=ability_id,
        damage_name=effect_id,
        damage_component="skill",
        attack_type="QTE" if "QTE" in ability_id else "E技能",
        damage_attribute=damage_attribute,
        target_id=target_id,
        target_name="目标",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=ability_id,
        gameplay_effect_id=effect_id,
    )


def _action(
    action_id: str,
    input_kind: str,
    character_id: int,
    hits: tuple[BattleAnalysisHit, ...],
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=action_id,
        character_id=character_id,
        character_name=hits[0].character_name,
        action_name=hits[0].skill_name,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=hits[0].relative_time_us,
        end_us=hits[-1].relative_time_us + 1,
        hits=len(hits),
        damage=sum(hit.damage for hit in hits),
        identity_confidence="中",
        timing_confidence="低",
        inference_basis="测试动作",
        evidence_event_ids=tuple(hit.event_id for hit in hits),
        gameplay_effect_ids=tuple(hit.gameplay_effect_id for hit in hits),
    )


def _linko_e(
    *,
    sequence: int = 1,
    start_us: int = 1_000_000,
    target_id: str = "target-1",
) -> tuple[tuple[BattleAnalysisHit, ...], BattleInferredAction]:
    offsets = (0, 151_000, 358_000, 653_000)
    hits = tuple(
        _hit(
            sequence + ordinal - 1,
            start_us + offsets[ordinal - 1],
            character_id=1072,
            ability_id="GA_Radio072_Skill",
            effect_id=f"GE_Player_Radio072_Skill{ordinal}_Damage",
            target_id=target_id,
        )
        for ordinal in range(1, 5)
    )
    return hits, _action(f"linko-e:{sequence}", "E", 1072, hits)


def _qte(
    *,
    sequence: int = 10,
    time_us: int = 4_200_000,
    character_id: int = 1036,
    target_id: str = "target-1",
    damage_attribute: str = "incantation",
) -> tuple[BattleAnalysisHit, BattleInferredAction]:
    name = "Zankou" if character_id == 1036 else "Sagiri"
    hit = _hit(
        sequence,
        time_us,
        character_id=character_id,
        ability_id=f"GA_{name}_QTE",
        effect_id=f"GE_Player_{name}_QTE_Damage",
        target_id=target_id,
        damage_attribute=damage_attribute,
    )
    return hit, _action(f"qte:{sequence}", "QTE", character_id, (hit,))


def _lte_aoe(
    *,
    sequence: int = 11,
    time_us: int = 4_280_000,
    target_id: str = "target-1",
) -> BattleAnalysisHit:
    return _hit(
        sequence,
        time_us,
        character_id=1072,
        ability_id="GA_Radio072_UltraSkillLTE_AOE",
        effect_id="GE_Player_Radio072_UltralSkillLTE_AOE_Damage",
        target_id=target_id,
        damage_attribute="nature",
    )


def _e_animation() -> BattleActionAnimationCandidate:
    return BattleActionAnimationCandidate(
        ability_id="GA_Radio072_Skill",
        selector_key="Skill1",
        montage_asset_path="/Game/Animation/Radio072_Skill",
        effect_hit_offsets_us=(
            ("GE_Player_Radio072_Skill1_Damage", (130_542,)),
            ("GE_Player_Radio072_Skill2_Damage", (281_632,)),
            ("GE_Player_Radio072_Skill3_Damage", (488_676,)),
            ("GE_Player_Radio072_Skill4_Damage", (783_425,)),
        ),
        trigger_end_offsets_us=(1_991_459,),
        end_event_offsets_us=(),
        section_end_offsets_us=(5_450_000,),
        duration_us=5_450_000,
    )


def _time_stops(
    intervals: tuple[tuple[int, int], ...] = (),
    *,
    source_kind: str = "none",
    confidence: str = "",
    q_action_intervals: tuple[tuple[int, int], ...] = (),
    type6_intervals: tuple[tuple[int, int], ...] = (),
    non_type6_intervals: tuple[tuple[int, int], ...] = (),
) -> BattleTimeStopProjection:
    resolved_non_type6 = (
        non_type6_intervals
        if non_type6_intervals or type6_intervals
        else intervals
    )
    return BattleTimeStopProjection(
        intervals=intervals,
        source_kind=source_kind,
        confidence=confidence,
        inference_basis="测试时停来源",
        q_action_intervals=q_action_intervals,
        type6_intervals=type6_intervals,
        non_type6_intervals=resolved_non_type6,
    )
