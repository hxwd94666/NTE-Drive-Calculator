# 验证同次游戏配装共用正式读取单元，角色详情只在本请求内复用，失败不发布结果。
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.domain.catalog.static_catalog import StaticCatalogRelease
from src.features.inventory.equipment.display import display_loaders as loaders
from src.integrations.static_catalog.static_catalog_release import StaticCatalogReadUnit, StaticCatalogReleaseError
from src.services.equipment.loadouts import saved_loadout_attribute_summary_service as summaries

NTE_TEST_TIER = "core"


def test_borrowed_role_details_are_request_local_and_items_are_not_cached(tmp_path, monkeypatch):
    static = (tmp_path / "static.sqlite3").absolute()
    release = StaticCatalogRelease(static, "fixture", 1, 1, "frozen", False)
    dao = object()
    unit = StaticCatalogReadUnit(dao, release)
    detail = Mock(return_value={"role": "frozen"})
    monkeypatch.setattr(summaries, "load_official_role_detail", detail)
    projection = Mock(side_effect=lambda items, borrowed: tuple(items) if borrowed is dao else None)
    monkeypatch.setattr(summaries, "project_equipment_items_to_max_level", projection)
    monkeypatch.setattr(summaries, "calculate_official_role_attribute_summaries", lambda role, items: items)
    cache = {}

    def load(account, item, request_cache=cache, owner=unit):
        return summaries.load_saved_loadout_attribute_summaries(
            account, static, 1003, (item,), request_cache=request_cache, static_read_unit=owner
        )

    first, second = {"value": 1}, {"value": 2}
    assert load(tmp_path / "one.sqlite3", first) == (first,)
    assert load(tmp_path / "one.sqlite3", second) == (second,)
    detail.assert_called_once()
    assert projection.call_count == 2
    load(tmp_path / "two.sqlite3", second)
    assert detail.call_count == 2
    load(tmp_path / "one.sqlite3", second, {})
    assert detail.call_count == 3
    unit._active = False
    with pytest.raises(StaticCatalogReleaseError, match="已结束"):
        load(tmp_path / "one.sqlite3", first)
    assert detail.call_count == 3


@pytest.mark.parametrize('entry, materializer', [
    ('_load_game_equipment_display_states', '_materialize_game_equipment_display_states'),
    ('_load_sqlite_equipment_display_states', '_materialize_sqlite_equipment_display_states'),
])
def test_read_unit_exit_failure_prevents_returning_projection(monkeypatch, entry, materializer):
    @contextmanager
    def changed(_path, **_kwargs):
        yield SimpleNamespace(release=SimpleNamespace(database_path=Path("frozen.sqlite3")))
        raise StaticCatalogReleaseError("static changed", code="release_changed")

    work = Mock(return_value={"projection": "must not publish"})
    monkeypatch.setattr(loaders, "static_catalog_read_unit", changed)
    monkeypatch.setattr(loaders, materializer, work)
    with pytest.raises(StaticCatalogReleaseError, match="static changed"):
        getattr(loaders, entry)("isolated.sqlite3")
    work.assert_called_once()
