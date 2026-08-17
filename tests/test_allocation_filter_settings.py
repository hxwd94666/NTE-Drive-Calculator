# 测试计算分配筛选设置。
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.allocation_filter_settings import (
    AllocationFilterSettings,
    AllocationFilterSettingsService,
    AllocationFilterValidationError,
    filter_allocation_candidates,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.allocation_lock_service import AllocationLockSnapshot
from src.services.sqlite_allocation_inventory import AllocationInventoryProjection


def _item(uid: str, item_type: str, quality: str) -> dict[str, str]:
    return {"uid": uid, "item_type": item_type, "quality": quality}


def test_default_filter_is_empty_and_preserves_every_candidate() -> None:
    settings = AllocationFilterSettings()
    candidates = (
        _item("blue-drive", "drive", "Blue"),
        _item("purple-tape", "tape", "Purple"),
    )

    result = filter_allocation_candidates(candidates, settings)

    assert result == candidates


def test_quality_filter_only_applies_to_selected_equipment_types() -> None:
    settings = AllocationFilterSettings(
        qualities=frozenset({"Gold"}),
        item_types=frozenset({"tape"}),
    )
    candidates = (
        _item("blue-drive", "drive", "Blue"),
        _item("gold-drive", "drive", "Gold"),
        _item("blue-tape", "tape", "Blue"),
        _item("gold-tape", "tape", "Gold"),
    )

    result = filter_allocation_candidates(candidates, settings)

    assert tuple(item["uid"] for item in result) == (
        "blue-drive",
        "gold-drive",
        "gold-tape",
    )


def test_selected_type_requires_at_least_one_quality() -> None:
    settings = AllocationFilterSettings(item_types=frozenset({"drive"}))

    with pytest.raises(
        AllocationFilterValidationError,
        match="选择分配类型后，必须至少选择一种分配品质",
    ):
        settings.validate()


def test_account_service_defaults_empty_and_round_trips_selection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "user.sqlite3"
        UserDataDao(database_path, account_id="filter-test").close()
        service = AllocationFilterSettingsService(database_path)

        assert service.load() == AllocationFilterSettings()

        expected = AllocationFilterSettings(
            qualities=frozenset({"Purple", "Gold"}),
            item_types=frozenset({"drive", "tape"}),
        )
        assert service.save(expected) == expected
        assert AllocationFilterSettingsService(database_path).load() == expected


def test_account_service_rejects_invalid_persisted_values() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "user.sqlite3"
        UserDataDao(database_path, account_id="filter-test").close()
        service = AllocationFilterSettingsService(database_path)

        with pytest.raises(AllocationFilterValidationError):
            service.save(
                AllocationFilterSettings(
                    qualities=frozenset({"Legendary"}),
                    item_types=frozenset({"drive"}),
                )
            )


@pytest.mark.parametrize(
    "strategy",
    ("role_priority", "global_optimal", "update_mode"),
)
def test_every_classic_strategy_receives_the_same_globally_filtered_pool(
    strategy: str,
    tmp_path: Path,
) -> None:
    from src.features.allocation import runner

    database_path = tmp_path / "user.sqlite3"
    database_path.touch()
    projection = AllocationInventoryProjection(
        snapshot_id=7,
        items=(
            _item("blue-drive", "drive", "Blue"),
            _item("blue-tape", "tape", "Blue"),
            _item("gold-tape", "tape", "Gold"),
        ),
        discarded_count=0,
    )
    lock_snapshot = AllocationLockSnapshot(7, frozenset(), frozenset(), ())
    facade_calls: list[tuple[list[dict[str, str]], str]] = []

    class Facade:
        def __init__(self, **_kwargs) -> None:
            pass

        def execute_allocation_inventory(
            self,
            items,
            _roles,
            _custom_sets,
            selected_strategy,
            **_kwargs,
        ):
            facade_calls.append((items, selected_strategy))
            return {}, None

    user_dao = MagicMock()
    user_dao.__enter__.return_value = user_dao
    user_dao.current_inventory_snapshot_id.return_value = 7
    static_dao = MagicMock()
    static_dao.__enter__.return_value = static_dao
    inventory = MagicMock()
    inventory.build.return_value = projection
    settings = AllocationFilterSettings(
        qualities=frozenset({"Gold"}),
        item_types=frozenset({"tape"}),
    )

    with (
        patch.object(
            runner,
            "_allocation_paths",
            return_value=(database_path, tmp_path, tmp_path, tmp_path, tmp_path / "static.sqlite3"),
        ),
        patch.object(runner, "UserDataDao", return_value=user_dao),
        patch.object(runner, "StaticGameDataDao", return_value=static_dao),
        patch.object(runner, "SqliteAllocationInventory", return_value=inventory),
        patch.object(runner, "build_allocation_lock_snapshot", return_value=lock_snapshot),
        patch("src.app.facade.NTEAppFacade", Facade),
    ):
        result = runner._run_allocation(
            SimpleNamespace(),
            strategy,
            ["角色A"],
            {},
            filter_settings=settings,
        )

    assert result.snapshot_id == 7
    assert len(facade_calls) == 1
    items, received_strategy = facade_calls[0]
    assert received_strategy == strategy
    assert [item["uid"] for item in items] == ["blue-drive", "gold-tape"]
