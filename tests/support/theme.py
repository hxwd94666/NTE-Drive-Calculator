# 显式共享三主题夹具和事件排空，不收集业务测试或启动后台任务。

import pytest

from tests.support.qt_fonts import fonts
from src.app.theme import apply_app_theme, current_theme_name, install_dialog_defaults

@pytest.fixture(params=["black"])
def themed_app(request):
    app = fonts()
    previous = current_theme_name()
    apply_app_theme(app, request.param)
    install_dialog_defaults(app)
    yield app
    apply_app_theme(app, previous)


def settle(app):
    # 布局、延迟删页和窗口事件须全部落定，不能只在模式回调刚返回时读取一次几何。
    for _ in range(8):
        app.processEvents()

