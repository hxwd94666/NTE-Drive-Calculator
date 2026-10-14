# 验证分配上限默认值及已有配置选择的兼容读取。
from src.services.allocation.filter_settings import AllocationFilterSettings


def test_new_allocation_limit_is_2000_but_saved_choice_survives():
    assert AllocationFilterSettings().blueprint_combo_limit == 2000
    assert AllocationFilterSettings.from_payload({}).blueprint_combo_limit == 2000
    assert AllocationFilterSettings.from_payload({'blueprint_combo_limit': 500}).blueprint_combo_limit == 500
