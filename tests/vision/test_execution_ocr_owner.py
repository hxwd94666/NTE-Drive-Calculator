# 验证执行 OCR 单任务复用、惰性初始化、物理结束、致命失败及借用边界。
from __future__ import annotations

from concurrent.futures import CancelledError
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch
import weakref

import numpy as np
import pytest

from src.features.drive_assembly import assembly_session, executor
from src.features.drive_assembly.rewind import execution as rewind
from src.integrations.vision.recognition import ocr_engine, task_session
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.integrations.vision.recognition.task_session import OcrTaskStateError, use_ocr_session
from src.integrations.vision.window_capture import WindowRect
from src.utils.exceptions import OCRParseError
from tests.vision.test_ocr_runtime import model_fixture, NativeOCR

NTE_TEST_TIER = 'core'
ACTION = {'name': 'main_stat_option', 'ocr_target_text': 'Attack Percent',
          'ocr_search_region': (100, 200, 300, 420), 'fallback_position': (9, 9)}
IMAGE = np.zeros((500, 500, 3), dtype=np.uint8)


class Backend:
    def __init__(self):
        self.clicks = []
        self.closed = False

    def screenshot(self):
        return IMAGE

    def click(self, position):
        self.clicks.append(position)

    def pause(self, seconds):
        pass

    def press_key(self, key):
        pass

    def close(self):
        self.closed = True


class Engine:
    def __init__(self, *, cancel_check=None):
        self.cancel_check = cancel_check
        self.reads = 0
        self.closed = False

    def extract_lines(self, image):
        self.reads += 1
        return [{'text': 'Attack Percent', 'box': (20, 10, 120, 50)}]

    def extract_text(self, image):
        self.reads += 1
        return ['0']

    def close(self):
        self.closed = True


def role_plan(name):
    return {'role_name': name, 'available': True,
            'actions': [{'sequence': [ACTION]}]}


@pytest.mark.parametrize('route', ['all', 'traversal'])
def test_nested_roles_and_navigation_share_one_lazy_engine(route):
    backend = Backend()
    engine = Engine()
    with patch.object(ocr_engine, 'OCREngine', return_value=engine) as factory:
        if route == 'all':
            executor.execute_all_role_assembly_plan(
                {'role_plans': [role_plan('one'), role_plan('two')]},
                backend=backend, pause_seconds=0, should_stop=lambda: False)
            expected = 2
        else:
            steps = [{'role_name': name, 'action_sequence': [ACTION,
                      {'name': 'run_drive_assembly_for_role', 'role_name': name}, ACTION]}
                     for name in ['one', 'two']]
            executor.execute_role_traversal_assembly_plan(
                {'plans': steps}, {'role_plans': [role_plan('one'), role_plan('two')]},
                backend=backend, pause_seconds=0, should_stop=lambda: False)
            expected = 6
    assert factory.call_count == 1
    assert engine.reads == expected
    assert backend.clicks == [(170, 230)] * expected


def test_no_ocr_action_creates_no_engine_and_independent_tasks_release_references():
    refs = []

    def create(**kwargs):
        engine = Engine(**kwargs)
        refs.append(weakref.ref(engine))
        return engine

    with patch.object(ocr_engine, 'OCREngine', side_effect=create) as factory:
        executor.execute_action_sequence([{'position': (1, 2)}], backend=Backend(), pause_seconds=0)
        factory.assert_not_called()
        for _ in range(2):
            executor.execute_action_sequence([ACTION], backend=Backend(), pause_seconds=0)
            assert refs[-1]() is None
        assert factory.call_count == 2


@pytest.mark.parametrize('mode,expected_reads', [('none', 3), ('enabled', 5)])
def test_rewind_balances_and_prices_share_default_engine(mode, expected_reads):
    class CurrencyEngine(Engine):
        def extract_text(self, image):
            self.reads += 1
            return ['600'] if mode == 'enabled' and self.reads in (2, 4) else ['0']

    engine = CurrencyEngine()
    capture = lambda: (np.zeros((1440, 2560, 3), dtype=np.uint8), WindowRect(0, 0, 2560, 1440))
    with patch.object(ocr_engine, 'OCREngine', return_value=engine) as factory:
        report = rewind.execute_rewind_request(
            rewind.RewindExecutionRequest(('gold', 'purple', 'blue'), mode),
            backend=Backend(), capture=capture, should_stop=lambda: False)
    assert factory.call_count == 1
    assert engine.reads == expected_reads
    assert report.ten_draws == 0
    assert report.quality_draws == (('gold', 0), ('purple', 0), ('blue', 0))


