# 覆盖智能推荐的冻结输入、保留状态、配装来源与弹窗取消边界。
import os
from pathlib import Path
import sqlite3
from threading import Event
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from src.domain.post_actions import merge_post_action_config
from src.features.inventory.warehouse.recommendation_dialog import RecommendationDialog
from src.services.inventory.warehouse_recommendation_preview import RecommendationGuard, preview_targets
from src.storage.sqlite.warehouse_recommendation_inputs import recommendation_account_identity


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


def test_temporary_database_keeps_explicit_release_image_root(tmp_path, monkeypatch):
    import src.services.inventory.warehouse_recommendation_preview as module

    release, config = tmp_path / 'release', tmp_path / 'config'
    release.mkdir()
    config.mkdir()
    images = release / 'role_catalog' / 'game_ui'
    images.mkdir(parents=True)
    for path in (release / 'game_static.sqlite3', release / 'manifest.json',
                 images / 'manifest.json', config / 'stats.json'):
        path.write_text('{}', encoding='utf8')
    monkeypatch.setattr(module, 'recommendation_account_identity', lambda _: 'frozen')
    monkeypatch.setattr(module, 'MatchingSettingsService', lambda _: SimpleNamespace(load=lambda: {}))
    saved = merge_post_action_config({'preserve_rules': [], 'recommendation': {'rule_scheme_version': 3}})
    monkeypatch.setattr(module, 'post_config', lambda _: saved)
    monkeypatch.setattr(module, 'freeze_warehouse_inputs', lambda source, target, **kwargs: target.write_bytes(b'inputs'))
    monkeypatch.setattr(module.RecommendationGuard, 'require_current', lambda self: None)
    captured = []
    def trial(**kwargs):
        assert kwargs['static_database'].parent != release
        assert kwargs['static_database'].is_file()
        assert kwargs['asset_root'] == images
        captured.append(kwargs['asset_root'])
        return {'snapshot_id': 1}
    monkeypatch.setattr(module, 'run_warehouse_trial', trial)
    _, guard = module.build_preview(database=tmp_path / 'account.sqlite3',
        static_database=release / 'game_static.sqlite3', asset_root=images, config_dir=config,
        config=saved, matching={}, options={}, check_context=lambda: None,
        cancel_check=lambda: False)
    assert captured and captured[0].is_dir()
    assert images / 'manifest.json' in guard.files


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


def test_warehouse_flow_injects_shared_images_independently_of_scoring_database(tmp_path, monkeypatch):
    import src.features.inventory.warehouse.recommendation_controller as module

    captured = []
    def dialog(owner, *, build, options):
        captured.append(build.keywords)
        return SimpleNamespace(is_running=lambda: False, reject=lambda: None,
                               exec=lambda: QDialog.Rejected, deleteLater=lambda: None)
    monkeypatch.setattr(module, 'RecommendationDialog', dialog)
    context = SimpleNamespace(account=SimpleNamespace(active_account_id='test', user_database_path=tmp_path / 'user.sqlite3'),
        generation=1, paths=SimpleNamespace(equipment_allocation_database_path=tmp_path / 'data/game_static.sqlite3',
            equipment_allocation_asset_root=tmp_path / 'data/role_catalog/game_ui', config_dir=tmp_path / 'config'),
        register_account_lifecycle=lambda _: lambda: None)
    module.WarehouseRecommendationFlow(context).open(None, {}, {}, mode='preview')
    assert captured[0]['static_database'] == context.paths.equipment_allocation_database_path
    assert captured[0]['asset_root'] == context.paths.equipment_allocation_asset_root
    assert captured[0]['asset_root'] != captured[0]['static_database'].parent / 'game_ui'




def test_guard_does_not_rebase_when_inputs_changed(monkeypatch):
    import src.services.inventory.warehouse_recommendation_preview as module

    monkeypatch.setattr(module, "recommendation_account_identity", lambda _: "before")
    monkeypatch.setattr(module, "file_identity", lambda _: ())
    monkeypatch.setattr(module, "post_config", lambda _: {"saved": True})
    monkeypatch.setattr(module, "MatchingSettingsService", lambda _: SimpleNamespace(load=lambda: {}))
    guard = RecommendationGuard(Path("unused"), 1, "before", (), (), {}, {"saved": True}, lambda: None)
    guard.require_current(check_snapshot=False)
    monkeypatch.setattr(module, "recommendation_account_identity", lambda _: "after")
    with pytest.raises(ValueError):
        guard.require_current(check_snapshot=False)
    assert guard.account_identity == "before"


