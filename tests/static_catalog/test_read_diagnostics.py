# 验证静态读取故障保留底层证据、真实占用可恢复，调用方异常不被误报。
import sqlite3
from unittest.mock import Mock

import pytest

from src.integrations.static_catalog import read_diagnostics as diagnostics
from src.integrations.static_catalog import static_catalog_release as module
from tests.characters.official.test_static_read_unit import release_fixture


def test_real_writer_is_reported_before_read_and_retry_succeeds_after_close(tmp_path, monkeypatch):
    path = release_fixture(tmp_path)
    logged = Mock()
    monkeypatch.setattr(diagnostics, 'log_event', logged)
    with path.open('r+b'):
        with pytest.raises(module.StaticCatalogReleaseError) as caught:
            module.StaticCatalogReleaseReader().freeze(path)
    assert caught.value.code == 'release_waiting'
    assert caught.value.__cause__.winerror == 32
    assert logged.call_count == 1
    assert logged.call_args.args[1] == 'static_catalog.read_failed'
    fields = logged.call_args.kwargs
    assert fields['stage'] == 'pin_open'
    assert fields['error_chain'][0]['winerror'] == 32
    assert fields['database_file'] == path.name
    with module.StaticCatalogReleaseReader().read_unit(path) as unit:
        assert unit.dao._one('SELECT value FROM marker')['value'] == 'A'
    assert logged.call_count == 1


def test_sqlite_failure_preserves_original_exception_and_distinct_stage(tmp_path, monkeypatch):
    path = release_fixture(tmp_path)
    logged = Mock()
    monkeypatch.setattr(diagnostics, 'log_event', logged)
    error = sqlite3.OperationalError('fixture read failure')
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR
    error.sqlite_errorname = 'SQLITE_IOERR'

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, 'StaticGameDataDao', fail)
    with pytest.raises(module.StaticCatalogReleaseError) as caught:
        module.StaticCatalogReleaseReader().freeze(path)
    assert caught.value.__cause__ is error
    fields = logged.call_args.kwargs
    assert fields['stage'] == 'sqlite_open'
    assert fields['error_chain'][0]['sqlite_errorcode'] == sqlite3.SQLITE_IOERR
    assert fields['error_chain'][0]['origin'].endswith(':fail')


@pytest.mark.parametrize('error', [OSError('account resource'), sqlite3.OperationalError('account query')])
def test_consumer_error_is_not_logged_as_static_failure(tmp_path, monkeypatch, error):
    path = release_fixture(tmp_path)
    logged = Mock()
    monkeypatch.setattr(diagnostics, 'log_event', logged)
    with pytest.raises(type(error)) as caught:
        with module.StaticCatalogReleaseReader().read_unit(path):
            raise error
    assert caught.value is error
    logged.assert_not_called()
    assert module.StaticCatalogReleaseReader().freeze(path).dataset_id == 'same-fixture'


def test_borrowed_revalidation_logs_once_and_keeps_error_cause(tmp_path, monkeypatch):
    path = release_fixture(tmp_path)
    logged = Mock()
    monkeypatch.setattr(diagnostics, 'log_event', logged)
    error = PermissionError(13, 'fixture manifest read denied')

    def fail():
        raise error

    with module.StaticCatalogReleaseReader().read_unit(path) as unit:
        unit._revalidate = fail
        with pytest.raises(module.StaticCatalogReleaseError) as caught:
            unit.ensure_unchanged()
        assert caught.value.__cause__ is error
    assert logged.call_count == 1
    assert logged.call_args.kwargs['stage'] == 'revalidate'


def test_diagnostic_sink_failure_does_not_change_original_business_failure(tmp_path, monkeypatch):
    path = release_fixture(tmp_path)
    monkeypatch.setattr(diagnostics, 'log_event', Mock(side_effect=RuntimeError('sink failed')))
    with path.open('r+b'):
        with pytest.raises(module.StaticCatalogReleaseError) as caught:
            module.StaticCatalogReleaseReader().freeze(path)
    assert caught.value.__cause__.winerror == 32
    assert module.StaticCatalogReleaseReader().freeze(path).dataset_id == 'same-fixture'


def test_exception_chain_is_bounded_and_redacts_secrets(monkeypatch):
    logged = Mock()
    monkeypatch.setattr(diagnostics, 'log_event', logged)
    error = OSError('token=private-fixture-value')
    error.__cause__ = error
    diagnostics.report_static_read_failure('game_static.sqlite3', 'pin_open', error)
    chain = logged.call_args.kwargs['error_chain']
    assert len(chain) == 1
    assert 'private-fixture-value' not in repr(chain)
