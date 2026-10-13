# 保留帮助入口的正式导航目标与分配上限选择，不锁定步骤文案和私有结构。
from src.features.home.feature_guide import FEATURE_GUIDES
from src.services.allocation.filter_settings import AllocationFilterSettings


def test_feature_guides_have_correct_entry_and_navigation():
    assert {key for guide in FEATURE_GUIDES for _, key in guide.destinations} == {
        'execute', 'equipment', 'warehouse', 'identify', 'toolbox', 'battle_report',
    }


def test_new_allocation_limit_is_2000_but_saved_choice_survives():
    assert AllocationFilterSettings().blueprint_combo_limit == 2000
    assert AllocationFilterSettings.from_payload({}).blueprint_combo_limit == 2000
    assert AllocationFilterSettings.from_payload({'blueprint_combo_limit': 500}).blueprint_combo_limit == 500
