# 覆盖智能推荐的冻结输入、保留状态、配装来源与弹窗取消边界。
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


from src.domain.inventory.post_actions import merge_post_action_config
from src.services.inventory.recommendation.warehouse_recommendation_preview import RecommendationGuard, preview_targets
from src.storage.sqlite.inventory.warehouse_recommendation_inputs import recommendation_account_identity


def test_targets_respect_original_locks_equipped_and_review():
    rows = []
    for uid, action, locked, equipped, discarded in [
        ("locked", "discard", True, False, False),
        ("equipped", "discard", False, True, False),
        ("review", "review", False, False, True),
        ("keep", "keep", False, False, True),
        ("newlock", "lock", False, False, False),
        ("discard", "discard", False, False, False),
    ]:
        rows.append(
            dict(
                uid=uid,
                decision=action,
                original_locked=locked,
                original_equipped=equipped,
                original_discarded=discarded,
            )
        )
    result = {"rows": rows, "violations": []}
    assert preview_targets(result) == {"keep": "normal", "newlock": "locked", "discard": "discarded"}
    result["violations"] = ["replacement_lost"]
    with pytest.raises(ValueError):
        preview_targets(result)


def test_config_roundtrip_preserves_recommendation_and_does_not_enable_actions():
    options = {"characters": [1003], "sources": {"1003": "12"}, "grade": "SS"}
    value = merge_post_action_config({"recommendation": options})
    assert merge_post_action_config(value)["recommendation"] == options
    assert not value["discard"]["enabled"] and not value["lock"]["enabled"]


def test_account_fingerprint_ignores_battle_but_detects_weights(tmp_path):
    path = tmp_path / "account.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE character_profile(id, weight); INSERT INTO character_profile VALUES(1,2);"
            "CREATE TABLE battle_report(id);"
        )
    first = recommendation_account_identity(path)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO battle_report VALUES(1)")
    assert recommendation_account_identity(path) == first
    with sqlite3.connect(path) as db:
        db.execute("UPDATE character_profile SET weight=3")
    assert recommendation_account_identity(path) != first






def test_guard_does_not_rebase_when_inputs_changed(monkeypatch):
    import src.services.inventory.recommendation.warehouse_recommendation_preview as module

    monkeypatch.setattr(module, "recommendation_account_identity", lambda _: "before")
    monkeypatch.setattr(module, "file_identity", lambda _: ())
    monkeypatch.setattr(module, "post_config", lambda _: {"saved": True})
    def matching_service(_, *, scope):
        from src.domain.equipment.eligibility.management_settings import MatchingScope
        assert scope == MatchingScope.WAREHOUSE
        return SimpleNamespace(load=lambda: {})
    monkeypatch.setattr(module, "MatchingSettingsService", matching_service)
    guard = RecommendationGuard(Path("unused"), 1, "before", (), (), {}, {"saved": True}, lambda: None)
    guard.require_current(check_snapshot=False)
    monkeypatch.setattr(module, "recommendation_account_identity", lambda _: "after")
    with pytest.raises(ValueError):
        guard.require_current(check_snapshot=False)
    assert guard.account_identity == "before"


def test_guard_failure_stops_dispatch_before_writer_receives_targets():
    from src.services.inventory.management.warehouse_state_management import (
        WarehouseStateManagementPlan,
        WarehouseStateManagementService,
        WarehouseStateManagementError,
    )
    from unittest.mock import Mock

    writer = Mock()
    guard = Mock()
    guard.require_current.side_effect = ValueError("changed")
    service = WarehouseStateManagementService("unused", None, state_writer_factory=lambda _: writer, input_guard=guard)
    with pytest.raises(WarehouseStateManagementError, match="changed"):
        service.apply(WarehouseStateManagementPlan(1, (), {}))
    writer.apply_many.assert_not_called()


def test_missing_required_restore_is_blocked_before_external_dispatch():
    from unittest.mock import Mock
    from src.services.inventory.management.warehouse_state_management import (
        WarehouseStateManagementPlan,
        WarehouseStateManagementService,
        WarehouseStateManagementError,
    )
    guard=RecommendationGuard(Path('unused'),1,'identity',(),(),{},{},lambda:None,
                              frozenset({'needed'}),frozenset({'needed'}))
    guard.require_current=lambda **_:None
    writer=Mock()
    service=WarehouseStateManagementService('unused',None,state_writer_factory=lambda _:writer,input_guard=guard)
    plan=WarehouseStateManagementPlan(1,({'uid':'extra','target_state':'discarded','equipment':{'slot':1,'serial':2}},),{})
    with pytest.raises(WarehouseStateManagementError,match='未保留'):
        service.apply(plan)
    writer.apply_many.assert_not_called()


@pytest.mark.parametrize(
    "decision,actions,expected",
    [
        ("keep", ["discard"], "discard"),
        ("discard", ["keep"], "keep"),
        ("discard", ["discard", "keep"], "discard"),
        ("keep", ["lock", "discard"], "lock"),
        ("lock", ["keep", "discard", "lock"], "keep"),
        ("lock", ["discard", "lock"], "discard"),
    ],
)
def test_rules_apply_first_matching_action(decision, actions, expected):
    from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
    rules = [{'action': action} for action in actions]
    result = {"rows": [{'uid': 'test', "decision": decision, "reasons": []}]}
    apply_ordered_rules(result, [SimpleNamespace(uid='test')], rules, matched_by_uid={'test': rules})
    assert result["rows"][0]["decision"] == expected


def test_dynamic_and_manual_rules_share_order_but_safety_gate_still_applies():
    from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
    from copy import deepcopy
    manual = {'name': '人工保留', 'enabled': True, 'action': 'keep'}
    dynamic = {'name': '当前用途', 'enabled': True, 'smart_kind': 'current_drive', 'action': 'lock'}
    discard = {'name': '人工弃置', 'enabled': True, 'action': 'discard'}
    base = {'rows': [{'uid': 'test', 'decision': 'lock', 'reasons': ['当前用途储备：角色／形状'],
                     'dynamic_rule_matches': {'current_drive': 'lock'}, 'future_guards': ['后面的组合规则']}]}
    for rules, expected in [([manual, dynamic], 'keep'), ([dynamic, manual], 'lock'), ([discard, dynamic], 'discard')]:
        result = deepcopy(base)
        apply_ordered_rules(result, [SimpleNamespace(uid='test')], rules, matched_by_uid={'test': [manual, discard]})
        assert result['rows'][0]['decision'] == expected
        assert result['rows'][0]['winning_rule']['position'] == 1
    result = deepcopy(base)
    apply_ordered_rules(result, [SimpleNamespace(uid='test')], [discard, dynamic],
                        matched_by_uid={'test': [discard]}, protected_uids=frozenset({'test'}))
    assert result['rows'][0]['decision'] == 'review'
