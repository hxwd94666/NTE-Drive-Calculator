# 测试全量扫描与截图解析的流水线执行逻辑。
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np


class StreamingScanPipelineTests(unittest.TestCase):
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

    def test_lock_icon_mask_distinguishes_closed_from_open(self):
        from src.features.scanning.streaming_pipeline import _lock_icon_mask_is_locked

        closed = np.array(
            [
                "....####....",
                "...######...",
                "..###..###..",
                ".##########.",
                "############",
                "############",
                "####....####",
                "####....####",
            ],
            dtype="U12",
        )
        opened = np.array(
            [
                "....####....",
                "...######...",
                "..##....##..",
                ".##.........",
                ".##.........",
                "############",
                "####....####",
                "####....####",
            ],
            dtype="U12",
        )

        self.assertTrue(_lock_icon_mask_is_locked(np.char.equal(np.array([list(row) for row in closed]), "#")))
        self.assertFalse(_lock_icon_mask_is_locked(np.char.equal(np.array([list(row) for row in opened]), "#")))

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


if __name__ == "__main__":
    unittest.main()
