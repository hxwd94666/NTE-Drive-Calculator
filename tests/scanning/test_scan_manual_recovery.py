# 验证扫描补录关闭重开、完整冻结身份及取消/账号/退场的公开行为。
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QMessageBox
from PIL import Image

from src.domain.work_mode import WorkMode
from src.features.scanning.dependencies import ScanningDependencies
from src.features.scanning.manual_recovery import ManualRecoverySession, complete_pending_manual_items
from src.integrations.vision.recognition.config import RecognitionLayout
from src.integrations.vision.recognition.duplicate_filter import RecoverableParseError
from src.integrations.vision.recognition.image_resources import RecognitionImageProfile, ShapeTemplateFile
from src.integrations.vision.recognition.language_resources import OcrDictionarySpec, OcrDictionaryFile, OcrRuntimeResources
from src.integrations.vision.recognition.model_resources import OcrModelSpec, OcrModelFile, OcrModelResources
from src.integrations.vision.recognition.resources import RecognitionResources

NTE_TEST_TIER = "core"
FILLED = (("MissingBeta", 23.125),)


def synthetic_resources(tmp_path):
    data = json.dumps({"gold_base_values": {"MissingAlpha": 1, "MissingBeta": 2}}).encode()
    files = tuple(OcrModelFile(OcrModelSpec(f"{keyword}.synthetic", 1,
        hashlib.sha256(keyword.encode()).hexdigest(), keyword), tmp_path / keyword, (1, 2, 1, 3, 4))
        for keyword in ("det_model_path", "rec_model_path", "cls_model_path"))
    spec = OcrDictionarySpec("dictionary.synthetic", 1, hashlib.sha256(b"A").hexdigest(), 1, files[1].spec.sha256)
    dictionary = OcrDictionaryFile(spec, tmp_path / "dictionary.synthetic", (1, 2, 1, 3, 4), ("A",))
    ocr = OcrRuntimeResources(OcrModelResources(files), dictionary)
    template = ShapeTemplateFile("S1", tmp_path / "shape.synthetic", hashlib.sha256(b"shape").hexdigest(), (1, 2, 1, 3, 4))
    profile = RecognitionImageProfile(RecognitionLayout((100, 100), (("sub", (0, 0, 100, 100)),)), (template,), ocr)
    return RecognitionResources(tmp_path, tmp_path / "static.sqlite3", ("synthetic", 1, "fixture"), data,
        hashlib.sha256(data).hexdigest(), (("suit-fixture", "fixture"),), frozenset({"S1"}), (), profile)


@pytest.fixture
def scene(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = QWidget()
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (8, 8), (20, 30, 40)).save(image)
    item = SimpleNamespace(item_type="drive", shape_id="S1", quality="Gold",
        sub_stats={"KnownA": 1.0, "KnownB": 2.0, "KnownC": 3.0})
    record = RecoverableParseError("synthetic", item, 3).to_record(str(image), image.name)
    stats = {"pending_manual_items": [record], "pending_manual_count": 1, "total_count": 1,
        "parse_scope": "full", "recognition_resources": synthetic_resources(tmp_path)}
    yield SimpleNamespace(app=app, root=root, image=image, stats=stats, scope=("fixture", 1, tmp_path / "user.sqlite3"))
    root.deleteLater()
    app.processEvents()


def modal(scene, session, action, *, stats=None, scope=None, is_current=lambda: True):
    errors = []
    def interact():
        dialog = scene.app.activeModalWidget()
        try:
            action(dialog)
        except Exception as error:
            errors.append(repr(error))
            dialog.reject()
    QTimer.singleShot(0, interact)
    result = complete_pending_manual_items(scene.root, stats or scene.stats, scene.image.parent,
        session=session, scope=scope or scene.scope, is_current=is_current)
    assert not errors, errors
    assert scene.app.activeModalWidget() is None
    scene.app.processEvents()
    return result


@pytest.mark.parametrize("finish", ["close", "reject"])
def test_closed_dialog_keeps_values_but_requires_explicit_accept(scene, finish):
    session = ManualRecoverySession()
    original = deepcopy(scene.stats["pending_manual_items"])
    raw = scene.image.read_bytes()
    def fill(dialog):
        dialog.restore_filled_values(FILLED)
        getattr(dialog, finish)()
    assert modal(scene, session, fill) is None
    assert session.has_draft
    observed = []
    def reopen(dialog):
        observed.append(dialog.filled_values())
        dialog.accept()
    items = modal(scene, session, reopen, stats=deepcopy(scene.stats))
    assert observed == [FILLED]
    assert items[0]["sub_stats"]["MissingBeta"] == 23.125
    assert "image_sha256" not in items[0]
    assert scene.stats["pending_manual_items"] == original
    assert scene.image.read_bytes() == raw
    assert not scene.scope[2].exists()


