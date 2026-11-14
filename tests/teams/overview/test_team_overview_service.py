# 验证简报只冻结真实配装来源，旧个人毕业计算退役后仍保留缺口、冲突、取消与账号隔离。
from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import replace
from threading import Event

import pytest

from src.domain.teams.models import TeamDraft
from src.domain.teams.overview import SourceMode
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case


def test_four_member_sources_retain_actual_equipment_and_stable_references(tmp_path):
    service, team, _ = overview_case(tmp_path)
    result, = service.load()
    assert result.ready_count == 4 and result.team == team
    for member in result.members:
        assert member.plan_id and member.slot_id and member.snapshot_id and member.fingerprint
        assert member.items and not member.issues
        assert all(item['level'] == 0 for item in member.items)
        core, = (item for item in member.items if item['kind'] == 'core')
        assert any(stat.get('property_id') == 'AtkUp' and stat['value'] == .05 for stat in core['main_stats'])


def test_sources_only_hydrate_selected_plan_uids_and_keep_publish_recheck(tmp_path, monkeypatch):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        plan = dao.get_loadout_slot(slots[1072])["current_plan"]
        unused = dao.create_loadout_slot(1072, "未选备用")
        ignored_plan = dao.save_loadout_plan(name="不参与", character_id=1072, slot_id=unused,
            assignments=plan["assignments"], source_snapshot_id=plan["source_snapshot_id"],
            status="ready", payload=plan["payload"])
    actual_items, actual_plans = UserDataDao.list_inventory_items, UserDataDao.get_loadout_plans
    reads, hydrated = [], []
    def items(dao, snapshot_id, **kwargs):
        assert kwargs.get("uids") is not None, "队伍来源不应水合整包库存"
        reads.append((snapshot_id, set(kwargs["uids"])))
        return actual_items(dao, snapshot_id, **kwargs)
    def plans(dao, identifiers):
        identifiers = tuple(identifiers)
        hydrated.extend(identifiers)
        return actual_plans(dao, identifiers)
    monkeypatch.setattr(UserDataDao, "list_inventory_items", items)
    monkeypatch.setattr(UserDataDao, "get_loadout_plans", plans)
    result, = service.load((team,))
    assert result.ready_count == 4 and ignored_plan not in hydrated
    assert len(reads) == 2 and reads[0] == reads[1]
    assert len(reads[0][1]) == sum(len(member.items) for member in result.members)


def test_missing_member_preserves_source_gap_instead_of_borrowing_another_plan(tmp_path):
    service, _, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        plan = dao.get_loadout_slot(slots[1072])["current_plan_id"]
        dao.deactivate_loadout_plan(plan)
    result, = service.load()
    assert result.ready_count == 3
    assert result.members[0].issues and not result.members[0].items


def test_explicit_secondary_slot_and_own_snapshot_do_not_follow_new_inventory(tmp_path):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        previous = dao.get_loadout_slot(slots[1072])["current_plan"]
        secondary = dao.create_loadout_slot(1072, "备用")
        dao.save_loadout_plan(name="旧快照方案", character_id=1072, assignments=previous["assignments"],
            source_snapshot_id=previous["source_snapshot_id"], status="ready", payload=previous["payload"], slot_id=secondary)
        bindings = tuple((key, secondary if key == 1072 else value) for key, value in slots.items())
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.CALC, bindings=bindings)
    before, = service.load()
    with UserDataDao(service.database_path) as dao:
        _, items = dao.export_inventory_snapshot(previous["source_snapshot_id"])
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]} for row in items]
        for item in native:
            item["sub_stats"] = [{"property_id": "CritBase", "value": .5, "percent": True}]
        dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 2, "sequence": 2, "observed_at_unix_ms": 1787000001000,
            "item_count": len(native), "items": native}})
    after, = service.load()
    assert after.members == before.members and after.members[0].slot_id == secondary


def test_uid_conflict_remains_a_source_error(tmp_path):
    service, _, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        original = dao.get_loadout_slot(slots[1072])["current_plan"]
        dao.save_loadout_plan(name="冲突来源", character_id=1003, assignments=original["assignments"],
            source_snapshot_id=original["source_snapshot_id"], status="ready", payload=original["payload"], slot_id=slots[1003])
    result, = service.load()
    assert "队伍存在装备冲突" in result.issues
    assert result.ready_count == 2


def test_game_source_is_read_only_and_all_four_share_one_snapshot(tmp_path):
    service, team, _ = overview_case(tmp_path)
    config, _ = service.source_choices(team)
    service.save_source(team, config, mode=SourceMode.GAME)
    with UserDataDao(service.database_path) as dao:
        before = tuple(dao._db().iterdump())
    result, = service.load()
    assert result.ready_count == 4
    assert len({member.snapshot_id for member in result.members}) == 1
    with UserDataDao(service.database_path) as dao:
        assert tuple(dao._db().iterdump()) == before


