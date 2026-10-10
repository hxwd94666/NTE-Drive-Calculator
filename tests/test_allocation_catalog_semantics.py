# 验证计算目录仅随业务输入改变且无关写入不造成反复重建。
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.services.allocation_catalog_service import AllocationCatalogService, CatalogDependencies
from src.services.character_weight_service import save_account_character_weights
from src.storage.sqlite.user_data_dao import UserDataDao

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.delenv("NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE", raising=False)
    config = tmp_path / "config"
    config.mkdir()
    shutil.copy2(ROOT / "config/stats.json", config / "stats.json")
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    calls = []
    deps = CatalogDependencies("fixture", 1, config, database, ROOT / "data/game_static.sqlite3", tmp_path)
    result = AllocationCatalogService(deps, lambda *_args: calls.append(object()) or calls[-1])
    return result, calls, database


def unrelated_write(database, number):
    with UserDataDao(database) as dao:
        dao.replace_application_setting_copy("unrelated_fixture", {"counter": number})


def test_unrelated_writes_reuse_catalog_and_semantic_identity(service):
    subject, calls, database = service
    baseline = subject.read()
    for number in range(5):
        unrelated_write(database, number)
        current = subject.read(verify=True)
        assert current[:2] == baseline[:2] and current[2] is False
    assert len(calls) == 1


def test_manual_weight_edit_rebuilds_and_keeps_old_frozen_result(service):
    subject, calls, database = service
    old, identity, _ = subject.read()
    save_account_character_weights(database, 1051, {"CritBase": 2.25},
                                   static_database_path=subject.dependencies.static_database_path)
    new, revised, rebuilt = subject.read(verify=True)
    assert rebuilt and revised != identity and new is not old
    assert calls == [old, new]


def test_unchanged_native_observation_timestamp_does_not_rebuild(service, monkeypatch):
    subject, calls, _database = service
    # The public native domain is included, but repeated evidence with identical
    # fields must not invalidate the whole catalog solely because of its timestamp.
    revision = ["first"]
    monkeypatch.setattr(UserDataDao, "list_native_character_profile_observations",
                        lambda _self: [{"character_id": 1051, "character_level": 80, "updated_at_utc": revision[0]}])
    subject.read()
    revision[0] = "second"
    subject.read(verify=True)
    assert len(calls) == 1


def test_world_bonus_is_relevant_but_other_setting_is_not(service):
    subject, calls, database = service
    subject.read()
    with UserDataDao(database) as dao:
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 5, "quantao_crit_damage": .02})
    assert subject.read(verify=True)[2]
    assert len(calls) == 2


def test_stats_template_and_manifest_changes_are_not_missed(service, monkeypatch):
    subject, calls, _database = service
    subject.read()
    template = subject.dependencies.config_dir / "workshop.json"
    monkeypatch.setenv("NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE", str(template))
    template.write_text(json.dumps({"schema_version": 1, "characters": {"1046": {"property_weights": {"CritBase": 2}}}}))
    assert subject.read(verify=True)[2]
    (subject.dependencies.asset_root / "manifest.json").write_text('{"characters":{}}')
    assert subject.read()[2]
    stats = subject.dependencies.config_dir / "stats.json"
    data = json.loads(stats.read_text(encoding="utf-8"))
    data["fixture_change"] = 1
    stats.write_text(json.dumps(data))
    assert subject.read()[2]
    assert len(calls) == 4


def test_unrelated_write_during_read_does_not_discard_result(service):
    subject, calls, database = service
    def read(*_args):
        unrelated_write(database, len(calls))
        calls.append(object())
        return calls[-1]
    subject.reader = read
    assert subject.read()[2]
    assert len(calls) == 1


def test_relevant_change_during_read_retries_once_then_returns_latest(service):
    subject, calls, database = service
    def read(*_args):
        calls.append(object())
        if len(calls) == 1:
            save_account_character_weights(database, 1051, {"CritBase": 2.25},
                                           static_database_path=subject.dependencies.static_database_path)
        return calls[-1]
    subject.reader = read
    assert subject.read()[0] is calls[-1] and len(calls) == 2


def test_continuous_relevant_writes_fail_boundedly_without_cache(service):
    subject, calls, database = service
    def read(*_args):
        calls.append(object())
        save_account_character_weights(database, 1051, {"CritBase": float(len(calls))},
                                       static_database_path=subject.dependencies.static_database_path)
        return calls[-1]
    subject.reader = read
    with pytest.raises(RuntimeError, match="读取期间发生变化"):
        subject.read()
    assert len(calls) == 2 and subject._cached is None


def test_custom_role_add_delete_and_shape_changes_invalidate(service):
    subject, calls, database = service
    subject.read()
    with UserDataDao(database) as dao:
        role = dao.create_custom_character("Fixture")
    assert subject.read(verify=True)[2]
    with UserDataDao(database) as dao:
        dao.save_custom_character_shape_bonus(role["character_id"], shape_label="Type-4", property_values={})
    assert subject.read(verify=True)[2]
    with UserDataDao(database) as dao:
        dao.delete_custom_character(role["character_id"])
    assert subject.read(verify=True)[2] and len(calls) == 4


def test_profile_fork_and_likeability_changes_invalidate(service):
    subject, _calls, database = service
    subject.read()
    profile = dict(character_id=1051, character_level=80, breakthrough_stage=6, awakening_level=0,
                   fork_id=None, fork_level=None, fork_breakthrough_stage=None, fork_refinement_level=None)
    with UserDataDao(database) as dao:
        dao.save_character_profile(**profile)
    assert subject.read(verify=True)[2]
    with UserDataDao(database) as dao:
        dao.save_character_profile(**profile, likeability_level_10_enabled=True)
    assert subject.read(verify=True)[2]
    with UserDataDao(database) as dao:
        dao.save_character_profile(**{**profile, "fork_id": "fixture_fork", "fork_level": 80,
                                     "fork_breakthrough_stage": 6, "fork_refinement_level": 1})
    assert subject.read(verify=True)[2]


def test_weight_change_then_restore_during_read_is_not_missed(service):
    subject, calls, database = service
    def save(value):
        save_account_character_weights(database, 1051, {"CritBase": value},
                                       static_database_path=subject.dependencies.static_database_path)
    save(1.0)
    def read(*_args):
        calls.append(object())
        if len(calls) == 1:
            save(2.0)
            save(1.0)
        return calls[-1]
    subject.reader = read
    assert subject.read()[0] is calls[-1] and len(calls) == 2
