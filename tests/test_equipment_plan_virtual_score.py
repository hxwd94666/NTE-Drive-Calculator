"""Ensure saved plans with empty placeholders do not trust a stale total."""

from src.features.inventory.equipment_plan_renderer import (
    _saved_plan_contains_virtual_equipment,
    _saved_plan_requires_score_recalculation,
)
from src.optimizer.contracts import (
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
)


def test_virtual_drive_forces_visible_total_recalculation() -> None:
    assert _saved_plan_contains_virtual_equipment({
        ROLE_EQUIPPED_TAPE: {"virtual": False},
        ROLE_EQUIPPED_DRIVES: [{"virtual": True}],
    })


def test_virtual_tape_forces_visible_total_recalculation() -> None:
    assert _saved_plan_contains_virtual_equipment({
        ROLE_EQUIPPED_TAPE: {"virtual": True},
        ROLE_EQUIPPED_DRIVES: [{"virtual": False}],
    })


def test_real_saved_plan_keeps_persisted_total() -> None:
    role_data = {
        ROLE_EQUIPPED_TAPE: {"virtual": False},
        ROLE_EQUIPPED_DRIVES: [{"virtual": False}],
        "_sqlite_assignment_scores_complete": True,
    }
    assert not _saved_plan_contains_virtual_equipment(role_data)
    assert not _saved_plan_requires_score_recalculation(role_data)


def test_legacy_incomplete_scores_force_visible_total_recalculation() -> None:
    assert _saved_plan_requires_score_recalculation({
        ROLE_EQUIPPED_TAPE: {"virtual": False},
        ROLE_EQUIPPED_DRIVES: [{"virtual": False}],
        "_sqlite_assignment_scores_complete": False,
    })
