# 验证原生物品归档只投影已确认材料数量，且账号与静态目录隔离。
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.domain.work_mode import WorkMode
from src.services.progression.owned_material_import import (
    CultivationOwnedMaterialImportService,
    project_observed_materials,
    project_packet_materials,
)
from src.ui.controllers.inventory_sync_controller import _import_cultivation_materials
from src.storage.sqlite.inventory.all_item_snapshot_dao import read_latest_all_item_snapshot_archive
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_all_item_snapshot_storage import all_items


def _snapshot(*records):
    snapshot = all_items(0)
    snapshot["records"] = list(records)
    snapshot["recordCount"] = len(records)
    snapshot["sourceRecordCount"] = len(records)
    return snapshot


def _material(item_id, amount, **changes):
    return {
        "kind": "HTItem", "source": "InventoryContainerMap.InventoryItemsMap",
        "ItemID": item_id, "Amount": amount,
        "UniqueID": {"key": "synthetic", "serial": 1, "solt": 0},
        "mapUniqueID": {"key": "synthetic", "serial": 1, "solt": 0},
        "mapUidMatchesItem": True, "duplicateMapUidObserved": False,
        "bIsTemporary": False, **changes,
    }


def test_projection_matches_static_id_and_preserves_absence_as_unknown():
    snapshot = _snapshot(
        _material("known", 7), _material("not-static", 10),
        {"kind": "HTDrive", "ItemID": "other", "Amount": 12},
    )
    assert project_observed_materials(snapshot, frozenset({"known", "other", "absent"})) == (
        {"known": 7}, 0,
    )


def test_lowercase_gold_is_imported_as_gold_without_merging_fons():
    snapshot = _snapshot(_material("gold", 7), _material("Fons", 11))
    assert project_observed_materials(snapshot, frozenset({"Gold", "Fons"})) == (
        {"Gold": 7, "Fons": 11}, 0,
    )


def test_gold_alias_and_canonical_duplicate_are_ambiguous():
    snapshot = _snapshot(_material("gold", 7), _material("Gold", 9))
    assert project_observed_materials(snapshot, frozenset({"Gold"})) == ({}, 1)


@pytest.mark.parametrize("bad", [
    _material("known", True),
    _material("known", -1),
    _material("known", 100_000_000),
    _material("known", 5, source="OtherContainer"),
    _material("known", 5, mapUidMatchesItem=False),
    _material("known", 5, duplicateMapUidObserved=True),
    _material("known", 5, bIsTemporary=True),
])
def test_invalid_matched_record_never_imports_as_zero(bad):
    assert project_observed_materials(_snapshot(bad), frozenset({"known"})) == ({}, 1)


def test_duplicate_material_id_is_ambiguous_not_summed_or_overwritten():
    snapshot = _snapshot(_material("known", 3), _material("known", 4))
    assert project_observed_materials(snapshot, frozenset({"known"})) == ({}, 1)


def _packet(*items):
    return {
        "source": "packet", "complete": False, "generation": 1,
        "observed_at_unix_ms": 1_000, "item_count": len(items), "items": list(items),
    }


def _packet_item(item_id, amount, slot=1, serial=1):
    return {"uid": {"slot": slot, "serial": serial}, "item_id": item_id, "amount": amount}


def test_packet_materials_project_only_observed_static_ids():
    snapshot = _packet(
        _packet_item("known", 7), _packet_item("gold", 8, 2),
        _packet_item("unrelated", 10, 3),
    )
    assert project_packet_materials(
        snapshot, frozenset({"known", "Gold", "absent"})
    ) == ({"known": 7, "Gold": 8}, 0)


def test_packet_duplicate_or_invalid_material_is_not_imported_as_zero():
    snapshot = _packet(
        _packet_item("known", 7), _packet_item("known", 9, 2),
        _packet_item("other", True, 3),
    )
    assert project_packet_materials(snapshot, frozenset({"known", "other"})) == ({}, 2)


