# 独立拥有基础权重页控件、账号草稿及后台任务，向组合根提供窄生命周期接口。
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from src.app.context import AppContext
from .controller import BasicWeightController
from .dependencies import freeze_basic_weight_dependencies
from . import page as view


class BasicWeightPage(QWidget):
    """保留原界面；角色编辑状态不再动态挂到 MainWindow。"""

    def __init__(self, app_context: AppContext, navigate: Callable[[str], None],
                 changed: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app_context = app_context
        self._navigate = navigate
        self._changed = changed
        self._basic_weight_controller: BasicWeightController | None = None
        self._clear_account_draft()
        view.build_config_widgets(self)

    @property
    def task_owner(self) -> BasicWeightController | None:
        """仅暴露已经创建的 owner；组合根查询状态不触发读取或建库。"""
        return self._basic_weight_controller

    def controller(self) -> BasicWeightController:
        dependencies = freeze_basic_weight_dependencies(self.app_context)
        previous = self._basic_weight_controller
        if previous is None or previous._closed or previous.dependencies != dependencies:
            if previous is not None:
                if previous.is_writing():
                    raise RuntimeError("账号配置正在提交，请等待保存完成后切换账号。")
                previous.retire()
            # 先清空旧账号草稿再接受新结果；刷新入口不得被旧 dirty 标记拦住。
            self._clear_account_draft()
            controller = BasicWeightController(dependencies, self)
            controller.changed.connect(lambda: self._changed() if self._is_current(controller) else None)
            self._basic_weight_controller = controller
        return self._basic_weight_controller

    def _is_current(self, controller: BasicWeightController) -> bool:
        return (controller is self._basic_weight_controller
                and controller.dependencies == freeze_basic_weight_dependencies(self.app_context))

    def _clear_account_draft(self) -> None:
        self._config_dirty = False
        self._current_config_name = None
        self._config_form_data = None
        self._config_loaded_model = None
        self._config_model_dependencies = None
        self._config_active_role = None
        self.config_roles_tabs = None
        self._config_weight_property_labels = {}
        for field in ("sub_choices", "main_choices"):
            setattr(self, "_config_weight_" + field, ())
        for field in ("shape_bonus_choices", "shape_label_choices", "suit_choices"):
            setattr(self, "_config_" + field, ())
        for field in ("character", "shape_bonus", "board", "target_suit"):
            setattr(self, f"_config_dirty_{field}_ids", set())
        # 账号代次变化后旧内容立即退出视图，不等待下一次异步读取完成。
        self._filter_config_roles = lambda _text: None
        if hasattr(self, "config_form_layout"):
            while self.config_form_layout.count():
                item = self.config_form_layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
                    item.widget().deleteLater()
            self.config_role_search.clear()
            self.config_load_status.clear()
            self.setEnabled(True)

    def clear_projection(self) -> None:
        """组合根停止旧 owner 后调用；不创建任务，也不取消已接受的提交。"""
        self._clear_account_draft()

    def is_dirty(self) -> bool:
        return self._config_dirty

    def refresh(self) -> None:
        self.controller()
        view.switch_config_form(self)

    def deactivate(self) -> None:
        if self.task_owner is not None:
            self.task_owner.cancel_reads()

    def confirm_leave(self, completion: Callable[[bool], None]) -> bool:
        return view.confirm_pending_config_changes(self, completion=completion)

    def close_tasks(self) -> None:
        if self.task_owner is not None:
            if self.task_owner.is_writing():
                raise RuntimeError("账号配置正在提交，请等待保存完成后退出。")
            self.task_owner.close()
        self._clear_account_draft()