@pytest.mark.parametrize('failure', [CancelledError, OcrModelResourceError, RuntimeError])
def test_initialization_failure_invalidates_same_task_without_reinitialization(failure):
    backend = Backend()
    expected = executor.AssemblyExecutionStopped if failure is CancelledError else (
        OcrModelResourceError if failure is OcrModelResourceError else OcrTaskStateError)
    with use_ocr_session() as owner, patch.object(ocr_engine, 'OCREngine', side_effect=failure('fixture')) as factory:
        with pytest.raises(expected):
            executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, ocr_session=owner)
        with pytest.raises(OcrTaskStateError):
            owner.extract_lines(IMAGE)
        assert factory.call_count == 1
        assert backend.clicks == []
    assert owner.closed and owner._engine is None
    with patch.object(ocr_engine, 'OCREngine', return_value=Engine()) as factory:
        executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0)
        assert factory.call_count == 1
        assert backend.clicks == [(170, 230)]


@pytest.mark.parametrize('stage', ['capture', 'extract'])
@pytest.mark.parametrize('failure', [CancelledError, OcrModelResourceError])
def test_fatal_capture_or_recognition_never_uses_fallback_position(stage, failure):
    backend = Backend()
    engine = Engine()
    if stage == 'capture':
        backend.screenshot = Mock(side_effect=failure('fixture'))
    else:
        engine.extract_lines = Mock(side_effect=failure('fixture'))
    expected = executor.AssemblyExecutionStopped if failure is CancelledError else OcrModelResourceError
    with use_ocr_session(engine=engine) as owner:
        with pytest.raises(expected):
            executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, ocr_session=owner)
    assert backend.clicks == []
    assert owner._failed
    assert not engine.closed  # 借用对象不由子步骤销毁。


def test_ordinary_parse_failure_still_uses_existing_fallback_but_cancel_after_return_does_not():
    engine = Engine()
    engine.extract_lines = Mock(side_effect=OCRParseError('fixture image'))
    backend = Backend()
    with use_ocr_session(engine=engine) as owner:
        executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, ocr_session=owner)
    assert backend.clicks == [(9, 9)]
    canceled = Event()

    def parse(image):
        canceled.set()
        return []

    engine.extract_lines = parse
    backend = Backend()
    with use_ocr_session(engine=engine, should_stop=canceled.is_set) as owner:
        with pytest.raises(executor.AssemblyExecutionStopped):
            executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, ocr_session=owner)
    assert backend.clicks == []
    assert not engine.closed


