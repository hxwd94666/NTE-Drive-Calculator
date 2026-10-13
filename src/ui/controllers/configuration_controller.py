# 协调配置页的离页与账号切换，不再向 MainWindow 挂载编辑转发方法。
"""只保留窗口级转场协调；页面控件及草稿由各自 feature 拥有。"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from src.app.context import CallbackAccountLifecycle
from src.features.official_role.page import confirm_pending_my_role_changes


def register_page_configuration_lifecycle(app_context, owners_provider, *, after_stop=None):
    """先阻止账号替换跨过已接受提交，再停止读取并清空旧页面投影。"""
    def stop():
        owners = tuple(owner for owner in owners_provider() if owner is not None)
        if any(owner.is_writing() for owner in owners):
            raise RuntimeError("账号配置正在提交，请等待保存完成后切换账号。")
        for owner in owners:
            owner.close()
        if after_stop is not None:
            after_stop()

    return app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: False, stop=stop, rebuild=lambda _account: None, start=lambda: None,
    ))


def defer_page_transition(window, continuation, *, pages=("config", "my_role")) -> bool:
    """草稿或已接受提交占有转场；成功后仅执行首个待定跳转一次。"""
    page_owner = getattr(window, "basic_weight_page", None)

    def owners_now():
        return tuple(owner for owner in (
            page_owner.task_owner if page_owner is not None else None,
            getattr(window, "_official_role_controller", None),
        ) if owner is not None)

    def dirty(page):
        if page == "config":
            return page_owner is not None and page_owner.is_dirty()
        return page == "my_role" and getattr(window, "_my_role_dirty", False)

    if getattr(window, "_pending_page_transition", None) is not None:
        return True
    if not any(owner.is_writing() for owner in owners_now()) and not any(dirty(page) for page in pages):
        return False
    token = object()
    context = window.app_context
    identity = context.account.active_account_id, context.generation
    window._pending_page_transition = token

    def current():
        return (getattr(window, "_pending_page_transition", None) is token
                and (window.app_context.account.active_account_id, window.app_context.generation) == identity)

    def cancel():
        if getattr(window, "_pending_page_transition", None) is token:
            window._pending_page_transition = None

    def continue_at(index):
        if not current():
            cancel()
            return
        writing = next((owner for owner in owners_now() if owner.is_writing()), None)
        if writing is not None:
            writing.when_commit_settled(lambda success: continue_at(index) if success else cancel())
            return
        if index >= len(pages):
            def finish():
                if current():
                    cancel()
                    continuation()
                else:
                    cancel()
            QTimer.singleShot(0, finish)
            return
        page = pages[index]
        callback = lambda success: continue_at(index + 1) if success else cancel()
        if page == "config":
            allowed = page_owner.confirm_leave(callback) if page_owner is not None else True
        elif page == "my_role":
            allowed = confirm_pending_my_role_changes(window, completion=callback)
        else:
            allowed = True
        if allowed:
            continue_at(index + 1)
        elif not any(owner.is_writing() for owner in owners_now()):
            cancel()

    continue_at(0)
    return True
