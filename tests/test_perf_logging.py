# 测试性能统计日志格式。
import re
import unittest


class PerfLoggingTests(unittest.TestCase):
    def test_perf_line_formats_elapsed_ms_and_fields(self):
        from src.utils.perf import format_perf_line

        line = format_perf_line("ocr", elapsed_ms=12.345, filename="raw_drive_0001.png", count=2)

        self.assertTrue(line.startswith("PERF ocr "))
        self.assertIn("elapsed_ms=12.35", line)
        self.assertIn("filename=raw_drive_0001.png", line)
        self.assertIn("count=2", line)
        self.assertRegex(line, r"^PERF ocr (\w+=\S+ ?)+$")


if __name__ == "__main__":
    unittest.main()
