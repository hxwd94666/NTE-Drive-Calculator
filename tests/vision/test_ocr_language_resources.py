# 核对真实 CTC 字符顺序、资源错误传播和最终事务的模型/字典身份。
from dataclasses import replace
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from src.integrations.vision.recognition.ocr import language_resources as language
from src.integrations.vision.recognition.ocr import model_resources as models
from src.integrations.vision.recognition import identification_parser, screenshot_parser, batch_processor
from src.integrations.vision.recognition.ocr.ocr_engine import OCREngine
from src.services.identification import tasks
from src.services.inventory import vision_snapshot
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
from src.services.recognition.resources import RecognitionResourceError
from src.services.scanning.pipeline import ScanParseCoordinator
from src.storage.sqlite.user_data_dao import UserDataDao
from src.models.equipment import Tape
from tests.vision.test_ocr_runtime import NativeOCR, model_fixture, freeze_micro_resources
from tests.vision.test_recognition_resources import (
    selected as selected, freeze_image, tape_row, baseline_user, read_inventory,
)

NTE_TEST_TIER = 'core'


@pytest.mark.parametrize('package', models.RAPIDOCR_PACKAGE_NAMES)
def test_actual_ctc_decoders_use_shared_order_blank_space_and_exact_lines(package, tmp_path):
    module_spec = importlib.util.find_spec(package)
    if module_spec is None:
        pytest.skip('本环境未安装该可选 OCR 后端')
    # 仅加载锁定后端的纯 CTC 文件；不导入原生 OpenVINO/ORT 推理会话。
    source = Path(module_spec.origin).parent / 'ch_ppocr_rec/utils.py'
    spec = importlib.util.spec_from_file_location('fixture_ctc_' + package, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = language.load_ocr_dictionary(language.shared_dictionary_path())
    decoder = module.CTCLabelDecode(character=['ignored metadata'], character_path=frozen.path)
    assert tuple(decoder.character) == frozen.decoder_characters
    text = '攻击力% 48'
    indexes = [0]
    for char in text:
        index = decoder.character.index(char)
        indexes.extend((index, index, 0))
    logits = np.zeros((1, len(indexes), len(decoder.character)), dtype=np.float32)
    for position, index in enumerate(indexes):
        logits[0, position, index] = 1
    assert decoder(logits)[0] == (text, 1.0)
    # 证明不是通用 strip/splitlines：首尾空格、垂直制表符和独立 CR 都保留。
    payload = b' A \r\nB\vC\rD\n'
    path = tmp_path / 'keys.txt'
    path.write_bytes(payload)
    custom = replace(language.OCR_DICTIONARY_SPEC, filename=path.name, size_bytes=len(payload),
                     sha256=hashlib.sha256(payload).hexdigest().upper(), character_count=2)
    evidence = language.load_ocr_dictionary(path, spec=custom)
    assert evidence.characters == (' A ', 'B\vC\rD')
    assert tuple(module.CTCLabelDecode(character_path=path).character) == evidence.decoder_characters


@pytest.mark.parametrize('point', ['constructor_order', 'constructor_interface', 'failed_constructor_dictionary',
                                   'warmup_dictionary', 'warmup_order'])
def test_constructor_and_warmup_resource_errors_never_fall_back(point):
    with model_fixture() as (root, _specs):
        calls = []
        class Native(NativeOCR):
            def __init__(self, **kwargs):
                super().__init__()
                if point == 'constructor_order':
                    self.text_rec.postprocess_op.character.reverse()
                elif point == 'constructor_interface':
                    del self.text_rec
                elif point == 'failed_constructor_dictionary':
                    Path(kwargs['rec_keys_path']).write_bytes(b'drift')
                    raise RuntimeError('native fixture failed')
            def __call__(self, image):
                calls.append('warmup')
                if point == 'warmup_dictionary':
                    (root / 'ppocr_keys_v1.txt').write_bytes(b'drift')
                elif point == 'warmup_order':
                    self.text_rec.postprocess_op.character.reverse()
                return [], 0
        cpu = Mock(return_value=NativeOCR())
        with patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=Native),
                                       'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=cpu)}):
            with pytest.raises(models.OcrModelResourceError):
                OCREngine()
        cpu.assert_not_called()
        assert calls == ([] if point.startswith('constructor') or point.startswith('failed') else ['warmup'])


