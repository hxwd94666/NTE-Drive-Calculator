# 测试全量扫描与截图解析的流水线执行逻辑。
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


class StreamingScanPipelineTests(unittest.TestCase):
    def test_gamepad_worker_waits_for_post_action_focus_ack(self):
        from src.app import workers

        worker = workers.GamepadScanParseWorkerThread(total_drives=1)
        events = []
        sleeps = []
        original_sleep = workers.time.sleep

        def on_ready():
            events.append("ready")
            worker.acknowledge_post_actions_ready()

        worker.post_actions_ready.connect(on_ready)
        workers.time.sleep = lambda seconds: sleeps.append(seconds)
        try:
            worker._notify_post_actions_ready()
        finally:
            workers.time.sleep = original_sleep

        self.assertEqual(["ready"], events)
        self.assertEqual([1.0], sleeps)

    def _state_button_image(self, active=None):
        from src.features.scanning import streaming_pipeline

        img = np.full((1440, 2560, 3), 24, dtype=np.uint8)
        centers = {
            "discarded": streaming_pipeline.TRASH_BUTTON_CENTER,
            "locked": streaming_pipeline.LOCK_BUTTON_CENTER,
        }
        for state, center in centers.items():
            cx = int(round(img.shape[1] * center[0]))
            cy = int(round(img.shape[0] * center[1]))
            value = 188 if state == active else 80
            img[cy - 15 : cy + 15, cx - 15 : cx + 15] = value
        return img

    def test_equipment_state_uses_right_panel_action_buttons(self):
        from src.features.scanning.streaming_pipeline import _right_panel_button_state_from_image

        self.assertEqual("normal", _right_panel_button_state_from_image(self._state_button_image()))
        self.assertEqual("locked", _right_panel_button_state_from_image(self._state_button_image("locked")))
        self.assertEqual("discarded", _right_panel_button_state_from_image(self._state_button_image("discarded")))

    def test_post_action_thresholds_include_equal_grade(self):
        from src.features.scanning.post_actions import default_post_action_config, target_state_for_item

        class FakeScoring:
            roles_db = {"A": {}, "B": {}}

            def get_grade_tag(self, score, _area):
                return {1.0: "SS", 2.0: "SSS"}.get(float(score), "D")

        item = SimpleNamespace(quality="Gold", area=1, role_scores={"A": 1.0, "B": 1.0})
        config = default_post_action_config()
        config["discard"]["enabled"] = True
        config["discard"]["grade"] = "SS"
        self.assertEqual("discarded", target_state_for_item(item, "normal", config, FakeScoring()))

        item.role_scores = {"A": 2.0, "B": 1.0}
        config = default_post_action_config()
        config["lock"]["enabled"] = True
        config["lock"]["grade"] = "SSS"
        self.assertEqual("locked", target_state_for_item(item, "normal", config, FakeScoring()))

    def test_post_action_type_range_filters_default_drive_shapes_and_tape_sets(self):
        from src.features.scanning.post_actions import default_post_action_config, target_state_for_item
        from src.models.equipment import Drive, Tape

        class FakeScoring:
            roles_db = {"A": {}}

            def get_grade_tag(self, score, _area):
                return "SSS" if float(score) >= 2.0 else "D"

        config = default_post_action_config()
        config["lock"]["enabled"] = True
        config["lock"]["grade"] = "SSS"

        keep_drive = Drive(
            uid="keep-drive",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 2.0},
        )
        excluded_drive = Drive(
            uid="excluded-drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={},
            role_scores={"A": 2.0},
        )
        keep_tape = Tape(
            uid="keep-tape",
            quality="Gold",
            area=15,
            set_name="森林萤火之心",
            main_stats="攻击力",
            sub_stats={},
            role_scores={"A": 2.0},
        )
        excluded_tape = Tape(
            uid="excluded-tape",
            quality="Gold",
            area=15,
            set_name="音速蓝刺猬",
            main_stats="攻击力",
            sub_stats={},
            role_scores={"A": 2.0},
        )

        self.assertEqual("locked", target_state_for_item(keep_drive, "normal", config, FakeScoring()))
        self.assertEqual("normal", target_state_for_item(excluded_drive, "normal", config, FakeScoring()))
        self.assertEqual("locked", target_state_for_item(keep_tape, "normal", config, FakeScoring()))
        self.assertEqual("normal", target_state_for_item(excluded_tape, "normal", config, FakeScoring()))

    def test_parser_consumes_first_capture_before_scan_finishes(self):
        from src.features.scanning.streaming_pipeline import run_streaming_scan_parse

        events = []

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.committed = False

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                self.commit_on_complete = commit_on_complete
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    events.append(f"capture:{index}")
                    on_capture(str(path), index, total_drives)
                    if index == 1:
                        deadline = time.time() + 1.0
                        while "parse:raw_drive_0001.png" not in events and time.time() < deadline:
                            time.sleep(0.001)
                    events.append(f"scan_after_callback:{index}")
                events.append("scan_done")
                return total_drives

            def _commit_temp_output(self):
                self.committed = True
                events.append("commit")

        class FakeProcessor:
            def __init__(self):
                self.inventory = []
                self.exported = False

            def process_image_file(self, image_path, filename, **_kwargs):
                events.append(f"parse:{filename}")
                self.inventory.append({"filename": filename})
                return SimpleNamespace(item_type="drive"), True

            def _export_to_json(self):
                self.exported = True
                events.append("export")

        with tempfile.TemporaryDirectory() as tmp:
            scanner = FakeScanner(Path(tmp))
            processor = FakeProcessor()

            stats = run_streaming_scan_parse(scanner, processor, total_drives=2)

        self.assertLess(events.index("parse:raw_drive_0001.png"), events.index("scan_done"))
        self.assertEqual(False, scanner.commit_on_complete)
        self.assertTrue(scanner.committed)
        self.assertTrue(processor.exported)
        self.assertEqual(2, stats["success_count"])
        self.assertEqual(0, stats["failed_count"])
        self.assertEqual("full", stats["parse_scope"])

    def test_auto_discard_marks_low_score_drive_indexes_after_scoring(self):
        from src.features.scanning.streaming_pipeline import run_streaming_scan_parse
        from src.models.equipment import Drive

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.marked = []

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    on_capture(str(path), index, total_drives)
                return total_drives

            def _commit_temp_output(self):
                pass

            def mark_discard_by_indexes(self, total_drives, target_indexes):
                self.marked.append((total_drives, list(target_indexes)))
                return len(target_indexes)

        class FakeProcessor:
            def __init__(self, items):
                self.items = items
                self.inventory = []
                self.exported_scores = []

            def process_image_file(self, _image_path, filename, **_kwargs):
                index = int(filename.removeprefix("raw_drive_").removesuffix(".png"))
                item = self.items[index - 1]
                self.inventory.append(item)
                return item, True

            def _export_to_json(self):
                self.exported_scores = [item.max_score for item in self.inventory]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "roles.json").write_text(
                json.dumps({"A": {"weights": {"Good": 1.0}, "default_set": "Set"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"Good": 1.0, "Bad": 1.0},
                        "main_only_keywords": [],
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            items = [
                Drive(
                    uid="low",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Bad": 1.0},
                ),
                Drive(
                    uid="high",
                    quality="Gold",
                    area=1,
                    shape_id="S2",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Good": 1.0},
                ),
            ]
            scanner = FakeScanner(root)
            processor = FakeProcessor(items)

            stats = run_streaming_scan_parse(
                scanner,
                processor,
                total_drives=2,
                auto_discard_grade="A",
                config_dir=config_dir,
            )

        self.assertEqual([(2, [1])], scanner.marked)
        self.assertEqual(1, stats["discard_target_count"])
        self.assertEqual(1, stats["discard_marked_count"])
        self.assertGreater(processor.exported_scores[1], processor.exported_scores[0])

    def test_auto_discard_skips_locked_drive_indexes(self):
        from src.features.scanning import streaming_pipeline
        from src.models.equipment import Drive

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.marked = []

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    on_capture(str(path), index, total_drives)
                return total_drives

            def _commit_temp_output(self):
                pass

            def mark_discard_by_indexes(self, total_drives, target_indexes):
                self.marked.append((total_drives, list(target_indexes)))
                return len(target_indexes)

        class FakeProcessor:
            def __init__(self, items):
                self.items = items
                self.inventory = []

            def process_image_file(self, _image_path, filename, **_kwargs):
                index = int(filename.removeprefix("raw_drive_").removesuffix(".png"))
                item = self.items[index - 1]
                self.inventory.append(item)
                return item, True

            def _export_to_json(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "roles.json").write_text(
                json.dumps({"A": {"weights": {"Good": 1.0}, "default_set": "Set"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"Bad": 1.0},
                        "main_only_keywords": [],
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            items = [
                Drive(
                    uid="locked-low",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Bad": 1.0},
                ),
                Drive(
                    uid="unlocked-low",
                    quality="Gold",
                    area=1,
                    shape_id="S2",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Bad": 1.0},
                ),
            ]
            scanner = FakeScanner(root)
            processor = FakeProcessor(items)
            original_detector = streaming_pipeline._drive_screenshot_is_locked
            streaming_pipeline._drive_screenshot_is_locked = lambda path: str(path).endswith("raw_drive_0001.png")
            try:
                stats = streaming_pipeline.run_streaming_scan_parse(
                    scanner,
                    processor,
                    total_drives=2,
                    auto_discard_grade="A",
                    config_dir=config_dir,
                )
            finally:
                streaming_pipeline._drive_screenshot_is_locked = original_detector

        self.assertEqual([(2, [2])], scanner.marked)
        self.assertEqual(1, stats["discard_target_count"])
        self.assertEqual(1, stats["discard_marked_count"])

    def test_auto_discard_unlock_mode_keeps_locked_drive_targets(self):
        from src.features.scanning import streaming_pipeline
        from src.models.equipment import Drive

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.marked = []

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    on_capture(str(path), index, total_drives)
                return total_drives

            def _commit_temp_output(self):
                pass

            def mark_discard_by_indexes(self, total_drives, target_indexes, locked_indexes=None):
                self.marked.append((total_drives, list(target_indexes), list(locked_indexes or [])))
                return len(target_indexes)

        class FakeProcessor:
            def __init__(self, items):
                self.items = items
                self.inventory = []

            def process_image_file(self, _image_path, filename, **_kwargs):
                index = int(filename.removeprefix("raw_drive_").removesuffix(".png"))
                item = self.items[index - 1]
                self.inventory.append(item)
                return item, True

            def _export_to_json(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "roles.json").write_text(
                json.dumps({"A": {"weights": {"Good": 1.0}, "default_set": "Set"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"Bad": 1.0},
                        "main_only_keywords": [],
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            items = [
                Drive(
                    uid="locked-low",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Bad": 1.0},
                ),
                Drive(
                    uid="unlocked-low",
                    quality="Gold",
                    area=1,
                    shape_id="S2",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Bad": 1.0},
                ),
            ]
            scanner = FakeScanner(root)
            processor = FakeProcessor(items)
            original_detector = streaming_pipeline._drive_screenshot_is_locked
            streaming_pipeline._drive_screenshot_is_locked = lambda path: str(path).endswith("raw_drive_0001.png")
            try:
                stats = streaming_pipeline.run_streaming_scan_parse(
                    scanner,
                    processor,
                    total_drives=2,
                    auto_discard_grade="A",
                    auto_discard_lock_action="unlock",
                    config_dir=config_dir,
                )
            finally:
                streaming_pipeline._drive_screenshot_is_locked = original_detector

        self.assertEqual([(2, [1, 2], [1])], scanner.marked)
        self.assertEqual(2, stats["discard_target_count"])
        self.assertEqual(2, stats["discard_marked_count"])
        self.assertEqual(1, stats["discard_locked_target_count"])

    def test_post_actions_syncs_lock_discard_and_clear_targets(self):
        from src.features.scanning import streaming_pipeline
        from src.models.equipment import Drive, Tape

        class FakeScanner:
            def __init__(self, root):
                self.output_dir = str(root)
                self.temp_dir = root / "temp"
                self.temp_dir.mkdir()
                self.synced = []

            def start_scan(self, total_drives, on_capture=None, commit_on_complete=True):
                for index in range(1, total_drives + 1):
                    path = self.temp_dir / f"raw_drive_{index:04d}.png"
                    path.write_bytes(b"png")
                    on_capture(str(path), index, total_drives)
                return total_drives

            def _commit_temp_output(self):
                pass

            def sync_equipment_states(self, total_drives, state_changes):
                self.synced.append((total_drives, list(state_changes)))
                return len(state_changes)

        class FakeProcessor:
            def __init__(self, items):
                self.items = items
                self.inventory = []

            def process_image_file(self, _image_path, filename, **_kwargs):
                index = int(filename.removeprefix("raw_drive_").removesuffix(".png"))
                item = self.items[index - 1]
                self.inventory.append(item)
                return item, True

            def _export_to_json(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "roles.json").write_text(
                json.dumps({"A": {"weights": {"Good": 1.0}, "default_set": "Set"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            (config_dir / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"Good": 1.0},
                        "tape_main_stat_values": {"Good": 1.0},
                        "main_only_keywords": [],
                        "stat_alias_mapping": {},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            items = [
                Drive(
                    uid="low",
                    quality="Gold",
                    area=1,
                    shape_id="S1",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={},
                ),
                Drive(
                    uid="high",
                    quality="Gold",
                    area=1,
                    shape_id="S2",
                    set_name="Set",
                    main_stats={"m1": 1.0, "m2": 1.0},
                    sub_stats={"Good": 1.0},
                ),
                Tape(
                    uid="discarded-high-tape",
                    quality="Purple",
                    area=15,
                    set_name="Set",
                    main_stats="Good",
                    sub_stats={"Good": 1.0},
                ),
            ]
            original_detector = streaming_pipeline._equipment_screenshot_state
            streaming_pipeline._equipment_screenshot_state = lambda path: (
                "discarded" if str(path).endswith("raw_drive_0003.png") else "normal"
            )
            try:
                scanner = FakeScanner(root)
                processor = FakeProcessor(items)
                events = []
                stats = streaming_pipeline.run_streaming_scan_parse(
                    scanner,
                    processor,
                    total_drives=3,
                    scan_done_callback=lambda captured, total: events.append(("scan_done", captured, total)),
                    parse_done_callback=lambda: events.append(("parse_done",)),
                    post_action_ready_callback=lambda: events.append(("post_action_ready",)),
                    post_actions_config={
                        "discard": {
                            "enabled": True,
                            "grade": "SS",
                            "role_scope": "all",
                            "quality_scope": "gold_purple",
                            "type_scope": "all",
                            "on_locked": "skip",
                            "on_discarded": "normal",
                        },
                        "lock": {
                            "enabled": True,
                            "grade": "SSS",
                            "role_scope": "all",
                            "quality_scope": "gold_purple",
                            "type_scope": "all",
                            "on_locked": "skip",
                            "on_discarded": "normal",
                        },
                    },
                    config_dir=config_dir,
                )
            finally:
                streaming_pipeline._equipment_screenshot_state = original_detector

        self.assertEqual(1, len(scanner.synced))
        self.assertEqual([("scan_done", 3, 3)], [event for event in events if event[0] == "scan_done"])
        self.assertLess(events.index(("scan_done", 3, 3)), events.index(("parse_done",)))
        self.assertLess(events.index(("parse_done",)), events.index(("post_action_ready",)))
        changes = scanner.synced[0][1]
        self.assertEqual(
            [(1, "normal", "discarded"), (2, "normal", "locked"), (3, "discarded", "locked")],
            [(c["index"], c["current_state"], c["target_state"]) for c in changes],
        )
        self.assertEqual(3, stats["post_action_target_count"])
        self.assertEqual(1, stats["discard_set_count"])
        self.assertEqual(2, stats["lock_set_count"])


if __name__ == "__main__":
    unittest.main()
