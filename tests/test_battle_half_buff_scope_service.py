# 验证上下半场切换只投影对应时间范围内的 Buff 证据。
from __future__ import annotations

from src.domain.battle_report import BattleInferredBuffInterval
from src.services.battle_half_buff_scope_service import (
    BattleHalfBuffScopeService,
)


def _interval(
    interval_id: str,
    character_id: int,
    start_us: int = 0,
    end_us: int = 100_000_000,
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path=f"/Game/{interval_id}",
        buff_name=interval_id,
        source_effect_definition_id=interval_id,
        source_kind="test",
        source_character_id=character_id,
        source_character_name=str(character_id),
        target_scope="team",
        start_us=start_us,
        end_us=end_us,
        stacks=1,
        duration_policy="test",
        state_confidence="test",
        value_confidence="test",
        inference_basis="test",
        trigger_event_type="test",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(),
    )


def _hit(
    time_us: int,
    half: str,
    character_id: int,
    *,
    direction: str = "outgoing",
    damage: float = 1.0,
    character_known: bool = True,
) -> dict[str, object]:
    return {
        "relative_time_us": time_us,
        "abyss_half": half,
        "direction": direction,
        "damage": damage,
        "character_id": character_id,
        "character_known": character_known,
    }


def test_upper_buff_ends_and_lower_static_buff_starts_at_half_boundary() -> None:
    scoped = BattleHalfBuffScopeService.scope(
        (
            _interval("upper-dynamic", 1004, 20_000_000),
            _interval("lower-static", 1052),
        ),
        raw_hits=(
            _hit(1_000_000, "upper", 1004),
            _hit(76_678_178, "lower", 1052),
            _hit(90_000_000, "lower", 1052),
        ),
        battle_end_us=100_000_000,
    )

    assert [(row.interval_id, row.start_us, row.end_us) for row in scoped] == [
        ("upper-dynamic", 20_000_000, 76_678_178),
        ("lower-static", 76_678_178, 100_000_000),
    ]


def test_external_condition_is_not_cleared_when_half_changes() -> None:
    external = _interval("witch", 0)

    scoped = BattleHalfBuffScopeService.scope(
        (external,),
        raw_hits=(
            _hit(1_000_000, "upper", 1004),
            _hit(76_678_178, "lower", 1052),
        ),
        battle_end_us=100_000_000,
    )

    assert scoped == (external,)


def test_same_character_in_both_halves_creates_two_disjoint_intervals() -> None:
    scoped = BattleHalfBuffScopeService.scope(
        (_interval("shared", 1052),),
        raw_hits=(
            _hit(1_000_000, "upper", 1052),
            _hit(76_678_178, "lower", 1052),
        ),
        battle_end_us=100_000_000,
    )

    assert [(row.interval_id, row.start_us, row.end_us) for row in scoped] == [
        ("shared:half:upper", 0, 76_678_178),
        ("shared:half:lower", 76_678_178, 100_000_000),
    ]


def test_pure_support_character_uses_formal_zero_damage_half_evidence() -> None:
    scoped = BattleHalfBuffScopeService.scope(
        (_interval("support-team-buff", 1075),),
        raw_hits=(
            _hit(
                1_000_000,
                "upper",
                1075,
                direction="incoming",
                damage=0.0,
            ),
            _hit(76_678_178, "lower", 1052),
        ),
        battle_end_us=100_000_000,
    )

    assert [(row.interval_id, row.start_us, row.end_us) for row in scoped] == [
        ("support-team-buff", 0, 76_678_178),
    ]
    assert scoped[0].target_scope == "team"


def test_missing_source_half_evidence_is_preserved_as_unknown_scope() -> None:
    scoped = BattleHalfBuffScopeService.scope(
        (_interval("unresolved-support", 1075),),
        raw_hits=(
            _hit(1_000_000, "upper", 1010),
            _hit(76_678_178, "lower", 1052),
        ),
        battle_end_us=100_000_000,
    )

    assert len(scoped) == 1
    assert scoped[0].interval_id == "unresolved-support"
    assert scoped[0].target_scope == "unknown"
    assert "半场" in scoped[0].inference_basis