def test_guard_failure_stops_dispatch_before_writer_receives_targets():
    from src.services.inventory.warehouse_state_management import (
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
    from src.services.inventory.warehouse_state_management import (
        WarehouseStateManagementPlan,WarehouseStateManagementService,WarehouseStateManagementError,
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
    from src.domain.equipment.warehouse_rule_order import apply_ordered_rules
    rules = [{'action': action} for action in actions]
    result = {"rows": [{'uid': 'test', "decision": decision, "reasons": []}]}
    apply_ordered_rules(result, [SimpleNamespace(uid='test')], rules, matched_by_uid={'test': rules})
    assert result["rows"][0]["decision"] == expected


def test_dynamic_and_manual_rules_share_order_but_safety_gate_still_applies():
    from src.domain.equipment.warehouse_rule_order import apply_ordered_rules
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




@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


def fake_build(**kwargs):
    guard = SimpleNamespace(require_current=lambda: None, require_targets=lambda _:None)
    base = {
            "role_options": [
                {
                    "character_id": 1,
                    "name": "角色甲",
                    "choices": [
                        {"source": "game", "label": "游戏配装", "score": 200.0},
                        {"source": "8", "label": "保存方案", "score": 300.0},
                    ],
                }
            ],
            "equipped_scores": [{"character_id": 1, "score": 200}],
        }
    return {
        **base,
        "violations": [],
        "rows": [
            dict(
                uid="item",
                decision="discard",
                original_locked=False,
                original_equipped=False,
                original_discarded=False,
                kind="drive",
                shape_id="H_3",
                set_name="",
                main_stat="",
                reasons=["已有足够副本"],
                stats={"攻击力%": 8},
            )
        ],
    }, guard




def test_close_cancels_worker_before_window_retires(app):
    started = Event()

    def build(**kwargs):
        started.set()
        while not kwargs["cancel_check"]():
            time.sleep(0.002)
        raise ValueError("cancelled")

    dialog = RecommendationDialog(None, build=build, options={"characters": [1]})
    wait_for(app, started.is_set)
    dialog.reject()
    wait_for(app, lambda: not dialog.is_running())
    assert dialog._closing and dialog.guard is None
    dialog.deleteLater()
    app.processEvents()


def test_failed_automatic_calculation_retries_calculation_without_prepare_step(app):
    calls = []
    def build(**kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise ValueError('暂时不可用')
        return fake_build(**kwargs)
    dialog = RecommendationDialog(None, build=build, options={'characters': [1]})
    try:
        wait_for(app, lambda: not dialog.is_running())
        assert calls == [True] and dialog.guard is None
        dialog.generate.click()
        wait_for(app, lambda: not dialog.is_running())
        assert calls == [True, True] and dialog.guard is not None
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_calculation_shows_compact_progress_then_results_without_role_page(app):
    release = Event()
    def build(**kwargs):
        while not release.is_set() and not kwargs['cancel_check']():
            time.sleep(0.002)
        return fake_build(**kwargs)
    dialog = RecommendationDialog(None, build=build, options={'characters': [1]})
    try:
        dialog.show()
        app.processEvents()
        assert dialog.tabs.isHidden()
        assert dialog.progress.isVisible() and dialog.width() <= 520
        release.set()
        wait_for(app, lambda: not dialog.is_running())
        assert dialog.progress.isHidden() and dialog.tabs.isVisible()
        assert dialog.tabs.count() == 1 and dialog.tabs.currentWidget() is dialog.review
        assert dialog.adopt.isVisible() and dialog.adopt.isEnabled()
    finally:
        release.set()
        dialog.close()
        wait_for(app, lambda: not dialog.is_running())
        dialog.deleteLater()
        app.processEvents()




def test_incomplete_selection_can_be_corrected_without_discarding_preview(app):
    allowed=[False]
    guard=SimpleNamespace(require_current=lambda:None)
    def require_targets(_):
        if not allowed[0]:raise ValueError('请采用所需恢复建议')
    guard.require_targets=require_targets
    def build(**kwargs):
        result,_=fake_build(**kwargs)
        return result,guard
    dialog=RecommendationDialog(None,build=build,options={'characters':[1]})
    try:
        wait_for(app,lambda:not dialog.is_running())
        dialog.adopt.click()
        assert dialog.guard is guard and dialog.recommendation_result is not None
        assert dialog.adopt.isEnabled() and '所需恢复' in dialog.status.text()
        allowed[0]=True
        dialog.adopt.click()
        assert dialog.result()==QDialog.Accepted
    finally:
        dialog.close();dialog.deleteLater();app.processEvents()
