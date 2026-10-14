# 验证养成工作草稿保存绑定、迟到请求撤销及删除后的主动重建。
from __future__ import annotations

from dataclasses import replace

import pytest

from src.domain.progression.history import HistoryContextExpired
from src.services.progression.history_service import CultivationHistoryService
from tests.test_cultivation_history import history_payload


NTE_TEST_TIER = "core"


def _service(tmp_path):
    from src.storage.sqlite.user_data_dao import UserDataDao

    path = tmp_path / "user.sqlite3"
    with UserDataDao(path, account_id="one"):
        pass
    identity = ["one", 1, "dataset"]
    service = CultivationHistoryService(
        account_id="one", user_database_path=path, context_identity=lambda: tuple(identity),
    )
    return service, identity


def _freeze(service, session, payload, *, explicit=True):
    import json

    return service.freeze(session, json.loads(payload.configuration_json), explicit_calculation=explicit)


def test_deleted_binding_rejects_late_explicit_and_automatic_requests(tmp_path):
    service, _identity = _service(tmp_path)
    session = service.new_session("single")
    payload = history_payload()
    first = service.save(_freeze(service, session, payload), payload)
    old_explicit = _freeze(service, session, payload)
    selection = service.select_all()
    service.delete(selection)
    with pytest.raises(HistoryContextExpired):
        service.save(old_explicit, payload)
    automatic = _freeze(service, session, payload, explicit=False)
    with pytest.raises(HistoryContextExpired):
        service.save(automatic, payload)
    assert service.list().total == 0
    second = service.save(_freeze(service, session, payload), payload)
    assert second.history_id != first.history_id
    assert second.revision == 1


def test_latest_request_only_and_clone_never_overwrites_original(tmp_path):
    service, _identity = _service(tmp_path)
    session = service.new_session("single")
    original = history_payload()
    first = service.save(_freeze(service, session, original), original)
    outdated = _freeze(service, session, original)
    service.invalidate(session)
    with pytest.raises(HistoryContextExpired):
        service.save(outdated, original)
    changed = history_payload(quantity=30)
    latest = service.save(_freeze(service, session, changed), changed)
    assert latest.history_id == first.history_id
    clone = service.new_session("single")
    cloned = service.save(_freeze(service, clone, changed), changed)
    assert cloned.history_id != first.history_id
    assert service.list().total == 2


def test_account_switch_and_closed_session_revoke_operations(tmp_path):
    service, identity = _service(tmp_path)
    session = service.new_session("single")
    payload = history_payload()
    envelope = _freeze(service, session, payload)
    identity[1] = 2
    with pytest.raises(HistoryContextExpired):
        service.save(envelope, payload)
    with pytest.raises(HistoryContextExpired):
        service.list()
    identity[1] = 1
    service.close()
    with pytest.raises(HistoryContextExpired):
        service.save(envelope, payload)


def test_envelope_cannot_be_reused_for_other_configuration(tmp_path):
    service, _identity = _service(tmp_path)
    session = service.new_session("single")
    envelope = _freeze(service, session, history_payload())
    with pytest.raises(ValueError):
        service.save(envelope, history_payload(quantity=30))
    with pytest.raises(HistoryContextExpired):
        service.save(replace(envelope, account_id="two"), history_payload())


def test_restart_preserves_history_but_starts_new_work_binding(tmp_path):
    service, identity = _service(tmp_path)
    payload = history_payload(quantity=30)
    first_session = service.new_session("single")
    first = service.save(_freeze(service, first_session, payload), payload)
    service.close()
    restarted = CultivationHistoryService(
        account_id="one", user_database_path=tmp_path / "user.sqlite3", context_identity=lambda: tuple(identity),
    )
    assert restarted.get(first.history_id).payload == payload
    new_session = restarted.new_session("single")
    second = restarted.save(_freeze(restarted, new_session, payload), payload)
    assert second.history_id != first.history_id
    assert restarted.list().total == 2
    restarted.close()
