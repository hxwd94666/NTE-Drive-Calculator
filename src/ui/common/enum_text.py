# 仅将冻结的前端枚举说明投影为译文；保留原表、键、业务 ID 和用户输入。
from src.integrations.preferences.locale_files import enum_text as _enum_text
from src.ui.common.text_catalog import resolve_text_catalog


def ui_enum_text(catalog, value):
    """只接收代码内声明的说明表/表头；游戏名称、账号名和异常原文不得传入。

    投影规则由本机语言目录层实现，服务层需要同一规则时直接复用同一个函数，
    不必引入窗口对象或 Qt 依赖。
    """

    return _enum_text(resolve_text_catalog(catalog), value)
