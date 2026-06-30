# 测试全量扫描与截图解析的流水线执行逻辑。
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace


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


if __name__ == "__main__":
    unittest.main()
