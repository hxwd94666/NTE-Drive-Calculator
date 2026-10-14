# 验证养成历史的完整输入、原子保存、账号隔离和确切集合删除。
from __future__ import annotations

import json
import sqlite3

import pytest

from src.domain.progression.history import HistoryConflict, HistoryPayload
from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"


def history_payload(*, quantity: int = 0, manual: bool = True) -> HistoryPayload:
    configuration = {
        "version": 1, "mode": "single", "hunter_level": 60,
        "identification_level": 7, "material_scope": "stamina",
        "targets": [{
            "line_id": "line-1", "character_id": 101, "name": "示例角色",
            "current_level": 60, "current_stage": 4,
            "target_level": 80, "target_stage": 6,
            "character_enabled": True, "skills_enabled": False,
            "skills": [{"skill_id": "skill_a", "current_level": 2, "target_level": 8}],
            "fork": {"fork_id": "fork-a", "name": "示例弧盘", "enabled": False,
                     "current_level": 20, "current_stage": 0,
                     "target_level": 80, "target_stage": 6},
        }],
        "owned_materials": [{"item_id": "material-a", "quantity": quantity,
                             "manual_override": manual, "source": "manual",
                             "observed_at_utc": None}],
    }
    result = {
        "version": 1,
        "dataset": {"dataset_id": "fixture", "schema_version": 39,
                    "importer_version": "1", "built_at_utc": "2026-01-01T00:00:00Z"},
        "algorithm_version": "cultivation-history-v1",
        "materials": [{"item_id": "material-a", "name": "示例材料", "required": 100,
                       "allocated_equivalent": min(100, quantity),
                       "remaining": max(0, 100 - quantity), "stamina_eligible": True}],
        "target_summaries": [{"line_id": "line-1", "character_id": 101,
                              "status": "complete", "known_stamina": 0,
                              "total_stamina": None}],
        "stamina": {"status": "unavailable", "known_stamina": 0,
                    "total_stamina": None, "runs": []},
        "gaps": [{"line_id": None, "reason_code": "yield_missing", "item_id": "material-a"}],
    }
    return HistoryPayload.create(configuration, result)


def test_complete_draft_and_explicit_zero_round_trip():
    payload = history_payload()
    restored = HistoryPayload.decode(payload.configuration_json, payload.result_snapshot_json)
    assert restored == payload
    draft = json.loads(restored.configuration_json)
    assert draft["targets"][0]["fork"]["enabled"] is False
    assert draft["targets"][0]["skills"][0]["target_level"] == 8
    assert draft["owned_materials"][0]["manual_override"] is True
    assert draft["owned_materials"][0]["quantity"] == 0
    assert json.loads(payload.result_snapshot_json)["stamina"]["total_stamina"] is None


def test_summary_includes_targets_and_corruption_does_not_prevent_deletion(tmp_path):
    payload = history_payload()
    summary = json.loads(payload.summary_json)
    assert "60/4→80/6" in summary["characters"][0]["target_label"]
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="one") as dao:
        dao.save_cultivation_history("one", "entry", payload)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE cultivation_history SET summary_json = '{}' WHERE history_id = 'entry'")
    with UserDataDao(database) as dao:
        with pytest.raises(ValueError, match="校验"):
            dao.get_cultivation_history("one", "entry")
        assert dao.delete_cultivation_histories("one", dao.select_cultivation_histories("one")) == 1


@pytest.mark.parametrize("field,value", [
    ("known_stamina", float("inf")), ("known_stamina", True),
    ("gap_count", -1), ("characters", [None]), ("characters", []),
    ("total_stamina", 0), ("version", 999), ("status", "unknown"),
])
def test_malformed_summary_is_displayable_and_remains_deletable(tmp_path, field, value):
    from src.features.toolbox.cultivation.history_display import summary_text

    payload = history_payload()
    summary = json.loads(payload.summary_json)
    summary[field] = value
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="one") as dao:
        dao.save_cultivation_history("one", "entry", payload)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE cultivation_history SET summary_json = ? WHERE history_id = 'entry'",
                           (json.dumps(summary),))
    with UserDataDao(database) as dao:
        saved = dao.list_cultivation_histories("one").items[0]
        assert summary_text(saved) == ("记录摘要异常，仍可勾选删除", "请查看详情或删除")
        assert dao.delete_cultivation_histories("one", dao.select_cultivation_histories("one")) == 1


def test_summary_rejects_duplicate_keys_and_preserves_complete_zero():
    from src.domain.progression.history import decode_summary

    value = json.loads(history_payload().summary_json)
    value.update(status="complete", known_stamina=0, total_stamina=0, gap_count=0)
    assert decode_summary(json.dumps(value))["total_stamina"] == 0
    duplicate = json.dumps(value)[:-1] + ', "known_stamina": 100}'
    with pytest.raises(ValueError, match="重复"):
        decode_summary(duplicate)


def test_historical_view_does_not_apply_current_identification_mapping(monkeypatch):
    from src.domain.progression.history import freeze_configuration

    payload = history_payload()

    def changed_mapping(*_args, **_kwargs):
        raise ValueError("current mapping changed")

    monkeypatch.setattr("src.domain.progression.history.project_identification_level", changed_mapping)
    assert HistoryPayload.decode(payload.configuration_json, payload.result_snapshot_json) == payload
    with pytest.raises(ValueError, match="mapping changed"):
        freeze_configuration(json.loads(payload.configuration_json))