def test_sources_publish_incrementally_and_allow_unrelated_writes(tmp_path):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        second = dao.save_team(TeamDraft("同阵容备用", team.character_ids))
        dao.save_team_source(second, dao.get_team_source(second.team_id),
                             mode=SourceMode.CALC, bindings=tuple(slots.items()))
    emitted = []
    def publish(value):
        emitted.append(value)
        with UserDataDao(service.database_path) as dao:
            dao.replace_application_setting_copy("unrelated", {"value": len(emitted)})
    results = service.load((team, second), publish=publish)
    assert tuple(emitted) == results and len(results) == 2
    assert results[0].members == results[1].members


def test_cancel_after_first_team_stops_remaining_publication(tmp_path):
    service, team, slots = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        second = dao.save_team(TeamDraft("同阵容备用", team.character_ids))
        dao.save_team_source(second, dao.get_team_source(second.team_id),
                             mode=SourceMode.CALC, bindings=tuple(slots.items()))
    token, emitted = Event(), []
    def publish(value):
        emitted.append(value)
        token.set()
    with pytest.raises(CancelledError):
        service.load((team, second), cancelled=token.is_set, publish=publish)
    assert len(emitted) == 1 and emitted[0].team == team


def test_concurrent_source_change_and_expired_account_publish_nothing(tmp_path, monkeypatch):
    service, team, _ = overview_case(tmp_path)
    config, _ = service.source_choices(team)
    database, changed = service._database, []
    @contextmanager
    def interleaved(**kwargs):
        with database(**kwargs) as dao:
            yield dao
        # 写入发生在首次一致读结束后，最终发布复核必须识别新来源。
        if not kwargs.get("write") and not changed:
            changed.append(True)
            service.save_source(team, config, mode=SourceMode.GAME)
    monkeypatch.setattr(service, "_database", interleaved)
    emitted = []
    with pytest.raises(ValueError, match="配装来源"):
        service.load(publish=emitted.append)
    assert not emitted
    service._current = lambda: False
    with pytest.raises(CancelledError):
        service.source_choices(replace(team, name="旧账号"))


def test_sources_do_not_read_personal_graduation_templates(tmp_path, monkeypatch):
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    service, _, _ = overview_case(tmp_path)
    def forbidden(*_args, **_kwargs):
        pytest.fail("来源简报不应读取角色页个人毕业模板")
    monkeypatch.setattr(StaticGameDataDao, "get_character_graduation_template", forbidden)
    result, = service.load()
    assert result.ready_count == 4


def test_visual_current_snapshot_does_not_gain_native_game_ownership(tmp_path):
    service, team, _ = overview_case(tmp_path)
    with UserDataDao(service.database_path) as dao:
        _, raw = dao.export_inventory_snapshot(dao.current_inventory_snapshot_id())
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]} for row in raw]
        dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 2, "sequence": 2, "observed_at_unix_ms": 1787000001000,
            "item_count": len(native), "items": native}}, source="vision")
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
    result, = service.load()
    assert result.ready_count == 0
    assert all("可靠" in member.issues[0] for member in result.members)


def test_missing_static_character_retains_source_evidence_for_later_model_diagnosis(tmp_path):
    service, team, slots = overview_case(tmp_path)
    missing_id = 9999999
    with UserDataDao(service.database_path) as dao:
        donor = dao.get_loadout_slot(slots[1072])["current_plan"]
        slot = dao.create_loadout_slot(missing_id, "目录暂缺角色")
        dao.save_loadout_plan(name="保留来源", character_id=missing_id, assignments=donor["assignments"],
            source_snapshot_id=donor["source_snapshot_id"], status="ready", payload=donor["payload"], slot_id=slot)
        changed = dao.save_team(TeamDraft("暂缺模型", (missing_id, *team.character_ids[1:])), existing=team)
        dao.save_team_source(changed, dao.get_team_source(changed.team_id), mode=SourceMode.CALC,
                             bindings=((missing_id, slot), *((key, slots[key]) for key in team.character_ids[1:])))
    result, = service.load((changed,))
    assert result.ready_count == 4
    assert result.members[0].items and result.members[0].character_id == missing_id


def test_colliding_team_and_slot_ids_in_other_account_never_reuse_first_account_values(tmp_path):
    from src.services.teams.overview import TeamOverviewService
    service, team, slots = overview_case(tmp_path)
    assert service.load()[0].ready_count == 4
    other = tmp_path / "other.sqlite3"
    with UserDataDao(other, account_id="other") as dao:
        with dao.write_transaction() as db:
            db.execute("INSERT INTO team VALUES(?,?,1,'2026-10-22','2026-10-22')", (team.team_id, team.name))
            db.executemany("INSERT INTO team_member VALUES(?,?,?)", [(team.team_id, i, key) for i, key in enumerate(team.character_ids)])
            db.execute("INSERT INTO team_overview_source VALUES(?,'slots',1)", (team.team_id,))
        new_slots = [dao.create_loadout_slot(key, "同 ID 空槽") for key in team.character_ids]
        assert set(new_slots) == set(slots.values())
    second = TeamOverviewService(account_id="other", generation=2, database_path=other,
        static_database_path=service.static_database_path, context_is_current=lambda: True)
    result, = second.load()
    assert result.ready_count == 0 and all(member.issues for member in result.members)
    service.database_path = other
    with pytest.raises(CancelledError, match="账号身份"):
        service.load()