@pytest.mark.parametrize("change", [
    {"complete": True}, {"source": "native"}, {"item_count": 2},
    {"generation": 0}, {"items": []},
])
def test_packet_materials_reject_invalid_or_empty_observation(change):
    snapshot = _packet(_packet_item("known", 7))
    snapshot.update(change)
    with pytest.raises(ValueError):
        project_packet_materials(snapshot, frozenset({"known"}))


def test_packet_import_uses_static_catalog_and_marks_partial_source(tmp_path):
    class Static:
        def __init__(self, path):
            assert path == tmp_path / "static.sqlite3"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def progression_item_ids(self):
            return frozenset({"known", "absent"})

    importer = CultivationOwnedMaterialImportService(
        user_database_path=tmp_path / "account.sqlite3",
        static_database_path=tmp_path / "static.sqlite3", account_id="a",
    )
    with patch("src.services.progression.owned_material_import.StaticGameDataDao", Static):
        result = importer.load_packet_observations(_packet(_packet_item("known", 7)))
    assert result.quantities == (("known", 7),)
    assert result.source == "packet"
    assert result.snapshot_id is None
    assert not (tmp_path / "account.sqlite3").exists()


def test_saved_packet_materials_import_after_sync_is_closed(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        dao.save_packet_item_observation(
            dict(_packet(_packet_item("known", 7)), sequence=1),
            account_id="a", check=lambda: None,
        )

    class Static:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def progression_item_ids(self):
            return frozenset({"known", "absent"})

    with patch("src.services.progression.owned_material_import.StaticGameDataDao", Static):
        result = CultivationOwnedMaterialImportService(
            user_database_path=database,
            static_database_path=tmp_path / "static.sqlite3", account_id="a",
        ).load_latest_packet()
    assert result.quantities == (("known", 7),)
    assert result.source == "packet"


def test_low_mode_material_button_reads_saved_observation_without_running_core(tmp_path):
    account = SimpleNamespace(active_account_id="a", user_database_path=tmp_path / "a.sqlite3")
    window = SimpleNamespace(
        app_context=SimpleNamespace(
            account=account,
            paths=SimpleNamespace(cultivation_database_path=tmp_path / "static.sqlite3"),
        ),
        work_mode_service=SimpleNamespace(settings=SimpleNamespace(mode=WorkMode.LOW)),
        _inventory_sync_service=None,
    )
    saved = object()
    with patch.object(CultivationOwnedMaterialImportService, "load_latest_packet", return_value=saved), \
            patch.object(CultivationOwnedMaterialImportService, "load_latest", side_effect=AssertionError):
        assert _import_cultivation_materials(window) is saved


def test_incomplete_archive_is_rejected_even_if_material_row_is_valid():
    snapshot = _snapshot(_material("known", 3))
    snapshot["collectionComplete"] = False
    with pytest.raises(ValueError):
        project_observed_materials(snapshot, frozenset({"known"}))


def test_read_only_import_checks_account_and_archive_hash(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="a") as dao:
        dao.save_all_item_snapshot(
            _snapshot(_material("known", 8), _material("other", 4)),
            account_id="a", check=lambda: None,
        )

    class Static:
        def __init__(self, path):
            assert path == tmp_path / "static.sqlite3"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def progression_item_ids(self):
            return frozenset({"known"})

    with patch("src.services.progression.owned_material_import.StaticGameDataDao", Static):
        result = CultivationOwnedMaterialImportService(
            user_database_path=database,
            static_database_path=tmp_path / "static.sqlite3",
            account_id="a",
        ).load_latest()
    assert result.quantities == (("known", 8),)
    assert result.skipped_item_count == 0
    assert result.snapshot_id > 0
    assert result.saved_at_utc
    with pytest.raises(UserDataValidationError):
        read_latest_all_item_snapshot_archive(database, account_id="b")

    with UserDataDao(database) as dao:
        dao._db().execute("UPDATE all_item_snapshot SET raw_snapshot_json='{}'")
        dao._db().commit()
    with pytest.raises(ValueError, match="校验失败"):
        CultivationOwnedMaterialImportService(
            user_database_path=database,
            static_database_path=tmp_path / "static.sqlite3",
            account_id="a",
        ).load_latest()
