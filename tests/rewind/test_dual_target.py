# 验证双组目标只交集正缺口、按面积归一并在八槽与实际需求内确定性分配。
from dataclasses import replace
from copy import deepcopy

import pytest

from src.domain.rewind.dual_target import DualTargetRequest, GroupTarget, recommend_dual_targets
from src.domain.rewind.rewind_loadout import RewindSavedDrive, RewindSlotReference, RewindSlotSummary
from src.domain.rewind.rewind_shape_recommendation import RewindPricingRule, RewindShape


def slot(character, *drives):
    return RewindSlotSummary(RewindSlotReference(character, character, character), str(character),
                             'primary', 0, 1, 100, 'ready', '', tuple(RewindSavedDrive(*drive) for drive in drives))


def request(first=(1,), second=(2,), *, improve='A', push='SSS'):
    return DualTargetRequest(GroupTarget(tuple(RewindSlotReference(i, i, i) for i in first), improve),
                             GroupTarget(tuple(RewindSlotReference(i, i, i) for i in second), push))


def test_intersection_is_of_positive_gaps_and_uses_independent_targets():
    result = recommend_dual_targets(request(), (RewindShape('a', 4), RewindShape('b', 2)),
        (slot(1, ('a', 4, 16), ('b', 2, 2)), slot(2, ('a', 4, 0), ('b', 2, 10))))
    assert [row.shape_id for row in result.shared] == ['b']
    assert result.groups[0].demands[0].normalized_gap == pytest.approx(.3)
    assert result.groups[1].demands[1].normalized_gap == pytest.approx(.2)
    assert result.recommendations[0].quantity == 2
    assert result.empty_slots == 6
    assert result.total_cost == RewindPricingRule().cost_for_quantity(2)


def test_normalization_does_not_favour_large_area_or_larger_group():
    shapes = (RewindShape('a', 4), RewindShape('b', 2))
    slots = (slot(1, ('a', 4, 0), ('b', 2, 0)), slot(2, ('a', 4, 0), ('b', 2, 0)))
    result = recommend_dual_targets(request(improve='S', push='S'), shapes, slots)
    assert result.shared[0].weight == result.shared[1].weight == .5
    assert [row.shape_id for row in result.shared] == ['a', 'b']
    assert recommend_dual_targets(request(improve='S', push='S'), shapes[::-1], slots[::-1]) == result
    enlarged = recommend_dual_targets(request(first=(1, 3), improve='S', push='S'), shapes,
        slots + (slot(3, ('a', 4, 0), ('b', 2, 0)),))
    assert [row.weight for row in enlarged.shared] == [row.weight for row in result.shared]


def test_no_intersection_and_fully_satisfied_group_never_fall_back():
    shapes = (RewindShape('a', 4), RewindShape('b', 2))
    for slots in [(slot(1, ('a', 4, 0)), slot(2, ('b', 2, 0))),
                  (slot(1, ('a', 4, 40)), slot(2, ('a', 4, 0)))]:
        result = recommend_dual_targets(request(), shapes, slots)
        assert result.recommendations == () and result.empty_slots == 8
        assert result.total_cost == 0


def test_over_eight_shapes_are_stably_truncated_and_reported():
    shapes = tuple(RewindShape(str(i), 1) for i in range(10))
    slots = tuple(slot(i, *((str(n), 1, 0) for n in range(10))) for i in [1, 2])
    result = recommend_dual_targets(request(), shapes, slots)
    assert len(result.recommendations) == 8 and result.uncovered_shapes == ('8', '9')
    assert all(row.quantity == 1 for row in result.recommendations)


def test_repeat_allocation_uses_marginal_cost_and_never_exceeds_need():
    shapes = (RewindShape('a', 1), RewindShape('b', 1))
    drives = [('a', 1, 0)] * 10 + [('b', 1, 0)] * 10
    result = recommend_dual_targets(request(), shapes, (slot(1, *drives), slot(2, *drives)))
    assert [row.quantity for row in result.recommendations] == [4, 4]
    assert result.total_cost == 200


@pytest.mark.parametrize('bad', ['cross_group', 'duplicate', 'empty', 'percent', 'grade', 'limit'])
def test_invalid_requests_rejected(bad):
    with pytest.raises(ValueError):
        if bad == 'cross_group': request(second=(1,))
        elif bad == 'duplicate': request(first=(1, 1))
        elif bad == 'empty': request(first=())
        elif bad == 'percent': replace(request().improve, custom_percent=float('nan'))
        elif bad == 'grade': replace(request().push, grade='missing')
        else: replace(request(), selection_limit=9)


def test_custom_targets_zero_scores_and_source_metadata_are_preserved():
    original = request()
    req = replace(original, improve=replace(original.improve, custom_percent=10),
                  push=replace(original.push, custom_percent=20))
    slots = (slot(1, ('a', 2, 0)), slot(2, ('a', 2, 0)))
    frozen = deepcopy(slots)
    result = recommend_dual_targets(req, (RewindShape('a', 2),), slots)
    assert result.groups[0].items[0].target_score == 2
    assert result.groups[1].items[0].target_score == 4
    assert result.groups[0].items[0].source_snapshot_id == 1
    assert slots == frozen


def test_cancel_and_invalid_or_missing_scores_do_not_produce_partial_result():
    req = request()
    def cancel():
        raise InterruptedError('cancel')
    with pytest.raises(InterruptedError):
        recommend_dual_targets(req, (RewindShape('a', 2),), (slot(1, ('a', 2, 0)), slot(2, ('a', 2, 0))), checkpoint=cancel)
    for score in [None, float('inf'), -1, True]:
        with pytest.raises(ValueError):
            recommend_dual_targets(req, (RewindShape('a', 2),), (slot(1, ('a', 2, score)), slot(2, ('a', 2, 0))))
