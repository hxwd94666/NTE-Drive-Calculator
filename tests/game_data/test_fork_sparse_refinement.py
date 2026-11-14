# 验证真实稀疏弧盘曲线的中间精炼值与未知模式拒绝语义。
import pytest

from tools.game_data.static_database_combat_imports import fork_refinement_curve_value
from tools.game_data.static_database_build_support import StaticDatabaseError


def curve(points, mode='RCIM_Linear'):
    return {'Keys': [{'Time': t, 'Value': v} for t, v in points],
            'InterpMode': mode, 'PreInfinityExtrap': 'RCCE_Constant',
            'PostInfinityExtrap': 'RCCE_Constant'}


@pytest.mark.parametrize('points,expected', [
    ([(1, .15), (4, .2625), (5, .3)], [.15, .1875, .225, .2625, .3]),
    ([(1, .32), (2, .4), (3, .48), (5, .64)], [.32, .4, .48, .56, .64]),
    ([(1, .08), (5, .2)], [.08, .11, .14, .17, .2]),
])
def test_sparse_linear_refinements(points, expected):
    assert [fork_refinement_curve_value(curve(points), level) for level in range(1, 6)] == pytest.approx(expected)


def test_constant_and_boundary_semantics():
    source = curve([(2, .1), (4, .3)], 'RCIM_Constant')
    assert [fork_refinement_curve_value(source, x) for x in range(1, 6)] == [.1, .1, .1, .3, .3]


def test_unknown_interpolation_does_not_use_previous_value():
    with pytest.raises(StaticDatabaseError):
        fork_refinement_curve_value(curve([(1, .1), (5, .3)], 'RCIM_Cubic'), 3)