def test_partial_invalid_values_survive_close_without_becoming_inventory(scene, monkeypatch):
    session = ManualRecoverySession()
    stats = deepcopy(scene.stats)
    second = deepcopy(stats["pending_manual_items"][0])
    second["filename"] = "synthetic-second.png"
    stats["pending_manual_items"].append(second)
    values = (("", -12.125), ("KnownA", 8.75))
    warnings = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warnings)
    def reject_invalid(dialog):
        dialog.restore_filled_values(values)
        dialog.accept()
        assert dialog.isVisible()  # 无效表单没有被接受，仍可继续填写。
        dialog.reject()
    assert modal(scene, session, reject_invalid, stats=stats) is None
    assert warnings.call_count == 1
    observed = []
    def reopen(dialog):
        observed.append(dialog.filled_values())
        dialog.close()
    assert modal(scene, session, reopen, stats=stats) is None
    assert observed == [values]
    assert not scene.scope[2].exists()


@pytest.mark.parametrize("change", ["account", "generation", "database", "dataset", "stats", "shape", "layout",
    "dictionary", "model", "image", "parsed_item", "missing_image", "missing_profile"])
def test_draft_never_rebinds_changed_or_unknown_identity(scene, change):
    session = ManualRecoverySession()
    def fill(dialog):
        dialog.restore_filled_values(FILLED)
        dialog.close()
    assert modal(scene, session, fill) is None
    stats, scope = deepcopy(scene.stats), scene.scope
    resources = stats["recognition_resources"]
    if change == "account": scope = ("other", *scope[1:])
    elif change == "generation": scope = (scope[0], 2, scope[2])
    elif change == "database": scope = (*scope[:2], scope[2].with_name("other.sqlite3"))
    elif change == "dataset": resources = replace(resources, dataset=("other", 1, "fixture"))
    elif change == "stats": resources = replace(resources, stats_sha256="a" * 64)
    elif change == "shape": resources = replace(resources, shape_ids=frozenset({"S2"}))
    elif change == "layout": resources = replace(resources, image_profile=replace(resources.image_profile,
        layout=RecognitionLayout((120, 100), (("sub", (0, 0, 120, 100)),))))
    elif change == "dictionary":
        profile = resources.image_profile
        dictionary = replace(profile.ocr_resources.dictionary, characters=("B",))
        resources = replace(resources, image_profile=replace(profile, ocr_resources=replace(profile.ocr_resources, dictionary=dictionary)))
    elif change == "model":
        profile = resources.image_profile
        files = list(profile.ocr_resources.models.files)
        files[0] = replace(files[0], spec=replace(files[0].spec, sha256="b" * 64))
        resources = replace(resources, image_profile=replace(profile, ocr_resources=replace(profile.ocr_resources,
            models=OcrModelResources(tuple(files)))))
    elif change == "image": stats["pending_manual_items"][0]["image_sha256"] = "c" * 64
    elif change == "parsed_item": stats["pending_manual_items"][0]["item"]["quality"] = "Purple"
    elif change == "missing_image": stats["pending_manual_items"][0]["image_sha256"] = None
    elif change == "missing_profile": resources = replace(resources, image_profile=None)
    stats["recognition_resources"] = resources
    observed = []
    def reopen(dialog):
        observed.append(dialog.filled_values())
        dialog.reject()
    assert modal(scene, session, reopen, stats=stats, scope=scope) is None
    assert observed == [(("MissingAlpha", 0.0),)]


@pytest.mark.parametrize("action", ["cancel", "close", "stale"])
def test_active_modal_is_released_and_invalidated_values_do_not_return(scene, action):
    session = ManualRecoverySession()
    current = [True]
    def invalidate(dialog):
        dialog.restore_filled_values(FILLED)
        if action == "stale":
            current[0] = False
            dialog.accept()
        else:
            getattr(session, action)()
    assert modal(scene, session, invalidate, is_current=lambda: current[0]) is None
    assert not session.has_draft
    current[0] = True
    if action == "close":
        assert complete_pending_manual_items(scene.root, scene.stats, scene.image.parent, session=session, scope=scene.scope) is None
    else:
        observed = []
        def reopen(dialog):
            observed.append(dialog.filled_values())
            dialog.accept()
        assert modal(scene, session, reopen)
        assert observed == [(("MissingAlpha", 0.0),)]