@pytest.mark.parametrize('point', ['before', 'during', 'during_failure'])
def test_inference_decoder_drift_delivers_no_text_or_ordinary_parse_error(point):
    with model_fixture(), patch.dict('sys.modules', {
        'rapidocr_openvino': SimpleNamespace(RapidOCR=lambda **kwargs: NativeOCR()),
    }):
        engine = OCREngine()
        native = engine.ocr
        if point == 'before':
            native.text_rec.postprocess_op.character.reverse()
        else:
            def infer(image):
                native.text_rec.postprocess_op.character.reverse()
                if point == 'during_failure':
                    raise RuntimeError('native fixture failed')
                return [], 0
            infer.text_rec = native.text_rec
            engine.ocr = infer
        with pytest.raises(models.OcrModelResourceError):
            engine.extract_text(np.zeros((10, 12), dtype=np.uint8))


def test_resource_error_crosses_standard_full_reward_batch_and_scan_boundaries(tmp_path, monkeypatch):
    error = models.OcrModelResourceError('synthetic incompatible decoder')
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    monkeypatch.setattr(identification_parser, 'imread_unicode', lambda _path: image)
    monkeypatch.setattr(identification_parser, 'crop_window_border_from_image', lambda img: img)
    processor = SimpleNamespace(_process_single_image=Mock(side_effect=error), ocr_engine=Mock())
    with pytest.raises(models.OcrModelResourceError):
        identification_parser.parse_identify_items(processor, 'synthetic.png')
    processor.ocr_engine.extract_lines.assert_not_called()
    processor._process_single_image.return_value = None
    processor._process_single_image.side_effect = None
    processor.ocr_engine.extract_lines.side_effect = error
    with pytest.raises(models.OcrModelResourceError):
        identification_parser.parse_identify_items(processor, 'synthetic.png')
    monkeypatch.setattr(identification_parser, 'process_identify_standard_forced', Mock(side_effect=error))
    with pytest.raises(models.OcrModelResourceError):
        identification_parser.parse_identify_items(processor, 'synthetic.png', forced_type='drive')
    monkeypatch.setattr(screenshot_parser, 'process_reward_scene', Mock(side_effect=error))
    with pytest.raises(models.OcrModelResourceError):
        screenshot_parser._prefer_complete_or_reward_item(SimpleNamespace(), image, SimpleNamespace(sub_stats={}), 'synthetic.png')
    owner = object.__new__(batch_processor.BatchProcessor)
    owner.cancel_check = None
    owner.input_dir = str(tmp_path)
    (tmp_path / 'shot.png').write_bytes(b'synthetic')
    owner.process_image_file = Mock(side_effect=error)
    with pytest.raises(models.OcrModelResourceError):
        owner.process_all()
    for streaming in (False, True):
        capture = Mock()
        def scan(count, *, on_capture, commit_on_complete):
            assert not commit_on_complete
            for index in (1, 2):
                on_capture('synthetic.png', index, count)
            return count
        capture.start_scan = scan
        parser = SimpleNamespace(process_image_file=Mock(side_effect=error))
        coordinator = ScanParseCoordinator(capture, parser, 2, parse_during_scan=streaming)
        with pytest.raises((models.OcrModelResourceError, RuntimeError)):
            coordinator.run()
        assert parser.process_image_file.call_count == 1


@pytest.mark.parametrize('member', ['dictionary', 'rec_model'])
def test_confirmation_and_final_transaction_reject_changed_ocr_file(selected, tmp_path, monkeypatch, member):
    frozen = freeze_image(selected)
    ocr = frozen.image_profile.ocr_resources
    target = ocr.dictionary.path if member == 'dictionary' else Path(ocr.model_kwargs()['rec_model_path'])
    database = tmp_path / 'user.sqlite3'
    previous = baseline_user(database)
    request = tasks.IdentificationMatchRequest.freeze(config_dir=selected[0], user_database_path=database,
            static_database_path=selected[1], items=[Tape(uid="fixture", quality="Gold", area=15,
                set_name="夹具套装", main_stats="攻击力%", sub_stats={})], resources=frozen)
    payload = target.read_bytes()
    target.write_bytes(payload + b'drift')
    with pytest.raises(RecognitionResourceError):
        tasks.match_identification_items(request)
    # 下一批重新冻结，模型/字典仍用原始字节；此后在最终事务内部改变文件。
    target.write_bytes(payload)
    frozen = freeze_image(selected)
    target = frozen.image_profile.ocr_resources.dictionary.path if member == 'dictionary' else Path(frozen.image_profile.ocr_resources.model_kwargs()['rec_model_path'])
    staged = []
    class ObservedDao(UserDataDao):
        def import_inventory_snapshot(self, snapshot, **kwargs):
            guard = kwargs['before_commit']
            def drift():
                assert self._db().in_transaction
                staged.append(self._db().execute('SELECT COUNT(*) FROM inventory_snapshot').fetchone()[0])
                target.write_bytes(target.read_bytes() + b'drift')
                guard()
            return super().import_inventory_snapshot(snapshot, **{**kwargs, 'before_commit': drift})
    monkeypatch.setattr(vision_snapshot, 'UserDataDao', ObservedDao)
    stats = {'parse_scope': 'full', 'total_count': 1, 'failed_count': 0, 'vision_items': [tape_row()],
             'recognition_resources': frozen}
    with pytest.raises(RecognitionResourceError):
        commit_completed_vision_inventory(database, stats, [])
    assert staged == [2] and read_inventory(database) == (previous, [], 1)
    assert str(selected[0]) not in str(frozen.public_identity())


