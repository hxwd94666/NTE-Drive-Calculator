# 引导用户在登录前重新建立背包监听，区分准备、就绪与同步完成。
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
        self.setWindowTitle("重新同步游戏数据" if native else "重新同步背包")
        layout = QVBoxLayout(self)
        self.detail = QLabel(
            "将重新连接游戏，读取背包、角色当前装备、等级与突破。已保存的数据和配装方案保持可用。" if native else
            "请先让游戏返回登录页，暂时不要进入游戏。\n\n"
            "准备好后点击下方按钮；等到提示“监听已就绪”，再进入游戏。"
            "重新同步会保留当前已保存的背包和配装方案。",
            self,
        )
        self.detail.setWordWrap(True)
        self._instructions = self.detail.text()
        layout.addWidget(self.detail)
        buttons = QHBoxLayout()
        self.begin = QPushButton("重新读取游戏数据" if native else "已返回登录页，开始监听", self)
        self.begin.clicked.connect(self._start)
        self.dismiss = QPushButton("关闭", self)
        self.dismiss.clicked.connect(self._dismiss)
        buttons.addWidget(self.begin)
        buttons.addWidget(self.dismiss)
        layout.addLayout(buttons)
        self.background_hint = QLabel("关闭窗口或按 Esc 不会停止后台同步；停止自动同步请使用首页开关。", self)
        self.background_hint.setWordWrap(True)
        layout.addWidget(self.background_hint)
        controller.state_changed.connect(self.update_state)
        controller.preparation_changed.connect(self.update_preparation)
        self.update_preparation(controller.preparation_state)
        fit_dialog_to_available_screen(self, QSize(530, 235))

    def _start(self):
        if self._invalid or self._begun:
            return
        if self.controller.window.battle_report_controller.is_running():
            self.detail.setText("请先结束战报，再重新同步。")
            return
        self._begun = True
        self.begin.setEnabled(False)
        self.dismiss.setText("取消本次重试")
        self.controller.restart()
        self.update_preparation(self.controller.preparation_state)

    def update_preparation(self, preparation):
        if self._invalid:
            return
        messages = {
            'checking_game': "正在检测游戏是否启动。已保存背包保持可用。",
            'waiting_game': ("等待启动游戏；启动后自动同步背包与角色数据。" if self.native else
                             "等待启动游戏；启动后自动准备监听，请在监听就绪前停留在登录页。"),
            'waiting_component': "已发现游戏，等待游戏内组件就绪后读取背包与角色数据。",
            'stopping': "正在收尾旧会话，完成后继续准备同步。",
        }
        if preparation in messages:
            self._ready = False
            if self._begun:
                self.dismiss.setText("取消本次重试")
            self.detail.setText(messages[preparation])
            if not self._begun and preparation == 'waiting_game':
                self.begin.setText("等待游戏并自动同步")
            return
        if self._begun:
            service = self.controller.window._inventory_sync_service
            if service is not None:
                self.update_state(service.state)
            else:
                self.detail.setText("正在准备读取背包与角色数据。" if self.native else "正在准备监听，请暂时停留在登录页。")
        else:
            self.detail.setText(self._instructions)
            self.begin.setText("重新读取游戏数据" if self.native else "已返回登录页，开始监听")

    def update_state(self, state):
        if not self._begun or self._invalid:
            return
        preparation = self.controller.preparation_state
        if preparation is not None and state.phase != "error":
            self.update_preparation(preparation)
            return
        if state.phase == "error":
            self.detail.setText("重新同步未完成，请检查首页状态或检测详情后重试。已保存背包保持可用。")
            self._begun = False
            self.begin.setEnabled(True)
            self.begin.setText("重试")
            self.dismiss.setText("关闭")
        elif state.source_snapshot_ready and state.phase == "listening":
            self._ready = True
            self.detail.setText("背包同步完成，正在持续监听变化。")
            if self.native:
                self.detail.setText("背包同步完成，正在持续监听变化。\n角色等级与突破的保存状态请查看首页。")
            self.dismiss.setText("完成")
        elif state.phase in {"collecting", "saving"}:
            self._ready = True
            self.detail.setText("正在接收并保存背包数据，完成后继续后台监听。")
            self.dismiss.setText("关闭")
        elif state.capturing and not self.native:
            self._ready = True
            self.detail.setText("监听已就绪，现在可以进入游戏。\n\n背包将在接收并稳定后保存，关闭此窗口不影响同步。")
            self.dismiss.setText("关闭")
        else:
            self.detail.setText(state.message + ("\n\n请暂时停留在登录页，等待监听就绪。" if not self.native else ""))

    def context_changed(self):
        self._invalid = True
        self.reject()

    def _dismiss(self):
        if self._begun and not self._ready and not self._invalid:
            self.controller.cancel_restart()
        self.reject()
