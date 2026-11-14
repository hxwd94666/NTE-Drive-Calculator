# 测试极速装配完成结果汇总。
"""Behavior tests for concise fast-apply completion presentation."""

from src.features.inventory.equipment.assembly.fast_apply_completion_summary import build_fast_apply_completion_summary


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


def test_partial_and_skipped_roles_are_not_presented_as_complete():
    skips = [{"kind": "module", "reason": "virtual"}, {"kind": "core", "reason": "missing"}]
    summary, details = build_fast_apply_completion_summary(
        [{"role_name": "甲", "module_count": 2, "verified": True, "skipped_assignments": skips}],
        skipped_roles=[{"role_name": "乙", "skipped_assignments": skips}],
    )
    assert "1 个为部分装配" in summary
    assert "1 个角色无可用装备，未发送指令" in summary
    assert "甲：部分装配，2 个驱动" in details
    assert "乙：未装配" in details
    assert "虚拟占位 1 件、背包中不存在 1 件" in details


def test_all_skipped_does_not_claim_dispatch():
    summary, _ = build_fast_apply_completion_summary([], skipped_roles=[{
        "role_name": "甲", "skipped_assignments": [{"kind": "module", "reason": "virtual"}],
    }])
    assert summary == "没有可用装备，未发送装配指令"
