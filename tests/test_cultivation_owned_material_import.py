# 验证原生物品归档只投影已确认材料数量，且账号与静态目录隔离。
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.cultivation_owned_material_import import (
    CultivationOwnedMaterialImportService,
    project_observed_materials,
)
from src.storage.sqlite.all_item_snapshot_dao import read_latest_all_item_snapshot_archive
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

    with patch("src.services.cultivation_owned_material_import.StaticGameDataDao", Static):
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
