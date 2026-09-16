# 用源头合成输入经过真实 Core 的公开响应，验证原生背包到游戏当前配装的互通。
from copy import deepcopy
import json
from pathlib import Path

from src.services.official_role_page_service import load_official_role_detail
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_inventory_sync_integration import ProjectionCore, setup_sync


def test_source_authored_core_responses_preserve_coordinates_percent_and_empty_role(tmp_path):
    fixture = json.loads((Path(__file__).parent / "fixtures/native_business_source_responses.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    assert fixture["origin"] == "source_authored_synthetic_fixture_not_live_capture"
    exchanges = fixture["inventory"]
    expected_items = [item for exchange in exchanges[1:] for item in exchange["response"]["result"]["items"]]
    expected_characters = exchanges[1]["response"]["result"]["characters"]
    assert len(expected_items) == 3 and len(expected_characters) == 2

    class SourceResponseCore(ProjectionCore):
        def call(self, method, params, **kwargs):
            self.calls.append((method, deepcopy(params)))
            if method == "native.snapshot.refresh":
                assert params == exchanges[0]["request"]["params"]
                return deepcopy(exchanges[0]["response"]["result"])
            if method == "native.inventory.page":
                exchange = next(entry for entry in exchanges[1:] if entry["request"]["params"]["offset"] == params["offset"])
                expected = exchange["request"]["params"]
                assert params["snapshotId"] == expected["snapshotId"]
                # The producer may return a smaller page than the caller's limit.
                assert params["limit"] >= expected["limit"]
                return deepcopy(exchange["response"]["result"])
            return {}

    core = SourceResponseCore()
    session, service, clients = setup_sync(tmp_path, core)
    try:
        service.start()
        state = service.wait_for_snapshot(timeout=3)
        service.stop()
        assert clients == [core]
        assert [params["offset"] for method, params in core.calls if method == "native.inventory.page"] == [0, 2]
        with UserDataDao(service.database_path) as dao:
            payload = dao.raw_snapshot(state.last_snapshot_id)["params"]
            assert payload["items"] == expected_items
            assert payload["characters"] == expected_characters
            assert payload["native_snapshot"]["complete"] is False
            assert payload["native_snapshot"]["sourceCoverage"] == "unknown"
            all_items = dao.list_current_inventory_items()
            assert len(all_items) == 3
            module = next(row for row in all_items if row["uid_serial"] == 700001)
            assert module["equipped_placement"] == {"row": 1, "column": 1}
            assert module["sub_stats"][0]["percent"] is True
            assert module["sub_stats"][0]["value"] == expected_items[0]["sub_stats"][0]["value"]
            unequipped = next(row for row in all_items if row["uid_serial"] == 700003)
            assert unequipped["equipped"] is False and unequipped["discarded"] is True
            assert unequipped["equipped_character_id"] is None and unequipped["equipped_placement"] is None
            assert dao.list_character_instance_mappings(1003)[0]["uid_serial"] == 900001
            assert dao.list_character_instance_mappings(1004)[0]["uid_serial"] == 900002
        current = load_official_role_detail(service.database_path, 1003, shared_database_path=tmp_path / "shared.sqlite3")["equipment_contexts"]["current"]
        assert current["available"] and len(current["items"]) == 2
        module = next(row for row in current["items"] if row["kind"] == "module")
        assert module["equipped_placement"] == {"row": 1, "column": 1}
        assert module["sub_stats"][0]["value"] == expected_items[0]["sub_stats"][0]["value"]
        assert module["sub_stats"][0]["percent"] is True
        empty = load_official_role_detail(service.database_path, 1004, shared_database_path=tmp_path / "shared.sqlite3")["equipment_contexts"]["current"]
        assert not empty["available"] and not empty["items"]
    finally:
        service.stop()
        session.close()
