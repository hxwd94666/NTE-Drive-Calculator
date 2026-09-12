# 验证原生目标身份有值但血量缺失时，展示契约保留未知。
from src.domain.battle_target import BattleTargetInstanceResolution
from src.integrations.native_battle_page_wire import decode
from src.services.battle_inferred_target_condition_service import BattleInferredTargetIdentity


def test_native_target_resolution_keeps_unknown_hp():
    row = {
        "scope_half": "lower", "captured_target_id": "test-instance",
        "resolved_monster_id": "mon_01_BP", "default_monster_id": "mon_01_BP",
        "possible_monster_ids": ["mon_01_BP"], "resolution_mode": "core_exact",
        "initial_max_hp": None, "target_condition": None,
    }
    result = decode(row, BattleTargetInstanceResolution)
    assert result.initial_max_hp is None
    assert result.resolved_monster_id == "mon_01_BP"
    row["initial_max_hp"] = 123.0
    assert decode(row, BattleTargetInstanceResolution).initial_max_hp == 123.0


def test_native_target_identity_keeps_unknown_hp():
    row = {"scope_half": "upper", "captured_target_id": "test-instance",
           "target_name": "测试怪物", "inferred_monster_id": "mon_01_BP", "initial_max_hp": None}
    assert decode(row, BattleInferredTargetIdentity).initial_max_hp is None
