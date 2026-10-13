# 用实际SQLite字节替换验证官方detail缓存、lazy排名和多角色有界共源。
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import os
from pathlib import Path
import sqlite3

import pytest

from src.integrations._static_release_pin import WindowsStaticFilePin
from src.integrations.static_catalog_release import StaticCatalogReleaseError
from src.services.battle_report.history.role_editor import build_role_editor_details
from src.services.legacy_allocation_static_catalog import build_legacy_allocation_static_catalog
from src.services.official_role.inventory_contexts import load_role_replacement_detail
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.replacement import (
    prepare_official_role_replacement,
    replacement_candidates_for_frozen_context,
    save_official_role_replacement,
)
from src.services.official_role.attributes import calculate_official_role_attribute_summaries
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.official_role.test_replacement_static_source import (
    CURVE, digest, explicit, sources as sources, write_release_manifest,
)

NTE_TEST_TIER = "core"


@pytest.fixture
def same_metadata_bytes(sources, tmp_path):
    paths = [tmp_path / key / "game_static.sqlite3" for key in ("live", "replacement")]
    for ordinal, path in enumerate(paths):
        path.parent.mkdir()
        with closing(sqlite3.connect(sources["paths"]["B"].as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(path)) as target:
                original.backup(target)
                maximum = target.execute(
                    "SELECT MAX(value) FROM equipment_base_attribute_point WHERE curve_id=?", (CURVE,),
                ).fetchone()[0]
                target.execute("UPDATE equipment_base_attribute_point SET value=value*? WHERE curve_id=?",
                               ((0.1 if ordinal == 0 else 0.3) / maximum, CURVE))
                # 两份包仅领域内容不同，dataset/schema/importer/scope完全相同。
                target.execute("UPDATE fork_item SET name_zh=?", ("Byte-A" if ordinal == 0 else "Byte-B",))
                target.commit()
        write_release_manifest(path)
    assert paths[0].stat().st_size == paths[1].stat().st_size
    assert digest(paths[0]) != digest(paths[1])
    return paths


def load_at(sources, path, *, cache=None, candidates=False):
    return load_official_role_detail(
        sources["account"], 1003, static_database_path=path,
        static_schema_version=sources["schema"], asset_root=sources["assets"],
        include_replacement_candidates=candidates, request_cache=cache,
    )


def swap_same_stat(paths):
    before = paths[0].stat()
    os.replace(paths[1], paths[0])
    os.utime(paths[0], ns=(before.st_atime_ns, before.st_mtime_ns))
    write_release_manifest(paths[0])
    assert paths[0].stat().st_size == before.st_size
    assert paths[0].stat().st_mtime_ns == before.st_mtime_ns


def observe_real_io(monkeypatch, path):
    counters = {"sha_calls": 0, "sql": [], "connections": []}
    original_hash, original_connect = WindowsStaticFilePin.sha256, sqlite3.connect

    def streamed(pin):
        result = original_hash(pin)
        counters["sha_calls"] += 1
        return result

    def connected(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        main = next(row[2] for row in connection.execute("PRAGMA database_list") if row[1] == "main")
        if Path(main).resolve() == path:
            counters["connections"].append(connection)
            connection.set_trace_callback(counters["sql"].append)
        return connection

    # 仅记录真实hash和SQLite；不替代DAO、资料、曲线或数值计算。
    monkeypatch.setattr(WindowsStaticFilePin, "sha256", streamed)
    monkeypatch.setattr(sqlite3, "connect", connected)
    return counters


def test_same_path_same_metadata_bytes_invalidate_lazy_rank_and_old_cache(sources, same_metadata_bytes, monkeypatch):
    paths = same_metadata_bytes
    cache = {}
    old = load_at(sources, paths[0], cache=cache)
    context_key = sources["context_key"]
    target = old["equipment_contexts"][context_key]["items"][0]
    old_before = deepcopy(old)
    old_cache_keys = set(cache)
    old_panel = calculate_official_role_attribute_summaries(old, old["equipment_contexts"][context_key]["calculation_items"])
    old_renderer = next(value for key, value in cache.items() if key[1][0] == "skill_name_renderer")
    swap_same_stat(paths)
    io = observe_real_io(monkeypatch, paths[0])
    for action in (
        lambda: load_role_replacement_detail(sources["account"], paths[0], sources["assets"], old, context_key,
                                             static_schema_version=old["static_schema_version"], static_release=old["static_release"]),
        lambda: replacement_candidates_for_frozen_context(old, context_key, target, **explicit(old)),
        lambda: save_official_role_replacement(sources["account"], old, target, {**target, "uid_serial": 602},
                    context_key=context_key, replacement_score=31, current_assignment_scores={"nte-core-17-601": 25}),
    ):
        with pytest.raises(StaticCatalogReleaseError) as error:
            action()
        assert error.value.code == "release_changed"
    assert io["sha_calls"] == 0 and io["connections"] == []
    assert old == old_before and set(cache) == old_cache_keys
    # 已完全物化的A数值可继续纯计算，但不得再混入B曲线/候选。
    assert calculate_official_role_attribute_summaries(old, old["equipment_contexts"][context_key]["calculation_items"]) == old_panel
    fresh = load_at(sources, paths[0], cache=cache)
    assert fresh["static_release"].sha256 != old["static_release"].sha256
    assert fresh["static_release"].dataset_id == old["static_release"].dataset_id
    assert fresh["static_release"].schema_version == old["static_release"].schema_version
    assert fresh["static_release"].scope == old["static_release"].scope
    assert {fork["name_zh"] for fork in old["forks"]} == {"Byte-A"}
    assert {fork["name_zh"] for fork in fresh["forks"]} == {"Byte-B"}
    new_renderer = next(value for key, value in cache.items()
                        if key[0] == fresh["static_release"] and key[1][0] == "skill_name_renderer")
    assert new_renderer is not old_renderer
    fresh_again = load_at(sources, paths[0], cache=cache)
    assert fresh_again["static_release"] == fresh["static_release"]
    assert fresh_again["attributes"] is fresh["attributes"]
    enriched, ranked = prepare_official_role_replacement(
        sources["account"], sources["assets"], fresh, context_key,
        fresh["equipment_contexts"][context_key]["items"][0], **explicit(fresh),
    )
    assert enriched["static_release"] == fresh["static_release"]
    assert next(row["value"] for row in ranked[0]["item"]["main_stats"] if row["property_id"] == "CritBase") == pytest.approx(0.3)
    with UserDataDao(sources["account"]) as user:
        assert user.current_inventory_snapshot_id() == sources["current"]
        assert user.get_loadout_plan(old["equipment_contexts"][context_key]["plan"]["plan_id"])["payload"] == old["equipment_contexts"][context_key]["plan"]["payload"]


def test_prepare_hashes_once_and_pure_sort_runs_after_physical_close(sources, same_metadata_bytes, monkeypatch):
    from src.services.official_role import replacement

    path = same_metadata_bytes[0]
    detail = load_at(sources, path)
    context_key = sources["context_key"]
    io = observe_real_io(monkeypatch, path)
    original_rank = replacement._rank_replacement_values
    sort_calls = []

    def after_close(*args):
        queries_before = len(io["sql"])
        assert io["connections"]
        for connection in io["connections"]:
            with pytest.raises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
        result = original_rank(*args)
        assert len(io["sql"]) == queries_before
        sort_calls.append(True)
        return result

    monkeypatch.setattr(replacement, "_rank_replacement_values", after_close)
    enriched, ranked = prepare_official_role_replacement(
        sources["account"], sources["assets"], detail, context_key,
        detail["equipment_contexts"][context_key]["items"][0], **explicit(detail),
    )
    assert io["sha_calls"] == 1 and len(io["connections"]) == 1 and sort_calls == [True]
    assert ranked and enriched["replacement_candidates_loaded"]
    assert next(row["value"] for row in ranked[0]["item"]["main_stats"] if row["property_id"] == "CritBase") == pytest.approx(0.1)


def test_failed_detail_never_publishes_new_cache(sources, same_metadata_bytes, monkeypatch):
    path = same_metadata_bytes[0]
    cache = {}
    good = load_at(sources, path, cache=cache)
    before = dict(cache)
    # 错误角色在asset缓存候选创建后真实查询失败，不提交半组子资源。
    with pytest.raises(ValueError, match="官方角色不存在"):
        load_official_role_detail(sources["account"], -1, static_database_path=path,
                                  asset_root=sources["assets"], request_cache=cache)
    assert cache == before and good["static_release"]


@pytest.mark.parametrize("consumer", ["legacy", "battle_editor"])
def test_actual_multi_role_consumer_uses_one_hash_and_one_static_connection(sources, same_metadata_bytes, monkeypatch, consumer):
    path = same_metadata_bytes[0]
    io = observe_real_io(monkeypatch, path)
    if consumer == "legacy":
        catalog = build_legacy_allocation_static_catalog(
            config_dir=sources["assets"], user_database_path=sources["account"], static_database_path=path,
        )
        assert len(catalog.roles_db) > 1 and catalog.board_matrices and catalog.shapes_db
    else:
        original = {"characters": [{"character_id": role, "ordinal": number, "profile": {"character_id": role}, "equipment": []}
                                   for number, role in enumerate((1003, 1004))]}
        before = deepcopy(original)
        details = build_role_editor_details(sources["account"], path, original, None, {}, None, [], True, False, False)
        assert [detail["character"]["character_id"] for detail in details] == [1003, 1004]
        assert details[0]["static_release"] == details[1]["static_release"]
        assert original == before
        assert all("static_release" not in detail["profile"] and "static_release" not in detail["original_profile"] for detail in details)
    assert io["sha_calls"] == 1 and len(io["connections"]) == 1
    for connection in io["connections"]:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_role_page_transports_verified_token_to_shared_popup(sources, same_metadata_bytes, monkeypatch):
    from types import SimpleNamespace
    from src.features.official_role import role_equipment
    from src.services.official_role.static_source import frozen_static_source

    detail = load_at(sources, same_metadata_bytes[0], candidates=True)
    target = detail["equipment_contexts"][sources["context_key"]]["items"][0]
    received = []

    def popup(window, current, item, *, static_database_path, static_schema_version, static_release,
              on_saved, context_key="saved"):
        frozen_static_source(current, static_database_path=static_database_path,
                             static_schema_version=static_schema_version, static_release=static_release)
        received.append((static_release, context_key, item))
        return False

    monkeypatch.setattr(role_equipment, "show_official_role_replacement", popup)
    role_equipment._show_replacement_optimizer(SimpleNamespace(), detail, target, context_key=sources["context_key"])
    assert received == [(detail["static_release"], sources["context_key"], target)]


@pytest.mark.parametrize("boundary", ["accepted", "account_changed", "role_changed", "page_hidden"])
def test_role_page_prepared_callback_never_relabels_or_publishes_after_invalid_boundary(
    sources, same_metadata_bytes, monkeypatch, boundary,
):
    from types import SimpleNamespace
    from src.features.official_role import role_equipment, role_shell
    from src.features.official_role.dependencies import OfficialRoleDependencies
    from src.services.official_role.static_source import frozen_static_source

    detail = load_at(sources, same_metadata_bytes[0])
    context_key = sources["context_key"]
    target = detail["equipment_contexts"][context_key]["items"][0]
    prepared = prepare_official_role_replacement(sources["account"], sources["assets"], detail, context_key,
                                                target, **explicit(detail))
    before = deepcopy(detail)
    pending, received = [], []
    dependency = object()
    visible = [True]
    window = SimpleNamespace(app_context=dependency, _current_official_role_id=1003,
                             _official_role_page=SimpleNamespace(isVisible=lambda: visible[0]))
    controller = SimpleNamespace(dependencies=dependency, request_replacement=lambda *args: pending.append(args[3]))
    monkeypatch.setattr(role_shell, "_role_controller", lambda _window: controller)
    monkeypatch.setattr(OfficialRoleDependencies, "from_app_context", staticmethod(lambda context: context))

    def popup(_window, current, item, *, static_database_path, static_schema_version, static_release,
              prepared_candidates, on_saved, context_key="saved"):
        frozen_static_source(current, static_database_path=static_database_path,
                             static_schema_version=static_schema_version, static_release=static_release)
        received.append((static_release, context_key, item, prepared_candidates))
        return False

    monkeypatch.setattr(role_equipment, "show_official_role_replacement", popup)
    role_equipment._show_replacement_optimizer(window, detail, target, context_key=context_key)
    assert len(pending) == 1 and not received
    if boundary == "account_changed":
        window.app_context = object()
    elif boundary == "role_changed":
        window._current_official_role_id = 1004
    elif boundary == "page_hidden":
        visible[0] = False
    # 投递实际服务已经完成物化的结果，验证UI接收门禁而不是让假worker先丢弃。
    pending[0](prepared)
    assert bool(received) is (boundary == "accepted")
    if received:
        assert received[0] == (detail["static_release"], context_key, target, prepared[1])
    assert detail == before