def test_packaged_dictionary_required_single_and_bound_to_recognizer(tmp_path, monkeypatch):
    internal = tmp_path / 'internal'
    frozen = freeze_micro_resources(internal / 'assets/ocr/models')
    dictionary = internal / 'assets/ocr/ppocr_keys_v1.txt'
    dictionary.write_bytes(frozen.dictionary.path.read_bytes())
    frozen.dictionary.path.unlink()
    monkeypatch.setattr(models, 'OCR_MODEL_SPECS', tuple(entry.spec for entry in frozen.models.files))
    monkeypatch.setattr(language, 'OCR_DICTIONARY_SPEC', frozen.dictionary.spec)
    from tools.release.ocr_build_inputs import write_ocr_support_manifest
    from src.integrations.vision.recognition.ocr.backend_support import OcrBackendSupport
    write_ocr_support_manifest(internal / 'assets/ocr/backend_support.json', OcrBackendSupport('default'))
    assert len(models.validate_packaged_ocr_models(internal)) == 3
    duplicate = internal / 'rapidocr_openvino/ch_ppocr_rec/ppocr_keys_v1.txt'
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(dictionary.read_bytes())
    with pytest.raises(models.OcrModelResourceError, match='重复'):
        models.validate_packaged_ocr_models(internal)
    duplicate.unlink()
    monkeypatch.setattr(language, 'OCR_DICTIONARY_SPEC', replace(frozen.dictionary.spec, recognizer_sha256='wrong'))
    with pytest.raises(models.OcrModelResourceError, match='绑定'):
        models.validate_packaged_ocr_models(internal)
    monkeypatch.setattr(language, 'OCR_DICTIONARY_SPEC', frozen.dictionary.spec)
    dictionary.unlink()
    with pytest.raises(models.OcrModelResourceError):
        models.validate_packaged_ocr_models(internal)


def test_actual_offline_worker_stops_first_decoder_drift_without_completed_signal(selected, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from src.features.scanning.vision_worker import VisionWorkerThread
    app = QApplication.instance() or QApplication([])
    shots = tmp_path / 'shots'
    shots.mkdir()
    for name in ('first.png', 'second.png'):
        (shots / name).write_bytes(b'synthetic; image parsing calls real OCR boundary')
    worker = VisionWorkerThread(str(shots), config_dir=selected[0], static_database_path=selected[1])
    completed, errors, canceled, calls = [], [], [], []
    worker.processing_done.connect(completed.append)
    worker.error.connect(errors.append)
    worker.canceled.connect(canceled.append)
    def process(owner, _path, filename, **kwargs):
        calls.append(filename)
        owner.ocr_engine.ocr.text_rec.postprocess_op.character.reverse()
        owner.ocr_engine.extract_text(np.zeros((10, 12), dtype=np.uint8))
        pytest.fail('invalid decoder returned an item')
    monkeypatch.setattr(batch_processor.BatchProcessor, 'process_image_file', process)
    with model_fixture(), patch.dict('sys.modules', {
        'rapidocr_openvino': SimpleNamespace(RapidOCR=lambda **kwargs: NativeOCR()),
    }):
        worker.run()
    app.processEvents()
    assert calls == ['first.png'] and completed == [] and canceled == [] and len(errors) == 1
