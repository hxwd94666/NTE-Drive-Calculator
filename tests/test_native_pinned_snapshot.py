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
    with patch("src.integrations.native_battle_snapshot.read_native_projection", side_effect=lambda *a, **kw: deepcopy(kw["header"])):
        snapshot = read_first_hit_snapshot(core, lambda: None, refs)
    assert snapshot["retention"] == "dll_pinned"
    assert set(snapshot["domains"]) == set(refs)
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