def controller_for(scene):
    from src.features.scanning.controller import ScanningController
    root = scene.image.parent
    account = SimpleNamespace(active_account_id="fixture", screenshot_dir=root, user_config_dir=root,
        user_database_path=root / "user.sqlite3")
    paths = SimpleNamespace(config_dir=root, template_dir=root, equipment_allocation_database_path=root / "static.sqlite3",
        equipment_allocation_asset_root=root / "game_ui")
    context = SimpleNamespace(account=account, generation=1, paths=paths)
    owner = ScanningController(app_context=context, dialog_parent=scene.root, navigate=Mock(),
        work_mode_provider=lambda: WorkMode.LOW, minimize_window=Mock(), restore_window=Mock(), activate_window=Mock(),
        update_inventory_status=Mock(), refresh_home=Mock(), preferences_provider=lambda: {}, save_preferences=Mock(),
        refresh_roles=Mock(), refresh_equipment=Mock(), card_factory=Mock(), equipment_presentation=Mock(), hotkey_manager=Mock())
    owner.btn_run = QPushButton(scene.root)
    owner._scan_dependencies = ScanningDependencies.from_app_context(context)
    owner._pending_parse_only = True
    owner._vision_commit.submit = Mock(return_value=True)
    return owner


@pytest.mark.parametrize("action", ["request_stop", "reset_account_state", "begin_close", "capture_cancel", "reopen"])
def test_actual_controller_owns_modal_stop_and_explicit_frozen_submission(scene, monkeypatch, action):
    from src.features.scanning import workflow
    owner = controller_for(scene)
    if action == "capture_cancel": scene.stats["capture_cancel_token"] = Event()
    owner.btn_run.setEnabled(False)
    before = scene.image.read_bytes()
    info = Mock()
    monkeypatch.setattr(workflow, "_scan_event", Mock())
    monkeypatch.setattr(QMessageBox, "information", info)
    monkeypatch.setattr(owner, "_reload_allocation_filter_settings", Mock())
    monkeypatch.setattr(owner, "_reload_full_scan_preference_widgets", Mock())
    errors = []
    def first():
        dialog = scene.app.activeModalWidget()
        try:
            dialog.restore_filled_values(FILLED)
            if action == "reset_account_state": owner.app_context.generation += 1
            if action == "reopen": dialog.close()
            elif action == "capture_cancel":
                scene.stats["capture_cancel_token"].set()
                dialog.accept()
            else: getattr(owner, action)()
        except Exception as error:
            errors.append(repr(error)); dialog.reject()
    try:
        QTimer.singleShot(0, first)
        owner._on_vision_done(scene.stats)
        assert not errors, errors
        owner._vision_commit.submit.assert_not_called()
        assert scene.app.activeModalWidget() is None
        assert scene.image.read_bytes() == before
        assert not scene.scope[2].exists()
        if action == "reopen":
            assert owner._manual_recovery.has_draft
            assert "再次解析" in info.call_args.args[2]
            observed = []
            def second():
                dialog = scene.app.activeModalWidget()
                observed.append(dialog.filled_values())
                dialog.accept()
            QTimer.singleShot(0, second)
            owner._on_vision_done(scene.stats)
            assert observed == [FILLED]
            dependencies, request = owner._vision_commit.submit.call_args.args
            assert dependencies is owner._scan_dependencies
            assert request.database_path == scene.scope[2]
            assert request.manual_items[0]["sub_stats"]["MissingBeta"] == 23.125
            assert "image_sha256" not in request.manual_items[0]
            assert request.stats["pending_manual_items"] == scene.stats["pending_manual_items"]
        else:
            assert not owner._manual_recovery.has_draft
            info.assert_not_called()  # 程序撤销不在收尾链中再生第二个模态框。
            if action in {"request_stop", "capture_cancel"}:
                assert owner.btn_run.isEnabled() and not owner._pending_parse_only
    finally:
        owner.close()
        scene.app.processEvents()


def test_input_fingerprint_is_local_outer_record_and_missing_source_is_unknown(tmp_path):
    image = tmp_path / "synthetic.png"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(image)
    item = SimpleNamespace(item_type="drive", sub_stats={"A": 1, "B": 2, "C": 3})
    error = RecoverableParseError("synthetic", item, 3)
    record = error.to_record(str(image))
    assert record["image_sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert "image_sha256" not in record["item"]
    assert error.to_record(str(tmp_path / "missing.png"))["image_sha256"] is None


def test_actual_controller_stop_revokes_queued_completion_from_finished_worker(scene, monkeypatch):
    from src.features.scanning import workflow
    owner = controller_for(scene)
    monkeypatch.setattr(workflow.VisionWorkerThread, "start", lambda self: None)
    monkeypatch.setattr(workflow, "_begin_scan_operation", Mock())
    try:
        owner._start_vision_processing(replace_output=True, parse_scope="full")
        worker = owner._vision_worker
        assert not worker.isRunning()
        owner.request_stop()
        assert worker.is_cancelled()
        worker.processing_done.emit(scene.stats)
        scene.app.processEvents()
        owner._vision_commit.submit.assert_not_called()
        assert scene.app.activeModalWidget() is None
        assert not owner._manual_recovery.has_draft
    finally:
        if owner._progress_dlg is not None: owner._progress_dlg.close()
        owner.close()
        scene.app.processEvents()
