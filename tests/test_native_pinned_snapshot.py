# 验证首击固定快照按原身份读取，跨半场不刷新、不借用后来的数据。
from copy import deepcopy
from unittest.mock import patch

import pytest

from src.integrations.native_battle_snapshot import read_first_hit_snapshot
from src.integrations.nte_core_protocol import NteCoreProtocolError
from tests.test_native_battle_snapshot import Core


def test_pinned_read_never_refreshes_live_domain_or_checks_live_revision():
    core = Core()
    refs = {domain: deepcopy(core.header(domain)) for domain in ("character", "inventory", "team", "environment")}
    equipped = {"schemaVersion": 1, "scope": "character_equipped_only", "complete": True,
                "items": [{"uid": {"slot": 8, "serial": 1}, "equipped_character_id": 1020}],
                "characters": [{"character_id": 1020}]}
    def projection(*args, **kwargs):
        assert kwargs["domain"] == "character"
        return {**deepcopy(kwargs["header"]), "battleEquipment": deepcopy(equipped)}
    with patch("src.integrations.native_battle_snapshot.read_native_projection", side_effect=projection):
        snapshot = read_first_hit_snapshot(core, lambda: None, refs)
    assert snapshot["retention"] == "dll_pinned"
    # 旧引用即使带整背包，也只读首击上下文和角色已装备子集，不附当前库存。
    assert set(snapshot["domains"]) == {"character", "team", "environment"}
    assert snapshot["character_projection"]["battleEquipment"] == equipped
    assert "inventory_projection" not in snapshot
    assert all(params["domain"] != "inventory" for _, params in core.calls)
    assert {method for method, _ in core.calls} == {"native.snapshot.open", "native.snapshot.page"}
    core.revision = "2"
    assert snapshot["domains"]["team"]["revision"] == refs["team"]["revision"]


def test_missing_first_hit_reference_stays_missing_without_using_current_team():
    core = Core()
    snapshot = read_first_hit_snapshot(core, lambda: None, {})
    assert snapshot["domains"] == {}
    assert "team_first_hit_unavailable" in snapshot["missing"]
    assert core.calls == []


def test_pinned_page_cannot_substitute_a_new_revision():
    core = Core()
    reference = deepcopy(core.header("team"))
    core.revision = "2"
    with pytest.raises(NteCoreProtocolError, match="身份不匹配"):
        read_first_hit_snapshot(core, lambda: None, {"team": reference})
