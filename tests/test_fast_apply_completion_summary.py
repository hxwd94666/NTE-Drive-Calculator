"""Behavior tests for concise fast-apply completion presentation."""

from src.features.inventory.fast_apply_completion_summary import (
    build_fast_apply_completion_summary,
)


def test_summary_keeps_each_role_but_hides_completed_role_statuses() -> None:
    summary, details = build_fast_apply_completion_summary([
        {"role_name": "角色甲", "verified": True},
        {"role_name": "角色乙", "verified": True, "repaired": True},
        {"role_name": "角色丙", "already_applied": True},
    ])

    assert summary == "已下发 3 个角色的配装"
    assert details == (
        "• 角色甲：已下发\n"
        "• 角色乙：已下发\n"
        "• 角色丙：已下发"
    )


def test_summary_hides_partial_packet_status_for_every_role() -> None:
    summary, details = build_fast_apply_completion_summary([
        {"role_name": "角色甲", "verified": True},
        {"role_name": "角色乙"},
        {"role_name": "角色丙", "scoped_event_observed": True},
        {"role_name": "角色丁", "scoped_verification_error": "bad event"},
    ])

    assert summary == "已下发 4 个角色的配装"
    assert details == (
        "• 角色甲：已下发\n"
        "• 角色乙：已下发\n"
        "• 角色丙：已下发\n"
        "• 角色丁：已下发"
    )


def test_summary_hides_mismatch_annotation_but_keeps_equipment_counts() -> None:
    _summary, details = build_fast_apply_completion_summary(
        [
            {"role_name": "角色甲", "module_count": 7, "verified": True},
            {"role_name": "角色乙", "module_count": 8},
        ],
        mismatch_role_names=frozenset({"角色乙"}),
    )

    assert details == "• 角色甲：7 个驱动\n• 角色乙：8 个驱动"
