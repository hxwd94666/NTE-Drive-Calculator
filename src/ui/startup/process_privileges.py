# 在账号准备前核验完整性等级，权限不足仅请求一次系统管理员重启。
from __future__ import annotations

import sys

from src.domain.localization.catalog import TextCatalog
from src.integrations.preferences.locale_files import bundled_text_catalog
from src.integrations.runtime.windows_privileges import (
    ADMIN_RESTART_ARGUMENT,
    HIGH_INTEGRITY_RID,
    current_process_integrity,
    request_admin_restart,
)


class StartupPrivilegeError(RuntimeError):
    """权限前置失败仅展示本项下一步，不套用数据目录修复提示。"""


def ensure_process_privileges(*, text_catalog: TextCatalog | None = None) -> None:
    """未通过不准备账号或输入后端；同意重启不等于新进程已通过核验。"""
    if sys.platform != "win32":
        return
    # 账号/本机偏好尚未准备，此处沿用启动窗口的发行中文回退目录。
    catalog = text_catalog if text_catalog is not None else bundled_text_catalog()
    try:
        integrity = current_process_integrity()
    except OSError as error:
        raise StartupPrivilegeError(catalog.render("startup.privilege.inspect_failed")) from error
    if integrity >= HIGH_INTEGRITY_RID:
        return
    if ADMIN_RESTART_ARGUMENT in sys.argv[1:]:
        raise StartupPrivilegeError(catalog.render("startup.privilege.still_low"))
    try:
        restarted = request_admin_restart()
    except OSError as error:
        raise StartupPrivilegeError(catalog.render("startup.privilege.relaunch_failed")) from error
    if not restarted:
        raise StartupPrivilegeError(catalog.render("startup.privilege.relaunch_failed"))
    # 旧进程尚未持有账号租约/worker，交接后直接退出，不并行启动第二套界面。
    raise SystemExit(0)
