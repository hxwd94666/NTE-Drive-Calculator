# 测试窗口共用中文字体准备，不导入或执行其他测试模块。
import os
from pathlib import Path
from PySide6.QtGui import QFontDatabase
from tests.support.auto_sync_ui_fixture import application


def fonts():
    app = application()
    if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
        for name in ("msyh.ttc", "msyhbd.ttc"):
            path = Path(os.environ["WINDIR"]) / "Fonts" / name
            if path.is_file():
                QFontDatabase.addApplicationFontFromData(path.read_bytes())
    return app
