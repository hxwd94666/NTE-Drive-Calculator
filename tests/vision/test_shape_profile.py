# 验证形状模板的只读缓存、截图布局冻结及图片资源变化的事务拒绝。
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from src.integrations.vision.recognition import batch_processor, screenshot_parser, identification_parser
from src.integrations.vision.recognition.config import ScannerConfig
from src.integrations.vision.recognition.image_resources import RecognitionImageProfile, ShapeTemplateFile
from src.integrations.vision.recognition.shape_recognizer import ShapeRecognizer
from src.models.equipment import Drive, Tape
from src.services.identification import tasks
from src.services.inventory import vision_snapshot
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
from src.services.recognition.resources import RecognitionResourceError, check_recognition_resources
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.vision.test_recognition_resources import (
    baseline_user, freeze, freeze_image, freeze_ocr, read_inventory, selected as selected, tape_row,
)

NTE_TEST_TIER = "core"


def png(path, pixels):
    ok, encoded = cv2.imencode(".png", pixels)
    assert ok
    encoded.tofile(str(path))


def test_immutable_lru_reuses_exact_resizes_and_keeps_budget(selected, monkeypatch):
    shape = ShapeRecognizer(str(selected[0]/"templates"), valid_shape_ids=frozenset({"H_2"}))
    pixels = shape.templates["H_2"]
    with pytest.raises(TypeError):
        shape.templates["H_2"] = np.zeros_like(pixels)
    with pytest.raises(ValueError):
        pixels.setflags(write=True)
    calls = []
    original = cv2.resize
    monkeypatch.setattr(cv2, "resize", lambda *args, **kwargs: (calls.append(args[1]), original(*args, **kwargs))[1])
    target = np.zeros((1800, 1800), dtype=np.uint8)
    first = shape.resized_template("H_2", (1400, 1400), target=target)
    assert shape.resized_template("H_2", (1400, 1400), target=target) is first
    second = shape.resized_template("H_2", (1300, 1300), target=target)
    assert shape.resized_template("H_2", (1400, 1400), target=target) is first  # 提升 LRU 优先级。
    shape.resized_template("H_2", (1200, 1200), target=target)
    assert shape.cached_pixel_bytes == 1400**2 + 1200**2
    assert np.array_equal(shape.resized_template("H_2", (1300, 1300), target=target), second)
    assert len(calls) == 4 and shape.cached_pixel_bytes <= 4*1024*1024
    with pytest.raises(ValueError):
        first.setflags(write=True)
    big = np.zeros((2050, 2050), dtype=np.uint8)
    size_before = shape.cached_pixel_bytes
    assert np.array_equal(shape.resized_template("H_2", (2050, 2050), target=big),
                          shape.resized_template("H_2", (2050, 2050), target=big))
    assert len(calls) == 6 and shape.cached_pixel_bytes == size_before
    for invalid in [(0, 4), (-1, 4), (1.5, 4), (True, 4), (1801, 4)]:
        with pytest.raises(ValueError):
            shape.resized_template("H_2", invalid, target=target)
    assert len(calls) == 6


def test_reload_is_atomic_rejects_alias_conflict_and_clears_old_sizes(selected):
    root = selected[0]/"templates"
    shape = ShapeRecognizer(str(root), valid_shape_ids=frozenset({"H_2"}))
    target = np.zeros((10, 12), dtype=np.uint8)
    cached = shape.resized_template("H_2", (12, 10), target=target)
    pool, evidence = shape.templates, shape.template_files
    png(root/"hen2.png", np.ones((8, 8), dtype=np.uint8))
    with pytest.raises(RecognitionResourceError, match="重复"):
        shape._load_templates()
    assert shape.templates is pool and shape.template_files is evidence
    assert shape.resized_template("H_2", (12, 10), target=target) is cached
    (root/"hen2.png").unlink()
    (root/"H_2.png").unlink()
    with pytest.raises(RecognitionResourceError, match="重载不完整"):
        shape._load_templates()
    assert shape.templates is pool and shape.cached_pixel_bytes == 120
    (root/"H_2.png").write_bytes(b"corrupt synthetic template")
    with pytest.raises(RecognitionResourceError, match="解码"):
        shape._load_templates()
    assert shape.templates is pool and shape.cached_pixel_bytes == 120
    png(root/"H_2.png", np.ones((8, 8), dtype=np.uint8))
    shape._load_templates()
    assert shape.cached_pixel_bytes == 0 and shape.templates is not pool
    assert np.all(shape.resized_template("H_2", (12, 10), target=target) == 1)


