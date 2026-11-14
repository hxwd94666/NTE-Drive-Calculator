# 验证大批 UID 定位保留完整物品/词条、过滤顺序与固定快照身份，不受参数上限影响。
import sqlite3

from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.direct_catalog_fixture import create_catalog_case


NTE_TEST_TIER = "core"


def test_large_uid_selection_keeps_global_order_fields_filters_and_limit(tmp_path):
    case = create_catalog_case(tmp_path, copies=256)
    with UserDataDao(case["user_database_path"]) as dao, dao.read_consistent_state():
        source = case["snapshot_id"]
        dao._db().setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 900)
        all_items = dao.list_inventory_items(source)
        requested = [(row["uid_serial"], row["uid_slot"]) for row in reversed(all_items)]
        requested += requested[:20] + [(99999999, 99)]
        assert len(requested) > 1000
        assert dao.list_inventory_items(source, uids=iter(requested)) == all_items
        expected = [row for row in all_items if row["kind"] == "module" and not row["equipped"]]
        assert dao.list_inventory_items(source, kind="module", equipped=False, uids=requested, limit=17) == expected[:17]
        assert dao.list_inventory_items(source, uids=[]) == []
        assert dao.list_inventory_items(source, uids=[(99999999, 99)]) == []
        assert dao.list_inventory_items(source + 1000, uids=requested) == []


def test_sparse_uid_selection_retains_own_snapshot_and_all_stat_rows(tmp_path):
    case = create_catalog_case(tmp_path, copies=128)
    with UserDataDao(case["user_database_path"]) as dao, dao.read_consistent_state():
        source = case["snapshot_id"]
        all_items = dao.list_inventory_items(source)
        subset = all_items[::37]
        requested = [(row["uid_serial"], row["uid_slot"]) for row in subset]
        result = dao.list_inventory_items(source, uids=requested)
        assert result == subset
        assert all(row["snapshot_id"] == source for row in result)
        assert sum(len(row["main_stats"]) + len(row["sub_stats"]) for row in result) > len(result)
