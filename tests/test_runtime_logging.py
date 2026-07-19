# 验证常驻日志与用户启用的时间戳运行日志生命周期。
import tempfile
import sys
import unittest
from pathlib import Path

import src.utils.logger as logger_module
from src.utils.logger import (
    disable_session_log,
    enable_session_log,
    is_session_log_enabled,
    logger,
    set_log_dir,
)


class RuntimeLoggingTests(unittest.TestCase):
    def setUp(self):
        self.original_log_dir = logger_module.LOG_DIR
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name)
        set_log_dir(self.log_dir)

    def tearDown(self):
        disable_session_log()
        set_log_dir(self.original_log_dir)
        self.temp_dir.cleanup()

    def test_session_log_is_created_and_receives_runtime_messages(self):
        session_path = enable_session_log()
        logger.info("session-log-probe")
        logger.complete()

        self.assertTrue(is_session_log_enabled())
        self.assertEqual(session_path.parent, self.log_dir)
        self.assertRegex(
            session_path.name,
            r"^nte_runtime_\d{8}_\d{6}(?:_\d+)?\.log$",
        )
        self.assertIn(
            "session-log-probe",
            (self.log_dir / "nte_runtime.log").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "session-log-probe",
            session_path.read_text(encoding="utf-8"),
        )

    def test_disabling_session_log_keeps_runtime_log_active(self):
        session_path = enable_session_log()

        disable_session_log()
        logger.info("runtime-only-probe")
        logger.complete()

        self.assertFalse(is_session_log_enabled())
        self.assertIn(
            "runtime-only-probe",
            (self.log_dir / "nte_runtime.log").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "runtime-only-probe",
            session_path.read_text(encoding="utf-8"),
        )

    def test_switching_log_directory_reopens_enabled_session_log(self):
        with tempfile.TemporaryDirectory() as second:
            first_session = enable_session_log()

            set_log_dir(second)
            logger.info("new-account-probe")
            logger.complete()

            second_sessions = list(Path(second).glob("nte_runtime_*.log"))
            self.assertEqual(len(second_sessions), 1)
            self.assertNotEqual(first_session, second_sessions[0])
            self.assertNotIn(
                "new-account-probe",
                first_session.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "new-account-probe",
                second_sessions[0].read_text(encoding="utf-8"),
            )
            disable_session_log()
            set_log_dir(self.log_dir)

    def test_windowed_runtime_installs_device_independent_null_streams(self):
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        try:
            sys.stdout = None
            sys.stderr = None

            logger_module._install_missing_standard_streams()
            replacement_stdout = sys.stdout
            replacement_stderr = sys.stderr
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr

        self.assertIsInstance(replacement_stdout, logger_module._NullTextStream)
        self.assertIsInstance(replacement_stderr, logger_module._NullTextStream)
        self.assertEqual(replacement_stdout.write("discarded"), len("discarded"))
        self.assertIsNone(replacement_stdout.flush())
        self.assertFalse(replacement_stdout.isatty())


if __name__ == "__main__":
    unittest.main()
