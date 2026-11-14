# 验证已保存配装偏好的最新版本复核、身份映射、降分门禁与取消，不把档案存在当成约束失败。
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from src.services.inventory.recommendation import warehouse_recommendation_profiles as module


def result(score=100, valid=True):
    candidate = SimpleNamespace(kind='module', uid_slot=3, uid_serial=7, uid=(3, 7))
    return SimpleNamespace(role_keys=((1003, 'r'),), candidates_by_legacy_uid={'context:3:7': candidate},
        plans={'r': {'valid': valid, 'score': score, 'assigned_set_drives': [SimpleNamespace(uid='context:3:7')]}})


def setup(monkeypatch, profiles):
    user = MagicMock()
    user.__enter__.return_value = user
    user.list_optimization_profiles.return_value = profiles
    monkeypatch.setattr(module, 'read_allocation_scoring_rules', lambda _: object())
    context = SimpleNamespace(profile_id=1, candidates=tuple(result().candidates_by_legacy_uid.values()))
    build = Mock(return_value=context)
    monkeypatch.setattr(module, 'build_allocation_context', build)
    contexts, issues = module.materialize_profile_contexts(user, MagicMock(), Path('config/stats.json'), 12, lambda: None)
    return context, build, SimpleNamespace(profile_contexts=contexts, profile_issues=issues)


def test_empty_profile_does_not_disable_discard(monkeypatch):
    _, build, inputs = setup(monkeypatch, [{'profile_id': 1, 'version': {'version_number': 5, 'characters': []}}])
    witnesses, issues = module.freeze_profile_witnesses(inputs, lambda: None, lambda: False)
    assert not witnesses and not issues
    build.assert_not_called()


def test_exact_profile_version_constraints_and_native_uids_are_rechecked(monkeypatch):
    context, build, inputs = setup(monkeypatch, [{'profile_id': 1, 'version': {'version_number': 5, 'characters': [{}]}}])
    run = Mock(return_value=result())
    monkeypatch.setattr(module, 'run_legacy_allocation', run)
    witnesses, issues = module.freeze_profile_witnesses(inputs, lambda: None, lambda: False)
    assert not issues
    assert build.call_args.kwargs['profile_version'] == 5
    assert build.call_args.kwargs['snapshot_id'] == 12
    assert module.witness_uids(witnesses) == frozenset({'nte-module-3-7'})
    run.return_value = result(score=90)
    assert module.verify_profile_removal(witnesses, {'nte-module-3-7'}, lambda: None, lambda: False) == ['profile:1:1003']
    assert run.call_args.args[0] is context
    assert run.call_args.kwargs['excluded_uids'] == {(3, 7)}


def test_failed_profile_is_incomplete_and_cancel_propagates(monkeypatch):
    _, _, inputs = setup(monkeypatch, [{'profile_id': 1, 'version': {'version_number': 5, 'characters': [{}]}}])
    run = Mock(return_value=result(valid=False))
    monkeypatch.setattr(module, 'run_legacy_allocation', run)
    _, issues = module.freeze_profile_witnesses(inputs, lambda: None, lambda: False)
    assert issues
    run.side_effect = CancelledError()
    with pytest.raises(CancelledError):
        module.freeze_profile_witnesses(inputs, lambda: None, lambda: False)


def test_native_profile_solver_does_not_drop_unsupported_constraints_or_fallback_on_error(monkeypatch):
    context = SimpleNamespace(allocation_strategy='role_priority',
        roles=[SimpleNamespace(property_limits=(), crit_threshold=None)])
    native = Mock()
    run = Mock(return_value=result())
    monkeypatch.setattr(module, 'run_legacy_allocation', run)
    module._run(context, lambda: False, executor=native)
    assert callable(run.call_args.kwargs['kernel_executor'])
    for limits, threshold in ((('special-bound',), None), ((), 0.0)):
        context.roles[0].property_limits = limits
        context.roles[0].crit_threshold = threshold
        module._run(context, lambda: False, executor=native)
        assert 'kernel_executor' not in run.call_args.kwargs
    context.roles[0].property_limits = ()
    context.roles[0].crit_threshold = None
    run.side_effect = RuntimeError('native failed')
    with pytest.raises(RuntimeError, match='native failed'):
        module._run(context, lambda: False, executor=native)