def test_missing_template_directory_is_read_only(tmp_path):
    missing = tmp_path/"missing"
    with pytest.raises(RecognitionResourceError, match="目录缺失"):
        ShapeRecognizer(str(missing), valid_shape_ids=frozenset({"H_2"}))
    assert not missing.exists()


def test_template_hash_belongs_to_decoded_bytes_and_detects_changed_read(selected, monkeypatch):
    path = selected[0]/"templates"/"H_2.png"
    payload = path.read_bytes()
    original = cv2.imdecode
    seen = []
    def decode(encoded, flags):
        seen.append(encoded.tobytes())
        path.write_bytes(payload + b"changed after read")
        return original(encoded, flags)
    monkeypatch.setattr(cv2, "imdecode", decode)
    shape = ShapeRecognizer(str(path.parent), valid_shape_ids=frozenset({"H_2"}))
    assert seen == [payload]  # 模板矩阵来自已哈希的载荷，不是第二次路径读取。
    profile = RecognitionImageProfile(ScannerConfig.freeze(), shape.template_files)
    with pytest.raises(RecognitionResourceError):
        profile.verify()
    monkeypatch.undo()
    original_read = Path.read_bytes
    def read_and_change(candidate):
        content = original_read(candidate)
        if candidate == path:
            candidate.write_bytes(content + b"changed during read")
        return content
    monkeypatch.setattr(Path, "read_bytes", read_and_change)
    with pytest.raises(RecognitionResourceError, match="读取期间"):
        ShapeTemplateFile.read("H_2", path)


@pytest.mark.parametrize("dimensions", [(1920, 1080), (2560, 1440), (3840, 2160), (2560, 1600)])
def test_both_parsers_use_frozen_layout_and_private_regions(selected, monkeypatch, dimensions):
    resources = freeze(selected)
    monkeypatch.setattr(batch_processor, "OCREngine", Mock(return_value=Mock(runtime_resources=freeze_ocr(selected))))
    processor = batch_processor.BatchProcessor(input_dir=str(selected[0]/"shots"), resources=resources)
    expected = ScannerConfig.get_region_profiles(*dimensions)
    assert processor.layout.get_region_profiles(*dimensions) == expected
    projection = processor.layout.get_region_profiles(*dimensions)
    projection[0][1]["drive_sub_stats"] = (0, 0, 1, 1)
    monkeypatch.setattr(ScannerConfig, "REGIONS_2K", {"wrong": (0, 0, 1, 1)})
    assert processor.layout.get_region_profiles(*dimensions) == expected
    seen = []
    img = np.zeros((dimensions[1], dimensions[0], 3), dtype=np.uint8)
    monkeypatch.setattr(screenshot_parser, "imread_unicode", lambda _path: img)
    monkeypatch.setattr(screenshot_parser, "crop_window_border_from_image", lambda image: image)
    def classify(owner, image, profiles):
        assert owner is processor and image is img and profiles == expected
        name, regions = profiles[0]
        return "drive", name, regions, {"shape_id": "H_2", "confidence": 1.0}, ""
    monkeypatch.setattr(screenshot_parser, "classify_item", classify)
    item = Drive(uid="fixture", quality="Gold", area=2, shape_id="H_2", main_stats={"攻击力": 10.0, "攻击力%": 3.0}, sub_stats={"攻击力%": 3})
    processor.ocr_engine.extract_text = lambda crop: (seen.append(crop.shape), ["攻击力 3%"]) [1]
    processor.parser = SimpleNamespace(synthesize_drive=lambda *_args: item)
    monkeypatch.setattr(screenshot_parser, "_prefer_complete_or_reward_item", lambda _proc, _img, result, _name: result)
    assert screenshot_parser.process_single_image(processor, "synthetic.png") is item
    assert identification_parser.process_identify_standard_forced(processor, img, "drive", "H_2") is item
    x1, y1, x2, y2 = expected[0][1]["drive_sub_stats"]
    assert seen == [(y2-y1, x2-x1, 3)]*2


