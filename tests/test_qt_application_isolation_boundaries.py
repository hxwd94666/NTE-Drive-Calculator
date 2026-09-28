# 校验测试进程内 Qt 应用实例的唯一性与 GUI 能力，防止原生崩溃。
"""Qt 应用隔离回归：非 GUI 应用实例不得污染后续控件测试。

根因（2026-09-28 定位）：`test_battle_report_analysis_load_service.py` 曾以
`QCoreApplication([])` 创建非 GUI 应用；后续 `test_role_catalog.py` 的
`QApplication.instance()` 会直接返回该实例，于是控件在缺少 QGuiApplication
的情况下被创建，Qt 触发致命错误并以 0xC0000409 终止整个测试进程，
表现为 core 分片无可读汇总行。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
_FORBIDDEN = ("QCoreApplication(", "QGuiApplication(")
_PREDECESSOR = "tests.test_battle_report_analysis_load_service"
_WIDGET_MODULE = "tests.test_role_catalog"


class QtApplicationIsolationTests(unittest.TestCase):
    def test_no_test_module_creates_a_non_gui_application(self) -> None:
        """测试必须经共享 fixture 获取应用，不能自建非 GUI 实例。"""

        offenders = [
            path.name
            for path in sorted(TESTS_DIR.glob("test_*.py"))
            # 本文件自身在文档与断言里引用了这些标记，不属于自建实例。
            if path.name != Path(__file__).name
            if any(marker in path.read_text(encoding="utf-8") for marker in _FORBIDDEN)
        ]

        self.assertEqual(
            [], offenders,
            "以下测试自建了非 GUI 的 Qt 应用实例，会污染同进程内的控件测试："
            f"{offenders}；请改用 tests.qt_application_fixture.ensure_qt_application()",
        )

    def test_widget_module_survives_a_qt_service_predecessor(self) -> None:
        """同进程内先跑 Qt 服务测试、再跑控件测试，必须不崩溃。"""

        completed = subprocess.run(
            [
                sys.executable, "-X", "utf8", "-m", "unittest",
                _PREDECESSOR, _WIDGET_MODULE,
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (completed.stdout or "") + (completed.stderr or "")

        self.assertEqual(
            0, completed.returncode,
            f"组合运行被终止（rc={completed.returncode}）；末尾输出：\n{output[-800:]}",
        )
        self.assertIn("Ran ", output)


if __name__ == "__main__":
    unittest.main()