@pytest.mark.parametrize('phase', ['warmup', 'extract'])
def test_real_engine_decoder_change_stops_target_input(phase):
    class ChangedDecoder(NativeOCR):
        calls = 0

        def __call__(self, image):
            self.calls += 1
            result = super().__call__(image)
            if self.calls == (1 if phase == 'warmup' else 2):
                self.text_rec.postprocess_op.character.reverse()
            return result

    actual = ocr_engine.OCREngine
    native = ChangedDecoder()
    backend = Backend()
    with model_fixture(), patch.dict('sys.modules', {'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=lambda **kwargs: native)}), patch.object(ocr_engine, 'OCREngine', side_effect=lambda **kwargs: actual('cpu', **kwargs)):
        with pytest.raises(OcrModelResourceError):
            executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, should_stop=lambda: False)
    assert backend.clicks == []


@pytest.mark.parametrize('phase', ['warmup', 'extract'])
def test_cancel_waits_for_real_native_return_before_closing_owner(phase):
    started, release, canceled = Event(), Event(), Event()
    owners, errors = [], []

    class BlockingNative(NativeOCR):
        calls = 0

        def __call__(self, image):
            self.calls += 1
            if self.calls == (1 if phase == 'warmup' else 2):
                started.set()
                assert release.wait(5)
            return super().__call__(image)

    actual_engine, actual_owner = ocr_engine.OCREngine, task_session.OcrTaskSession
    native, backend = BlockingNative(), Backend()

    def create_owner(**kwargs):
        owner = actual_owner(**kwargs)
        owners.append(owner)
        return owner

    def run():
        try:
            executor.execute_action_sequence([ACTION], backend=backend, pause_seconds=0, should_stop=canceled.is_set)
        except BaseException as exc:
            errors.append(exc)

    with model_fixture(), patch.dict('sys.modules', {'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=lambda **kwargs: native)}), patch.object(ocr_engine, 'OCREngine', side_effect=lambda **kwargs: actual_engine('cpu', **kwargs)), patch.object(task_session, 'OcrTaskSession', side_effect=create_owner):
        thread = Thread(target=run)
        thread.start()
        try:
            assert started.wait(5)
            canceled.set()
            assert not owners[0].closed
            assert (owners[0]._engine is None) == (phase == 'warmup')
            assert backend.clicks == []
        finally:
            release.set()
            thread.join(5)
        assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], executor.AssemblyExecutionStopped)
    assert owners[0].closed and owners[0]._engine is None
    assert backend.clicks == []


@pytest.mark.parametrize('failure_stage', ['none', 'startup', 'roster', 'record', 'plan', 'execute'])
def test_assembly_session_reuses_startup_owner_and_cleans_all_middle_phases(monkeypatch, failure_stage):
    backend, engines, owners = Backend(), [], []
    plan = {'roles': ['fixture'], 'role_plans': [role_plan('fixture')]}
    monkeypatch.setattr(assembly_session, '_capture_foreground_client_image', lambda: (IMAGE, WindowRect(0, 0, 500, 500)))
    monkeypatch.setattr(assembly_session, '_fit_content_rect', lambda width, height: (0, 0, width, height))
    monkeypatch.setattr(assembly_session, 'build_all_role_assembly_plan', lambda *args, **kwargs: plan)
    monkeypatch.setattr(assembly_session, '_filter_assembly_plan_for_roles', lambda plan, roles: plan)
    monkeypatch.setattr(assembly_session, '_log_assembly_plan_diagnostics', lambda *args: None)
    monkeypatch.setattr(assembly_session, '_log_traversal_plan_diagnostics', lambda *args: None)
    monkeypatch.setattr(assembly_session, 'role_recognition_candidates', lambda *args: ['fixture'])
    monkeypatch.setattr(assembly_session, 'map_role_list_mouse_entry', lambda *args: {'entry_sequence': [ACTION]})
    monkeypatch.setattr(assembly_session, 'map_role_list_mouse_row_scan', lambda *args: {})

    class Recorder:
        def __init__(self, root):
            self.directory = root

        def save_image(self, image, name):
            pass

        def capture_foreground(self, name):
            if failure_stage == 'record' and name == 'role_list_scan_complete':
                raise RuntimeError('record fixture')

        def record_action(self, *args):
            pass

    def recognize(image, roles, owner, **kwargs):
        owners.append(owner)
        owner.extract_text(image)
        if failure_stage == 'startup':
            raise OcrModelResourceError('startup fixture')
        return SimpleNamespace(role_name='fixture', method='ocr', raw_text='private-ocr-fixture', confidence=1)

    def collect(roles, **kwargs):
        kwargs['enter_role_list']()
        kwargs['current_observer'](0)
        if failure_stage == 'roster':
            raise RuntimeError('roster fixture')
        return {'roles': ['fixture'], 'current_index': 0}

    def traversal(*args, **kwargs):
        if failure_stage == 'plan':
            raise RuntimeError('plan fixture')
        return {'plans': [{'role_name': 'fixture', 'action_sequence': [
            {'name': 'run_drive_assembly_for_role', 'role_name': 'fixture'}]}]}

    class SessionEngine(Engine):
        def extract_lines(self, image):
            if failure_stage == 'execute' and self.reads == 3:
                raise OcrModelResourceError('execute fixture')
            return super().extract_lines(image)

    def engine(**kwargs):
        result = SessionEngine(**kwargs)
        engines.append(result)
        return result

    monkeypatch.setattr(assembly_session, 'AssemblyRunRecorder', Recorder)
    monkeypatch.setattr(assembly_session, 'recognize_current_role_from_image', recognize)
    monkeypatch.setattr(assembly_session, 'collect_role_roster_from_mouse_rows', collect)
    monkeypatch.setattr(assembly_session, 'plan_role_assembly_from_role_list_roster', traversal)
    monkeypatch.setattr(ocr_engine, 'OCREngine', engine)
    call = lambda: assembly_session._execute_roles_from_current_game_page(
        {}, None, backend, 'template-fixture', 1, 0, 0, False,
        record_root='record-fixture', should_stop=lambda: False)
    if failure_stage == 'none':
        call()
        assert engines[0].reads == 4  # 首次文字、入口目标、观察文字、装配目标。
        assert backend.clicks == [(170, 230)] * 2
        assert len(owners) == 2 and owners[0] is owners[1]
    else:
        with pytest.raises(RuntimeError, match='fixture'):
            call()
    assert len(engines) == 1
    assert all(owner.closed and owner._engine is None for owner in owners)
    assert backend.closed
