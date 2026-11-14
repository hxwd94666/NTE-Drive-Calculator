# 按同步来源引导重连，区分组件部署、抓包准备、就绪与同步完成。
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.home.page import inventory_sync_error_guidance


class SyncRetryDialog(QDialog):
    """窗口关闭/Esc 只收起；准备阶段仅“取消重启”按钮取消请求。"""
    def __init__(self, parent, *, controller, native=False, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.controller = controller
        self.native = native
        self._begun = False
        self._ready = False
        self._invalid = False
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.home.init.self') if native else resolve_text_catalog(text_catalog).render('ui.home.init.self_2'))
        layout = QVBoxLayout(self)
        self.detail = plain_label(
            (
                resolve_text_catalog(text_catalog).render('ui.home.init.detail')
            ) if native else (
                resolve_text_catalog(text_catalog).render('ui.home.init.detail_2')
            ),
            self,
        )
        self.detail.setWordWrap(True)
        self._instructions = self.detail.text()
        layout.addWidget(self.detail)
        self.buttons = QHBoxLayout()
        self.begin = TextActionButton(resolve_text_catalog(text_catalog).render('ui.home.init.begin'), self)
        self.begin.clicked.connect(self._start)
        self.dismiss = TextActionButton(resolve_text_catalog(text_catalog).render('common.close'), self)
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
            self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.start.detail'))
            return
        self._begun = True
        self.begin.setEnabled(False)
        self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.start.dismiss'))
        self.controller.restart()
        self.update_preparation(self.controller.preparation_state)

    def update_preparation(self, preparation):
        if self._invalid:
            return
        if not self._begun:
            self._ready = False
            self.detail.setText(self._instructions)
            self.begin.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.init.begin'))
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
                self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.start.dismiss'))
            self.detail.setText(messages[preparation])
            return
        if self._begun:
            service = self.controller.window._inventory_sync_service
            if service is not None:
                self.update_state(service.state)
            else:
                self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_preparation.detail') if self.native else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_preparation.detail_2'))

    def update_state(self, state):
        if not self._begun or self._invalid:
            return
        preparation = self.controller.preparation_state
        if preparation is not None and state.phase != "error":
            self.update_preparation(preparation)
            return
        if state.phase == "error":
            guidance = inventory_sync_error_guidance(
                state.error_code, state.error,
                capture_source="native" if self.native else "packet",
                text_catalog=getattr(self, 'text_catalog', None),
            ).replace("处理：", "下一步：")
            self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_5') + guidance)
            self.detail.setToolTip(
                plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail', value=f"{state.error_code or '未分类'}"))
            )
            self._begun = False
            self.begin.setEnabled(True)
            self.begin.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.begin'))
            self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.close'))
        elif state.source_snapshot_ready and state.phase == "listening":
            self._ready = True
            self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_2'))
            self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.dismiss'))
        elif state.phase in {"collecting", "saving"}:
            self._ready = True
            self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_3'))
            self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.close'))
        elif state.capturing and not self.native:
            self._ready = True
            self.detail.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_4'))
            self.dismiss.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.close'))
        else:
            self.detail.setText(
                state.message + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_6') if self.native
                else state.message + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.home.update_state.detail_7')
            )

    def context_changed(self):
        self._invalid = True
        self.reject()

    def _dismiss(self):
        if self._begun and not self._ready and not self._invalid:
            self.controller.cancel_restart()
        self.reject()
