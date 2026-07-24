# 覆盖词条配装替换评分与方案总分的一致性。
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.features.weighted_allocation.runner import (
    WeightedAllocationPreview,
    replace_weighted_allocation_assignment,
)
from src.services.allocation_solver import (
    AllocationAssignment,
    AllocationSolveResult,
    RoleAllocationOption,
    UnifiedAllocation,
)


def test_replacement_uses_solver_score_not_dialog_display_score() -> None:
    """Core replacement must not inherit the old page scorer's inflated value."""

    old_uid = (1, 1)
    new_uid = (2, 2)
    assignment = AllocationAssignment(
        old_uid, "core", "old-core", None, None, (), None, 4.0, (), (), False, 15,
    )
    option = RoleAllocationOption(101, 1, 10.0, (), (assignment,), (), ())
    result = AllocationSolveResult(
        1, 1, 1, "test", 1, (), UnifiedAllocation("test", 10.0, (option,), (), ()),
    )
    context = SimpleNamespace(
        candidates=(SimpleNamespace(
            uid=new_uid, kind="core", item_id="new-core", suit_id=None,
            geometry=None, grid_count=15,
        ),),
        roles=(SimpleNamespace(character_id=101),),
    )
    preview = WeightedAllocationPreview(
        result=result,
        static_dataset=None,
        account_id="test",
        user_database_path=Path("test.sqlite3"),
        context=context,
    )

    with patch(
        "src.features.weighted_allocation.runner.score_allocation_candidate",
        return_value=37.5,
    ) as scorer:
        updated = replace_weighted_allocation_assignment(
            preview,
            old_uid=old_uid,
            new_uid=new_uid,
        )

    scorer.assert_called_once_with(context, context.roles[0], context.candidates[0])
    updated_option = updated.result.unified.selected[0]
    assert updated_option.assignments[0].score == 37.5
    assert updated_option.score == 37.5
    assert updated.result.unified.total_score == 37.5


def test_borrowed_item_keeps_the_previous_owner_other_assignment_scores() -> None:
    old_uid = (1, 1)
    new_uid = (2, 2)
    target = RoleAllocationOption(
        101,
        1,
        13.0,
        (),
        (
            AllocationAssignment(old_uid, "core", "old", None, None, (), None, 4.0, (), (), False, 15),
            AllocationAssignment((1, 2), "module", "keep", None, "Hen2", (), None, 9.0, (), (), False, 2),
        ),
        (),
        (),
    )
    source = RoleAllocationOption(
        102,
        1,
        18.0,
        (),
        (
            AllocationAssignment(new_uid, "core", "borrowed", None, None, (), None, 6.0, (), (), False, 15),
            AllocationAssignment((2, 3), "module", "keep", None, "Hen2", (), None, 12.0, (), (), False, 2),
        ),
        (),
        (),
    )
    context = SimpleNamespace(
        candidates=(SimpleNamespace(
            uid=new_uid, kind="core", item_id="borrowed", suit_id=None,
            geometry=None, grid_count=15,
        ),),
        roles=(SimpleNamespace(character_id=101), SimpleNamespace(character_id=102)),
    )
    preview = WeightedAllocationPreview(
        AllocationSolveResult(
            1, 1, 1, "test", 1, (),
            UnifiedAllocation("test", 31.0, (target, source), (), ()),
        ),
        None,
        "test",
        Path("test.sqlite3"),
        context,
    )

    with patch(
        "src.features.weighted_allocation.runner.score_allocation_candidate",
        return_value=7.5,
    ):
        updated = replace_weighted_allocation_assignment(
            preview,
            old_uid=old_uid,
            new_uid=new_uid,
        )

    updated_target, updated_source = updated.result.unified.selected
    assert updated_target.score == 16.5
    assert updated_source.score == 12.0
    assert updated_source.assignments[1].score == 12.0
    assert updated.result.unified.total_score == 28.5
