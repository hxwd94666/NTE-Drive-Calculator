# 验证伤害作业的原始装备与评分投影来自一次一致读取，篡改仍拒绝。
from types import SimpleNamespace

import pytest

from src.domain.equipment.properties.equipment_normalizer import normalize_equipment_item
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.axis.axis_postprocess import freeze_actual_allocation_inventory
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.test_axis_postprocess_component import axis_facade_case

NTE_TEST_TIER = "core"


def test_damage_inventory_uses_one_snapshot_read_and_still_checks_canonical_identity(tmp_path, monkeypatch):
    case = axis_facade_case(tmp_path)
    request = SimpleNamespace(inventory=tuple(SimpleNamespace(**normalize_equipment_item(row)) for row in case["inventory"]))
    with static_catalog_read_unit(case["dependencies"].static_database_path) as unit, UserDataDao(case["dependencies"].user_database_path) as dao:
        calls = []
        actual = dao.export_inventory_snapshot

        def read(snapshot):
            calls.append(snapshot)
            return actual(snapshot)

        monkeypatch.setattr(dao, "export_inventory_snapshot", read)
        with dao.read_consistent_state():
            frozen = freeze_actual_allocation_inventory(request, dao, unit, case["source_snapshot_id"])
            assert len(calls) == 1
            assert set(frozen) == {item.uid for item in request.inventory}
            request.inventory[0].main_value = -123
            with pytest.raises(ValueError, match="不一致"):
                freeze_actual_allocation_inventory(request, dao, unit, case["source_snapshot_id"])
