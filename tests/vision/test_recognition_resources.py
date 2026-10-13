# 用小型真实 SQLite 和真实任务所有者验证识别资料冻结、取消及原子提交。
import json
import sqlite3
from concurrent.futures import CancelledError
from dataclasses import replace

import cv2
import numpy as np
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from src.features.identification.controller import IdentificationController
from src.features.identification.dependencies import IdentificationDependencies
from src.features.identification.task_controller import IdentificationTaskController
from src.features.scanning.manual_recovery import _stat_pool
from src.integrations.vision.recognition.parser import DriveDataParser
from src.integrations.vision.recognition.shape_recognizer import ShapeRecognizer
from src.models.equipment import Tape
from src.optimizer.scoring import ScoringEngine
from src.services.identification import tasks
from src.services.identification.tasks import IdentificationMatchRequest, IdentificationParsedBatch
from src.services.inventory import vision_snapshot
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
from src.services.recognition import resources as resource_service
from src.services.recognition.resources import (
    RecognitionResourceError, check_recognition_resources, load_recognition_resources,
)
from src.storage.sqlite.static_game_data_dao import SCHEMA_VERSION, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao

NTE_TEST_TIER = "core"


@pytest.fixture
def selected(tmp_path):
    config = tmp_path / "selected-config"
    config.mkdir()
    stats = {"gold_base_values": {"攻击力%": 3.0, "暴击率%": 2.8},
             "tape_main_stats_pool": ["攻击力%"],
             "tape_main_stat_values": {"攻击力%": 123.0}}
    (config / "stats.json").write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8")
    database = tmp_path / "selected-static.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE schema_migration(version INTEGER);
            CREATE TABLE dataset(dataset_id TEXT, importer_version INTEGER, built_at_utc TEXT);
            INSERT INTO dataset VALUES ('selected-dataset', 1, 'fixture-time');
            CREATE TABLE equipment_shape(shape_id TEXT, cell_count INTEGER,
                first_grid_delta_x INTEGER, first_grid_delta_y INTEGER, source_row_id TEXT);
            INSERT INTO equipment_shape VALUES ('EquipmentGeometry_Hen2', 2, 0, 0, 'shape-row');
            CREATE TABLE equipment_shape_cell(shape_id TEXT, ordinal INTEGER, x INTEGER, y INTEGER);
            INSERT INTO equipment_shape_cell VALUES ('EquipmentGeometry_Hen2', 0, 0, 0);
            CREATE TABLE equipment_suit(suit_id TEXT, name_zh TEXT, name_text_table TEXT,
                name_text_key TEXT, icon_path TEXT, source_row_id TEXT);
            INSERT INTO equipment_suit VALUES ('fixture-suit', '夹具套装', '', '', '', 'suit-row');
            CREATE TABLE equipment_suit_required_shape(suit_id TEXT, ordinal INTEGER, shape_id TEXT);
            CREATE TABLE equipment_suit_effect(suit_id TEXT, required_count INTEGER,
                modify_pack_id TEXT, buff_object_path TEXT, description_zh TEXT,
                description_text_table TEXT, description_text_key TEXT,
                reapply_after_revive INTEGER, source_row_id TEXT);
        """)
        db.execute("INSERT INTO schema_migration VALUES (?)", (SCHEMA_VERSION,))
    db.close()  # sqlite3 的 with 仅结束事务；夹具必须显式关闭，避免延迟 checkpoint。
    templates = config / "templates"
    templates.mkdir()
    ok, encoded = cv2.imencode(".png", np.arange(64, dtype=np.uint8).reshape(8, 8))
    assert ok
    encoded.tofile(str(templates / "H_2.png"))
    return config, database


def freeze(selected):
    return load_recognition_resources(*selected)


def freeze_ocr(selected):
    from tests.vision.test_ocr_runtime import freeze_micro_resources
    return freeze_micro_resources(selected[0] / 'ocr-fixture')


def freeze_image(selected):
    from src.integrations.vision.recognition.config import ScannerConfig
    from src.integrations.vision.recognition.image_resources import RecognitionImageProfile
    resources = freeze(selected)
    shape = ShapeRecognizer(str(selected[0] / "templates"), valid_shape_ids=resources.shape_ids)
    shape.require_complete_templates()
    return replace(resources, image_profile=RecognitionImageProfile(ScannerConfig.freeze(), shape.template_files, freeze_ocr(selected)))


def mutate_stats(config):
    path = config / "stats.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tape_main_stat_values"]["攻击力%"] = 321.0
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def tape_row():
    return {"item_type": "tape", "uid": "fixture-tape", "quality": "Gold", "area": 15,
            "set_name": "夹具套装", "main_stats": "攻击力%", "sub_stats": {"暴击率%": 2.8}}


def read_inventory(path):
    with UserDataDao(path) as dao:
        current = dao.current_inventory_snapshot_id()
        rows = dao.list_inventory_items(current)
        count = dao._db().execute("SELECT COUNT(*) FROM inventory_snapshot").fetchone()[0]
        return current, rows, count


def baseline_user(path):
    with UserDataDao(path, account_id="recognition-fixture") as dao:
        return dao.import_inventory_snapshot({"complete": True, "item_count": 0, "items": []},
                                             source="vision")


def test_frozen_catalog_is_private_and_saved_main_uses_selected_rules(selected, tmp_path):
    resources = freeze(selected)
    first = resources.stat_catalog()
    first.tape_main_values["攻击力%"] = 999.0
    assert resources.stat_catalog().tape_main_values["攻击力%"] == 123.0
    path = tmp_path / "user.sqlite3"
    baseline_user(path)
    snapshot = vision_snapshot.import_vision_inventory(path, [tape_row()], resources=resources)
    current, rows, count = read_inventory(path)
    assert current == snapshot and count == 2
    assert rows[0]["main_stats"][0]["value"] == pytest.approx(1.23)
    assert rows[0]["suit_id"] == "fixture-suit"
    identity = resources.public_identity()
    assert identity["dataset_id"] == "selected-dataset" and identity["input_language"] == "zh-CN"
    assert not any(isinstance(value, Path) or str(selected[0]) in str(value)
                   for value in identity.values())


@pytest.mark.parametrize("change", ["stats", "dataset"])
def test_load_rejects_sources_changed_between_queries(selected, monkeypatch, change):
    original = StaticGameDataDao.list_suits
    def changed(dao):
        result = original(dao)
        if change == "stats":
            mutate_stats(selected[0])
        else:
            with sqlite3.connect(selected[1]) as writer:
                writer.execute("UPDATE dataset SET dataset_id='changed-dataset'")
        return result
    monkeypatch.setattr(StaticGameDataDao, "list_suits", changed)
    with pytest.raises(RecognitionResourceError):
        freeze(selected)


def test_dao_multiple_queries_share_read_transaction(selected):
    with StaticGameDataDao(selected[1], reuse_connection=False) as dao:
        with dao.read_consistent_state():
            assert dao.dataset_metadata()["dataset_id"] == "selected-dataset"
            with sqlite3.connect(selected[1]) as writer:
                writer.execute("UPDATE dataset SET dataset_id='new-dataset'")
                writer.execute("UPDATE equipment_suit SET name_zh='新套装'")
            assert dao.dataset_metadata()["dataset_id"] == "selected-dataset"
            assert dao.list_suits()[0]["name_zh"] == "夹具套装"
        assert dao.dataset_metadata()["dataset_id"] == "new-dataset"


def test_sha_detects_stats_changes_even_if_probe_is_unchanged(selected, monkeypatch):
    resources = freeze(selected)
    mutate_stats(selected[0])
    monkeypatch.setattr(resource_service, "recognition_file_versions",
                        lambda *args: resources.file_versions)
    with pytest.raises(RecognitionResourceError, match="词条规则"):
        check_recognition_resources(resources)


@pytest.mark.parametrize("change", ["metadata", "file"])
def test_identity_check_reads_actual_database_and_rejects_changed_file(selected, monkeypatch, change):
    resources = freeze(selected)
    if change == "metadata":
        with sqlite3.connect(selected[1]) as writer:
            writer.execute("UPDATE dataset SET dataset_id='changed-dataset'")
        monkeypatch.setattr(resource_service, "recognition_file_versions",
                            lambda *args: resources.file_versions)
    else:
        mutate_stats(selected[0])
    with pytest.raises(RecognitionResourceError):
        check_recognition_resources(resources)


def test_injected_parser_shape_scoring_and_manual_pool_do_not_reread_defaults(selected, monkeypatch):
    resources = freeze(selected)
    def forbidden(*args, **kwargs):
        raise AssertionError("injected consumer reread defaults")
    monkeypatch.setattr(DriveDataParser, "_load_sets_from_json", forbidden)
    monkeypatch.setattr(DriveDataParser, "_load_stat_catalog", forbidden)
    monkeypatch.setattr(ShapeRecognizer, "_load_valid_shape_ids", forbidden)
    monkeypatch.setattr(ShapeRecognizer, "_load_templates", lambda self: None)
    monkeypatch.setattr(ScoringEngine, "_load_stats", forbidden)
    parser = DriveDataParser(selected[0], stat_catalog=resources.stat_catalog(),
                             suit_names=[name for _, name in resources.suits])
    shape = ShapeRecognizer(valid_shape_ids=resources.shape_ids)
    scoring = ScoringEngine(selected[0], roles_db={}, stat_catalog=resources.stat_catalog())
    mutate_stats(selected[0])
    assert parser.stat_catalog.tape_main_values["攻击力%"] == 123.0
    assert shape.valid_shape_ids == {"H_2"}
    assert scoring.stat_catalog.tape_main_values["攻击力%"] == 123.0
    assert _stat_pool(selected[0], resources) == ["攻击力%", "暴击率%"]


def test_image_to_confirmation_to_match_keeps_resource_and_rejects_update(selected, tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    resources = freeze(selected)
    deps = IdentificationDependencies("fixture", 1, tmp_path, tmp_path/"shots", selected[0],
                                     tmp_path/"user.sqlite3", tmp_path/"templates", selected[1])
    owner = IdentificationTaskController(lambda: deps)
    task = owner.begin(deps)
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="夹具套装",
                main_stats="攻击力%", sub_stats={})
    try:
        owner._deliver(task, "parse", IdentificationParsedBatch((item,), resources))
        mutate_stats(selected[0])  # 真实确认阶段后更新规则，而不是重绑最新资料。
        form = SimpleNamespace(_identify_tasks=owner, _identify_task=task)
        assert IdentificationController._get_tape_main_stats_pool(form) == ["攻击力%"]
        request = IdentificationMatchRequest.freeze(config_dir=selected[0],
            user_database_path=deps.user_database_path, static_database_path=selected[1], items=[item])
        seen = []
        monkeypatch.setattr(owner, "_submit", lambda tag, stage, read: seen.append(read))
        monkeypatch.setattr(tasks.EquipmentIdentificationService, "from_paths",
                            lambda **kwargs: pytest.fail("stale matcher was constructed"))
        owner.submit_matches(task, request)
        with pytest.raises(RecognitionResourceError):
            seen[0]()
        owner.cancel()
        assert owner.resources_for(task) is None
        assert item.main_stats == "攻击力%"  # 表单副本没有被后台规则重解释。
    finally:
        owner.close()
        app.processEvents()


@pytest.mark.parametrize("reject", ["resource", "cancel", "generation"])
def test_final_authorization_rolls_back_staged_snapshot_and_pointer(selected, tmp_path, monkeypatch, reject):
    resources = freeze_image(selected)
    path = tmp_path / "user.sqlite3"
    previous = baseline_user(path)
    staged = []
    class ObservedDao(UserDataDao):
        def import_inventory_snapshot(self, snapshot, **kwargs):
            guard = kwargs["before_commit"]
            def observed_guard():
                assert self._db().in_transaction
                staged.append(self._db().execute("SELECT COUNT(*) FROM inventory_snapshot").fetchone()[0])
                if reject == "resource":
                    mutate_stats(selected[0])
                guard()
            kwargs["before_commit"] = observed_guard
            return super().import_inventory_snapshot(snapshot, **kwargs)
    monkeypatch.setattr(vision_snapshot, "UserDataDao", ObservedDao)
    calls = [0]
    def current():
        calls[0] += 1
        return calls[0] < 3
    stats = {"vision_items": [tape_row()], "parse_scope": "full", "total_count": 1,
             "failed_count": 0, "recognition_resources": resources}
    if reject == "cancel":
        args = {"cancel_check": lambda: not current()}
    elif reject == "generation":
        args = {"result_is_current": current}
    else:
        args = {}
    with pytest.raises((RecognitionResourceError, CancelledError)) as rejected:
        commit_completed_vision_inventory(path, stats, [], **args)
    assert staged == [2], str(rejected.value)
    assert read_inventory(path) == (previous, [], 1)


def test_change_after_final_authorization_may_finish_frozen_complete_snapshot(selected, tmp_path, monkeypatch):
    resources = freeze(selected)
    path = tmp_path / "user.sqlite3"
    baseline_user(path)
    class AfterAuthorizationDao(UserDataDao):
        def import_inventory_snapshot(self, snapshot, **kwargs):
            guard = kwargs["before_commit"]
            def after_guard():
                guard()
                mutate_stats(selected[0])  # 授权点不是对外部文件的事务锁。
            kwargs["before_commit"] = after_guard
            return super().import_inventory_snapshot(snapshot, **kwargs)
    monkeypatch.setattr(vision_snapshot, "UserDataDao", AfterAuthorizationDao)
    snapshot = vision_snapshot.import_vision_inventory(path, [tape_row()], resources=resources)
    current, rows, count = read_inventory(path)
    assert current == snapshot and count == 2
    assert rows[0]["main_stats"][0]["value"] == pytest.approx(1.23)


def test_missing_resource_and_incomplete_results_preserve_inventory(selected, tmp_path):
    path = tmp_path / "user.sqlite3"
    previous = baseline_user(path)
    stats = {"vision_items": [tape_row()], "parse_scope": "full", "total_count": 1,
             "failed_count": 0}
    with pytest.raises(RecognitionResourceError):
        commit_completed_vision_inventory(path, stats, [])
    assert read_inventory(path) == (previous, [], 1)


def test_independent_match_freezes_once_and_copies_form_items(selected, tmp_path, monkeypatch):
    resources = freeze(selected)
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="夹具套装",
                main_stats="攻击力%", sub_stats={})
    request = IdentificationMatchRequest.freeze(config_dir=selected[0],
        user_database_path=tmp_path/"user.sqlite3", static_database_path=selected[1], items=[item])
    loads = []
    monkeypatch.setattr(tasks, "load_recognition_resources",
                        lambda *args: (loads.append(args), resources)[1])
    def matcher(**kwargs):
        assert kwargs["resources"] is resources
        def identify(candidate):
            candidate.set_name = "changed privately"
            return {"fixture": True}
        return SimpleNamespace(identify_item=identify)
    monkeypatch.setattr(tasks.EquipmentIdentificationService, "from_paths", matcher)
    assert tasks.match_identification_items(request) == [{"fixture": True}]
    assert len(loads) == 1 and item.set_name == "夹具套装"


def test_cached_old_actual_dao_is_rejected_before_role_or_scoring_reads(selected, tmp_path, monkeypatch):
    from contextlib import closing
    from src.integrations import static_catalog_release as release_reader
    from src.services import legacy_allocation_static_catalog as legacy
    from tests.allocation.static_release_helpers import publish_test_manifest

    resources = freeze(selected)
    # 正式发行须先关连接并离开 WAL；旧 DAO 的错误身份在当前 reader 边界注入。
    with closing(sqlite3.connect(selected[1])) as database:
        database.execute("PRAGMA journal_mode=DELETE")
    publish_test_manifest(selected[1])
    old_database = tmp_path / "old-connection.sqlite3"
    with closing(sqlite3.connect(selected[1])) as source, closing(sqlite3.connect(old_database)) as target:
        source.backup(target)
        target.execute("UPDATE dataset SET dataset_id='stale-cached-dataset'")
        target.commit()
    calls = []
    def stale_dao(requested_path, **options):
        calls.append((requested_path, options["reuse_connection"]))
        return StaticGameDataDao(old_database, reuse_connection=False)
    def forbidden(*args, **kwargs):
        pytest.fail("role/scoring read preceded actual database identity check")
    monkeypatch.setattr(release_reader, "StaticGameDataDao", stale_dao)
    monkeypatch.setattr(StaticGameDataDao, "list_role_template_characters", forbidden)
    monkeypatch.setattr(ScoringEngine, "_scoring_property_name", forbidden)
    with pytest.raises(release_reader.StaticCatalogReleaseError) as error:
        legacy.build_legacy_allocation_static_catalog(config_dir=selected[0],
            static_database_path=selected[1], expected_dataset=resources.dataset,
            stat_catalog=resources.stat_catalog())
    assert error.value.code == "release_metadata_mismatch"
    assert calls == [(selected[1], False)]


@pytest.mark.parametrize("mode", ["complete", "partial", "all_failed", "empty"])
def test_facade_actual_batch_rejects_partial_and_saves_complete(selected, tmp_path, monkeypatch, mode):
    from unittest.mock import Mock
    from src.app.facade import NTEAppFacade
    from src.integrations.vision.recognition import batch_processor as batch
    from src.services.inventory.visual_scan_commit import IncompleteVisionScanError
    shots = tmp_path / "shots"
    shots.mkdir()
    for name in (() if mode == "empty" else ("good.png", "second.png")):
        (shots/name).write_bytes(b"synthetic image; OCR is replaced")
    database = tmp_path / "user.sqlite3"
    previous = baseline_user(database)
    monkeypatch.setattr(batch, "OCREngine", Mock(return_value=Mock(runtime_resources=freeze_ocr(selected))))
    def process(self, path, filename, **kwargs):
        if mode == "all_failed" or mode == "partial" and filename == "second.png":
            raise ValueError("synthetic single-image failure")
        item = Tape(uid=filename, quality="Gold", area=15, set_name="夹具套装",
                    main_stats="攻击力%", sub_stats={"暴击率%": 2.8})
        self.inventory.append(item)
        return item, True
    monkeypatch.setattr(batch.BatchProcessor, "process_image_file", process)
    facade = NTEAppFacade(config_dir=selected[0], user_config_dir=tmp_path,
        user_database_path=database, screenshot_dir=shots, allocation_static_database_path=selected[1])
    if mode in {"partial", "all_failed"}:
        with pytest.raises(IncompleteVisionScanError):
            facade.execute_vision_processing()
        assert read_inventory(database) == (previous, [], 1)
    elif mode == "empty":
        facade.execute_vision_processing()
        assert read_inventory(database) == (previous, [], 1)
    else:
        facade.execute_vision_processing()
        current, rows, count = read_inventory(database)
        assert current != previous and len(rows) == 2 and count == 2
        assert all(row["main_stats"][0]["value"] == pytest.approx(1.23) for row in rows)
    if mode != "empty":
        assert (shots/"good.png").read_bytes() == b"synthetic image; OCR is replaced"
