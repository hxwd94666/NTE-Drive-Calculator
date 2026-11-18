# 验证形状余量按冻结逐件保存分与目标聚合、排序与校验，且超出八种形状时仍保留余量。
import pytest

from src.domain.rewind.rewind_loadout import RewindSlotReference
from src.domain.rewind.shape_overview import DriveMargin, summarize_shape_margins
from tests.rewind.test_dual_target_service import scene
from tests.rewind.test_slot_selection import service_fixture


def margin(shape, area, saved, target, *, character=1, group=None):
    return DriveMargin(character, character, shape, area, saved, target, group)


def test_rows_sum_shortfall_and_surplus_and_sort_by_shortfall_then_surplus():
    rows = summarize_shape_margins([
        margin('A', 2, 4, 10), margin('A', 2, 15, 10, character=2),
        margin('B', 4, 10, 16), margin('B', 4, 20, 16, character=2),
        margin('C', 3, 9, 12), margin('D', 3, 30, 12),
    ])
    # A、B 各缺 6 分：多分少的 B 在前；只有多分的 D 排最后。
    assert [row.shape_id for row in rows] == ['B', 'A', 'C', 'D']
    a = rows[1]
    assert (a.totals.shortfall_count, a.totals.shortfall) == (1, 6)
    assert (a.totals.surplus_count, a.totals.surplus) == (1, 5)
    assert rows[0].totals.surplus == 4 and rows[3].totals.shortfall_count == 0


def test_exactly_on_target_counts_as_met_without_surplus():
    row, = summarize_shape_margins([margin('A', 2, 10, 10)])
    assert (row.totals.shortfall_count, row.totals.surplus_count, row.totals.surplus) == (0, 1, 0)
    assert row.demand == 'none'


def test_dual_rows_keep_each_group_and_sort_by_normalized_gap():
    rows = summarize_shape_margins([
        margin('Big', 4, 20, 30, group='improve'), margin('Big', 4, 40, 30, character=2, group='push'),
        margin('Small', 2, 2, 8, group='improve'), margin('Small', 2, 2, 8, character=2, group='push'),
    ], normalized=True)
    # 归一化缺口 Small=0.6 > Big=0.25；多分只算高分组那件。
    assert [row.shape_id for row in rows] == ['Small', 'Big']
    assert rows[0].demand == 'shared' and rows[1].demand == 'improve'
    improve, push = rows[1].groups
    assert (improve.shortfall_count, push.surplus_count, push.surplus) == (1, 1, 10)
    assert rows[0].totals.normalized_shortfall == pytest.approx(0.6)


@pytest.mark.parametrize('bad', [dict(area=0), dict(saved_score=float('nan')), dict(group='other'),
                                 dict(target_score=True), dict(character_id=0), dict(slot_id=True), dict(shape_id='')])
def test_invalid_margin_data_is_rejected(bad):
    values = dict(character_id=1, slot_id=1, shape_id='A', area=2, saved_score=1,
                  target_score=2, group=None) | bad
    with pytest.raises(ValueError):
        DriveMargin(**values)


@pytest.mark.parametrize('limit', [8, 0])
def test_legacy_analysis_freezes_every_drive_even_when_shapes_exceed_the_limit(tmp_path, limit):
    service, _dao = service_fixture(tmp_path)
    result = service.analyze_for_targets(target_character_ids=(1004,), selection_limit=limit,
                                         selected_slots=(RewindSlotReference(1004, 1, 11),))
    assert bool(result.notice) == (limit == 0) and bool(result.recommendations) == (limit == 8)
    assert len(result.drive_margins) == 5
    assert {(row.character_id, row.slot_id, row.saved_score, row.target_score, row.group)
            for row in result.drive_margins} == {(1004, 1, 10, 20, None)}


def test_dual_analysis_freezes_margins_per_group(tmp_path):
    service, _dao, request = scene(tmp_path)
    result = service.analyze_dual_target_shortfalls(request)
    groups = {row.group: row.target_score for row in result.drive_margins}
    assert groups == {'improve': 16, 'push': 28}
    assert len(result.drive_margins) == 10


@pytest.mark.parametrize('rows', [
    [margin('A', 2, 1, 2), margin('B', 2, 1, 2, group='push')],
    [margin('A', 2, 1, 2), margin('A', 3, 1, 2)],
    ['not a margin'],
])
def test_inconsistent_margin_batches_are_rejected(rows):
    with pytest.raises(ValueError):
        summarize_shape_margins(rows)
