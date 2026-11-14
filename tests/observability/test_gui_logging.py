# 验证 GUI 日志不依赖控制台可写，同时保留账号及详细文件日志。
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


class GuiLoggingTests(unittest.TestCase):
    def test_blocked_console_cannot_block_gui_logs_or_account_switch(self):
        # 子进程隔离全局 logger；模拟选择模式中无法返回的 write，而非仅断言 sink 数量。
        script = textwrap.dedent('''
            import sys, tempfile, threading
            from pathlib import Path
            class PausedConsole:
                encoding = "utf-8"
                def __init__(self):
                    self.writes = 0
                    self.release = threading.Event()
                def write(self, value):
                    self.writes += 1
                    self.release.wait(3)
                    return len(value)
                def flush(self): pass
                def isatty(self): return False
            console = PausedConsole()
            original = sys.stderr
            sys.stderr = console
            from src.utils.logger import (
                disable_console_logging, set_log_dir, enable_session_log,
                disable_session_log, logger,
            )
            disable_console_logging()
            disable_console_logging()
            errors = []
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                def work():
                    try:
                        set_log_dir(root / "first")
                        detailed = enable_session_log()
                        logger.info("battle-saved-probe")
                        logger.debug("detail-probe")
                        assert "detail-probe" in detailed.read_text(encoding="utf-8")
                        set_log_dir(root / "second", reopen_session=False)
                        logger.info("new-account-probe")
                        disable_session_log()
                    except BaseException as error:
                        errors.append(error)
                worker = threading.Thread(target=work)
                worker.start()
                worker.join(1)
                blocked = worker.is_alive()
                console.release.set()
                worker.join(5)
                sys.stderr = original
                assert not blocked, "GUI logging waited for paused console"
                assert not errors, errors
                assert console.writes == 0, console.writes
                assert "battle-saved-probe" in (root / "first/nte_runtime.log").read_text(encoding="utf-8")
                assert "new-account-probe" in (root / "second/nte_runtime.log").read_text(encoding="utf-8")
                logger.remove()
        ''')
        result = subprocess.run(
            [sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[2],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