def test_image_parse_delivers_profile_and_confirmation_rejects_changed_template(selected, tmp_path, monkeypatch):
    monkeypatch.setattr(batch_processor, "OCREngine", Mock(return_value=Mock(runtime_resources=freeze_ocr(selected))))
    item = Tape(uid="fixture", quality="Gold", area=15, set_name="夹具套装", main_stats="攻击力%", sub_stats={})
    monkeypatch.setattr(batch_processor.BatchProcessor, "parse_identify_items", lambda *_args, **_kwargs: [item])
    request = tasks.IdentificationImageRequest(tmp_path/"shots", tmp_path/"unused.json", selected[0],
                    (tasks.IdentificationImageJob(tmp_path/"synthetic.png"),), selected[1])
    parsed = tasks.parse_identification_images(request)
    assert parsed.resources.image_profile.template_files[0].shape_id == "H_2"
    identity = parsed.resources.public_identity()
    assert str(selected[0]) not in str(identity) and "layout_sha256" in identity["image_profile"]
    (selected[0]/"templates"/"H_2.png").write_bytes(b"template changed after confirmation")
    monkeypatch.setattr(tasks.EquipmentIdentificationService, "from_paths", lambda **_kwargs: pytest.fail("stale image matcher constructed"))
    match = tasks.IdentificationMatchRequest.freeze(config_dir=selected[0], user_database_path=tmp_path/"user.sqlite3",
                         static_database_path=selected[1], items=parsed.items, resources=parsed.resources)
    with pytest.raises(RecognitionResourceError):
        tasks.match_identification_items(match)


def test_image_profile_required_for_full_commit_and_final_guard_keeps_old_inventory(selected, tmp_path, monkeypatch):
    database = tmp_path/"user.sqlite3"
    previous = baseline_user(database)
    stats = {"parse_scope": "full", "total_count": 1, "failed_count": 0, "vision_items": [tape_row()],
             "recognition_resources": freeze(selected)}
    with pytest.raises(RecognitionResourceError):
        commit_completed_vision_inventory(database, stats, [])
    assert read_inventory(database) == (previous, [], 1)
    resources = freeze_image(selected)
    check_recognition_resources(resources)
    stats["recognition_resources"] = replace(resources, image_profile=replace(resources.image_profile, template_files=()))
    with pytest.raises(RecognitionResourceError):
        commit_completed_vision_inventory(database, stats, [])
    assert read_inventory(database) == (previous, [], 1)
    stats["recognition_resources"] = resources
    staged = []
    class ObservedDao(UserDataDao):
        def import_inventory_snapshot(self, snapshot, **kwargs):
            guard = kwargs["before_commit"]
            def changed_template():
                assert self._db().in_transaction
                staged.append(self._db().execute("SELECT COUNT(*) FROM inventory_snapshot").fetchone()[0])
                (selected[0]/"templates"/"H_2.png").write_bytes(b"changed after staging")
                guard()
            return super().import_inventory_snapshot(snapshot, **{**kwargs, "before_commit": changed_template})
    monkeypatch.setattr(vision_snapshot, "UserDataDao", ObservedDao)
    with pytest.raises(RecognitionResourceError):
        commit_completed_vision_inventory(database, stats, [])
    assert staged == [2] and read_inventory(database) == (previous, [], 1)
