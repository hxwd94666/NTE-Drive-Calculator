"""Verify persisted loadout totals are rebuilt from concrete slots."""

from src.domain.loadout_plan_scores import exact_assignment_score_total


def test_exact_total_counts_virtual_placeholder_as_its_zero_slot_score() -> None:
    assignments = (
        {"uid_slot": 0, "uid_serial": 101, "kind": "module"},
        {"uid_slot": 8, "uid_serial": 202, "kind": "core"},
    )

    assert exact_assignment_score_total(
        assignments,
        {
            "nte-module-0-101": 0.0,
            "nte-core-8-202": 42.5,
        },
    ) == 42.5


def test_exact_total_requires_every_assignment_score() -> None:
    assignments = (
        {"uid_slot": 1, "uid_serial": 11, "kind": "module"},
        {"uid_slot": 2, "uid_serial": 22, "kind": "core"},
    )

    assert exact_assignment_score_total(
        assignments,
        {"nte-module-1-11": 30.0},
    ) is None
