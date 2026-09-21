# 按同步来源引导重连，区分组件部署、抓包准备、就绪与同步完成。
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen


class SyncRetryDialog(QDialog):
    def __init__(self, parent, *, controller, native=False):
        super().__init__(parent)
        self.controller = controller
        self.native = native
        self._begun = False
        self._ready = False
        self._invalid = False
        self.setWindowTitle("重启游戏数据同步" if native else "重启背包同步")
        layout = QVBoxLayout(self)
        self.detail = QLabel(
            (
                "同步异常或数据未更新时，可在这里重新建立同步连接。\n\n"
                "如需部署或更新组件，请先完全退出游戏，部署完成后再启动游戏。"
                "请登录并进入游戏场景，再点击“开始重启同步”，等待同步完成。"
            ) if native else (
                "同步异常或数据未更新时，可在这里重新建立同步连接。\n\n"
                "请先退回游戏登录界面，再点击“开始重启同步”；"
                "等待准备完成后，重新登录游戏。"
            ),
            self,
        )
        self.detail.setWordWrap(True)
        self._instructions = self.detail.text()
        layout.addWidget(self.detail)
        self.buttons = QHBoxLayout()
        self.begin = QPushButton("开始重启同步", self)
        self.begin.clicked.connect(self._start)
        self.dismiss = QPushButton("关闭", self)
        self.dismiss.clicked.connect(self._dismiss)
        self.buttons.addWidget(self.begin)
        self.buttons.addWidget(self.dismiss)
        layout.addLayout(self.buttons)
        controller.state_changed.connect(self.update_state)
        controller.preparation_changed.connect(self.update_preparation)
        self.update_preparation(controller.preparation_state)
        fit_dialog_to_available_screen(self, QSize(500, 165))

    def _start(self):
        if self._invalid or self._begun:
            return
        if self.controller.window.battle_report_controller.is_running():
            self.detail.setText("请先结束战报，再重启同步。")
            return
        self._begun = True
        self.begin.setEnabled(False)
        self.dismiss.setText("取消重启")
        self.controller.restart()
        self.update_preparation(self.controller.preparation_state)

    def update_preparation(self, preparation):
        if self._invalid:
            return
        if not self._begun:
            self._ready = False
            self.detail.setText(self._instructions)
            self.begin.setText("开始重启同步")
            return
        messages = {
            'checking_game': (
                "正在检查游戏状态与组件部署情况。" if self.native
                else "正在检查游戏状态，请保持在登录界面。"
            ),
            'waiting_game_exit': "组件尚未完成部署或更新。请完全退出游戏，部署完成后再启动，并进入游戏场景。",
            'waiting_deployment': "组件尚未完成部署或更新，请暂勿启动游戏。请查看检测详情，完成部署后再启动，并进入游戏场景。",
            'waiting_game': (
                "等待启动游戏。请登录并进入游戏场景，程序将自动同步背包与角色数据。" if self.native
                else "等待启动游戏。启动后请先停留在登录界面，准备完成后再登录。"
            ),
            'waiting_component': "已检测到游戏，正在等待同步组件就绪。请进入游戏场景；若长时间未就绪，请查看检测详情。",
            'stopping': "正在结束原同步连接，随后会自动重新建立。",
        }
        if preparation in messages:
            self._ready = False
            if self._begun:
                self.dismiss.setText("取消重启")
            self.detail.setText(messages[preparation])
            return
        if self._begun:
            service = self.controller.window._inventory_sync_service
            if service is not None:
                self.update_state(service.state)
            else:
                self.detail.setText("正在准备读取背包与角色数据。请进入游戏场景，等待同步完成。" if self.native else "正在准备监听，请暂时停留在登录页。")

    def update_state(self, state):
        if not self._begun or self._invalid:
            return
        preparation = self.controller.preparation_state
        if preparation is not None and state.phase != "error":
            self.update_preparation(preparation)
            return
        if state.phase == "error":
            self.detail.setText("重启同步未完成，请检查首页状态或检测详情后重试。已保存背包保持可用。")
            self._begun = False
            self.begin.setEnabled(True)
            self.begin.setText("再次尝试")
            self.dismiss.setText("关闭")
        elif state.source_snapshot_ready and state.phase == "listening":
            self._ready = True
            self.detail.setText("数据同步完成，后台监听已恢复。")
            self.dismiss.setText("完成")
        elif state.phase in {"collecting", "saving"}:
            self._ready = True
            self.detail.setText("正在读取并保存数据，完成后会继续后台监听。")
            self.dismiss.setText("关闭")
        elif state.capturing and not self.native:
            self._ready = True
            self.detail.setText("同步连接已准备完成，现在请重新登录游戏。\n\n数据读取和保存完成后，会继续后台监听。")
            self.dismiss.setText("关闭")
        else:
            self.detail.setText(
                state.message + "\n\n请进入游戏场景，等待同步完成。" if self.native
                else state.message + "\n\n请保持在登录界面，等待准备完成。"
            )

    def context_changed(self):
        self._invalid = True
        self.reject()

    def _dismiss(self):
        if self._begun and not self._ready and not self._invalid:
            self.controller.cancel_restart()
        self.reject()