def test_whitelist_duplicate_ids_and_boolean_quantities_rejected():
    payload = history_payload()
    config = json.loads(payload.configuration_json)
    result = json.loads(payload.result_snapshot_json)
    config["owned_materials"][0]["quantity"] = True
    with pytest.raises(ValueError):
        HistoryPayload.create(config, result)
    config = json.loads(payload.configuration_json)
    config["targets"].append(config["targets"][0])
    with pytest.raises(ValueError):
        HistoryPayload.create(config, result)
    config = json.loads(payload.configuration_json)
    config["database_path"] = "private"
    with pytest.raises(ValueError):
        HistoryPayload.create(config, result)


def test_updates_keep_one_row_and_deleted_update_never_inserts(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        first = dao.save_cultivation_history("one", "history-1", history_payload())
        assert first.revision == 1
        changed = dao.save_cultivation_history(
            "one", "history-1", history_payload(quantity=30), expected_revision=1,
        )
        assert changed.revision == 2
        assert changed.first_calculated_at_utc == first.first_calculated_at_utc
        unchanged = dao.save_cultivation_history(
            "one", "history-1", history_payload(quantity=30), expected_revision=2,
        )
        assert unchanged == changed
        selected = dao.select_cultivation_histories("one")
        assert len(selected) == 1
        assert dao.delete_cultivation_histories("one", selected) == 1
        with pytest.raises(HistoryConflict):
            dao.save_cultivation_history(
                "one", "history-1", history_payload(quantity=40), expected_revision=2,
            )
        assert dao.list_cultivation_histories("one").total == 0


def test_delete_revision_conflict_rolls_back_all_rows(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
        dao.save_cultivation_history("one", "b", history_payload())
        selection = dao.select_cultivation_histories("one")
        dao.save_cultivation_history("one", "b", history_payload(quantity=1), expected_revision=1)
        with pytest.raises(HistoryConflict):
            dao.delete_cultivation_histories("one", selection)
        assert dao.list_cultivation_histories("one").total == 2


def test_all_selection_is_frozen_and_missing_row_is_idempotent(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
        selection = dao.select_cultivation_histories("one")
        dao.save_cultivation_history("one", "new", history_payload())
        assert dao.delete_cultivation_histories("one", selection) == 1
        assert dao.delete_cultivation_histories("one", selection) == 0
        assert dao.list_cultivation_histories("one").items[0].history_id == "new"


def test_account_and_precommit_guards(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        with pytest.raises(ValueError):
            dao.save_cultivation_history("two", "a", history_payload())
        checks = 0

        def guard():
            nonlocal checks
            checks += 1
            if checks == 3:
                raise RuntimeError("stale generation")

        with pytest.raises(RuntimeError):
            dao.save_cultivation_history("one", "a", history_payload(), check=guard)
        assert dao.list_cultivation_histories("one").total == 0


def test_corrupt_detail_remains_deletable(tmp_path):
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="one") as dao:
        dao.save_cultivation_history("one", "a", history_payload())
        dao._db().execute("UPDATE cultivation_history SET configuration_json = '{}' WHERE history_id = 'a'")
        dao._db().commit()
        with pytest.raises(ValueError):
            dao.get_cultivation_history("one", "a")
        assert dao.delete_cultivation_histories("one", dao.select_cultivation_histories("one")) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_second_delete_chunk_conflict_rolls_back_first_chunk(tmp_path):
    with UserDataDao(tmp_path / "user.sqlite3", account_id="one") as dao:
        dao.save_cultivation_history("one", "base", history_payload())
        dao._db().executemany(
            "INSERT INTO cultivation_history SELECT ?, mode, revision, first_calculated_at_utc, "
            "last_calculated_at_utc, payload_version, configuration_json, result_snapshot_json, "
            "summary_json, search_text, content_sha256 FROM cultivation_history WHERE history_id = 'base'",
            [(f"history-{index:04d}",) for index in range(401)],
        )
        dao._db().commit()
        selection = dao.select_cultivation_histories("one")
        last = selection[-1]
        dao._db().execute("UPDATE cultivation_history SET revision = 2 WHERE history_id = ?", (last.history_id,))
        dao._db().commit()
        with pytest.raises(HistoryConflict):
            dao.delete_cultivation_histories("one", selection)
        assert dao.list_cultivation_histories("one").total == 402


def test_disabled_module_keeps_lower_editing_target():
    payload = history_payload()
    config = json.loads(payload.configuration_json)
    result = json.loads(payload.result_snapshot_json)
    config["targets"][0]["fork"]["current_level"] = 80
    config["targets"][0]["fork"]["current_stage"] = 6
    config["targets"][0]["fork"]["target_level"] = 20
    config["targets"][0]["fork"]["target_stage"] = 0
    config["targets"][0]["skills"][0]["current_level"] = 10
    config["targets"][0]["skills"][0]["target_level"] = 1
    restored = json.loads(HistoryPayload.create(config, result).configuration_json)
    assert restored["targets"][0]["fork"]["target_level"] == 20
    assert restored["targets"][0]["skills"][0]["target_level"] == 1
