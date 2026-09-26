# 管理首页自动背包同步的进程观察、异步收尾和重新同步请求。
from __future__ import annotations

from threading import Thread

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from src.integrations.game_process_watcher import GameProcessWatcher


class AutoSyncController(QObject):
    process_changed = Signal(object)
    stop_finished = Signal(object)
    state_changed = Signal(object)
    preparation_changed = Signal(object)

    def __init__(self, *, window, policy, request_check, watcher_factory=GameProcessWatcher,
                 submit_stop=None, request_enable_preflight=None):
        super().__init__(window)
        self.window, self.policy = window, policy
        self._request_check = request_check
        self._request_enable_preflight = request_enable_preflight
        self._watcher_factory = watcher_factory
        self._submit_stop = submit_stop or self._background
        self._closed = False
        self._started = False
        self._key = None
        self._watcher = None
        self._watch_token = None
        self._process = None
        self._probe = None
        self._probe_context = None
        self._attempt = None
        self._stopping = None
        self._stop_failed = False
        self._retry_cancelled = False
        self._restarting = False
        self._dialog = None
        self._detail = ""
        self._last_preparation = object()
        self.process_changed.connect(self._process_event)
        self.stop_finished.connect(self._stopped)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)

    @staticmethod
    def _background(job):
        Thread(target=job, name="inventory-sync-teardown", daemon=True).start()

    def _context(self):
        settings = self.policy.settings
        context = self.window.app_context
        return (context.account.active_account_id, context.generation, settings.mode,
                settings.risk_confirmed, settings.paused, settings.auto_sync_enabled, self.policy.operation_revision)

    @property
    def packet_game_running(self):
        """None means the watcher is not the active process observer."""
        if self._closed or self._watch_token is None:
            return None
        return self._process is not None

    @property
    def preparation_state(self):
        if self._stopping:
            return 'stopping'
        if self._stop_failed or self._retry_cancelled:
            return None
        if self.policy.allowed('native_sync'):
            if self.policy.settings.pending_cleanup:
                if self._probe_context != self._context() or self._probe is None:
                    return 'checking_game'
                return 'waiting_game_exit' if self._probe.game_running else 'waiting_deployment'
            if self._probe_context != self._context() or self._probe is None:
                return 'checking_game'
            if self._probe.native_load.files is False:
                return 'waiting_game_exit' if self._probe.game_running else 'waiting_deployment'
            if not self._probe.game_running:
                return 'waiting_game'
            if not (self._probe.core_available and self._probe.native_inventory.handshake):
                return 'waiting_component'
        elif self.policy.allowed('packet_capture') and self._process is None:
            return 'waiting_game'
        return None

    def start(self):
        if self._closed:
            return
        self._started = True
        self._timer.start()
        self.refresh()

    def observe_probe(self, probe):
        self._probe = probe
        self._probe_context = self._context()
        if self.policy.allowed("native_sync") and not probe.game_running:
            self._stop_inventory()
        self.refresh()

    def _stop_watcher(self):
        self._watch_token = None
        self._process = None
        if self._watcher is not None:
            self._watcher.stop()

    def _ensure_watcher(self):
        if self._watch_token is not None:
            return
        if self._watcher is not None and getattr(self._watcher, "worker_alive", self._watcher.is_running):
            return
        token = object()
        self._watch_token = token
        frozen = self._context()
        self._watcher = self._watcher_factory(
            on_change=lambda event: self._publish_process((token, frozen, event)),
        )
        self._watcher.start()

    def _publish_process(self, event):
        if not self._closed:
            try:
                self.process_changed.emit(event)
            except RuntimeError:
                pass  # Parent Qt object may already have been destroyed during shutdown.

    def _process_event(self, message):
        token, frozen, event = message
        if self._closed or token is not self._watch_token or frozen != self._context():
            return
        if event.kind == "started":
            self._process = event.process
            self._attempt = None
            self._retry_cancelled = False
            self._detail = "已发现游戏，正在准备背包监听。"
        elif event.kind == "exited":
            if self._process != event.process:
                return
            self._process = None
            self._attempt = None
            self._retry_cancelled = False
            self._detail = "游戏已退出，等待下次启动。已保存背包仍可用于计算。"
            self._stop_inventory()
        elif event.kind == "error":
            self._detail = event.error
            self._process = None
            self._attempt = None
            self._stop_inventory()
        elif event.kind == "waiting" and self._process is None:
            self._detail = "等待启动游戏；发现游戏后自动开始背包监听。"
        self.refresh()

    def set_enabled(self, enabled):
        if self._closed:
            return
        if enabled and (not self.policy.settings.auto_sync_enabled or self.policy.settings.paused) and self._request_enable_preflight:
            self.render()  # Keep the persisted off state visible until confirmation.
            self._request_enable_preflight(self._confirm_enable)
            return
        if enabled and not (self.policy.allowed("native_sync") or self.policy.allowed("packet_capture")):
            self.window.operation_entry("game_sync", "自动同步")
            self.render()
            return
        if enabled and self.policy.settings.paused:
            self.window.operation_unavailable(
                "自动同步", "连接已暂停，请查看检测详情并完成同步条件核对。", target="detection",
            )
            self.render()
            return
        try:
            self.policy.set_auto_sync_enabled(enabled)
        except OSError:
            QMessageBox.warning(self.window, "自动同步", "自动同步设置未能保存，请检查配置目录后重试。")
        self.refresh()
        if self.policy.settings.auto_sync_enabled:
            self._request_check()

    def _confirm_enable(self) -> bool:
        if self._closed or (self.policy.settings.auto_sync_enabled and not self.policy.settings.paused):
            return False
        if not (self.policy.allowed("native_sync") or self.policy.allowed("packet_capture")):
            self.render()
            return False
        try:
            self.policy.enable_auto_sync_after_preflight(
                resume_paused=self.policy.settings.paused,
            )
        except (OSError, PermissionError):
            self.render()
            return False
        self.refresh()
        return True

    def refresh(self):
        if self._closed or not self._started:
            return
        key = self._context()
        if key != self._key:
            if self._key is not None:
                self._stop_inventory()
            self._key = key
            self._stop_watcher()
            self._attempt = None
            self._retry_cancelled = False
            self._restarting = False
            self._detail = ""
            if self._dialog is not None:
                self._dialog.context_changed()
        native = self.policy.allowed("native_sync", automatic=True)
        packet = not self.policy.allowed("native_sync") and self.policy.allowed("packet_capture", automatic=True)
        if not (native or packet):
            self._stop_watcher()
            self.render()
            return
        if packet:
            self._ensure_watcher()
            ready = self._process is not None
            identity = self._process
        else:
            probe = self._probe
            ready = bool(self._probe_context == key and probe and probe.game_running
                         and not self.policy.settings.pending_cleanup
                         and probe.native_load.files is not False
                         and probe.core_available and probe.native_inventory.handshake)
            identity = "native"
            if not ready:
                self._attempt = None
        service = self.window._inventory_sync_service
        if (ready and self._stopping is None and not self._stop_failed and not self._retry_cancelled
                and not self.window.work_mode_controller.is_transitioning
                and not (native and self.window.battle_report_controller.is_running())
                and not (service and service.is_running)
                and self._attempt != (key, identity)):
            self._attempt = key, identity
            self.window._start_inventory_sync(automatic=True)
        self.render()

    def _stop_inventory(self):
        service = self.window._inventory_sync_service
        if service is None or self._stopping is service:
            return
        self.window.invalidate_inventory_sync_notifications()
        service.request_stop()
        self._stopping = service

        def finish():
            error = ""
            try:
                service.stop()
            except Exception:
                error = "上次背包同步尚未停止，请等待收尾或重新同步。"
            if not self._closed:
                try:
                    self.stop_finished.emit((service, error))
                except RuntimeError:
                    pass  # The UI can be destroyed after cancellation but before join finishes.

        self._submit_stop(finish)

    def _stopped(self, result):
        service, error = result
        if service is not self._stopping:
            return
        self._stopping = None
        if self._closed:
            return
        self._stop_failed = bool(error and service.is_running)
        if self._stop_failed:
            self._detail = error
        elif service is self.window._inventory_sync_service:
            self.window._stop_inventory_sync()
        self.refresh()

    def open_restart(self):
        if self._closed:
            return
        if self.policy.settings.paused:
            self.set_enabled(True)
            return
        if not self.policy.settings.auto_sync_enabled:
            self.set_enabled(True)
            return
        if self.window.battle_report_controller.is_running():
            return
        if self._dialog is not None:
            self._dialog.show()
            self._dialog.raise_()
            return
        if not self.policy.allowed("native_sync") and not self.policy.allowed("packet_capture"):
            self.window.operation_entry("game_sync", "重启同步")
            return
        from src.features.home.sync_retry_dialog import SyncRetryDialog
        dialog = SyncRetryDialog(self.window, controller=self, native=self.policy.allowed("native_sync"))
        self._dialog = dialog
        dialog.finished.connect(lambda _result: self._clear_dialog(dialog))
        dialog.show()

    def _clear_dialog(self, dialog):
        if self._dialog is dialog:
            self._dialog = None
        dialog.deleteLater()

    def restart(self):
        if self._closed or self.window.battle_report_controller.is_running():
            return
        capability = "native_sync" if self.policy.allowed("native_sync") else "packet_capture"
        if not self.policy.allowed(capability, automatic=True):
            return
        self._attempt = None
        self._retry_cancelled = False
        self._stop_failed = False
        self._restarting = True
        self._detail = "正在停止旧会话并重新建立同步，已保存背包保持可用。"
        if self._watcher is not None and not self._watcher.is_running:
            self._stop_watcher()
        self._stop_inventory()
        self.refresh()

    def cancel_restart(self):
        if not self._restarting:
            return
        self._restarting = False
        self._retry_cancelled = True
        self._detail = "本次重启同步已取消，可点击“重启同步”重试。已保存背包保持可用。"
        self._stop_inventory()
        self.render()

    def inventory_state_changed(self, state):
        if self._closed:
            return
        if state.source_snapshot_ready and state.phase == "listening":
            self._restarting = False
        self.state_changed.emit(state)
        self.render()

    def render(self):
        preparation = self.preparation_state
        if preparation != self._last_preparation:
            self._last_preparation = preparation
            self.preparation_changed.emit(preparation)
        toggle = getattr(self.window, "home_auto_sync_toggle", None)
        if toggle is None:
            return
        settings = self.policy.settings
        toggle.blockSignals(True)
        toggle.setChecked(settings.auto_sync_enabled and not settings.paused)
        toggle.blockSignals(False)
        native = self.policy.allowed("native_sync")
        online = native or self.policy.allowed("packet_capture")
        source = ("来源：游戏内组件" if native else
                  "来源：抓包（角色养成需手动维护）")
        source_tip = (
            "同步背包、当前装备及角色养成（等级、突破、技能、好感度、弧盘），并持续监听变化。"
            if native else
            "同步背包和当前装备并持续监听变化；角色养成需手动维护。"
        )
        if not online:
            source = "离线模式：使用已保存数据"
            source_tip = "不连接游戏，也不采集新数据。"
        self.window.home_sync_source_label.setText(source)
        self.window.home_sync_source_label.setToolTip(source_tip)
        self.window.home_sync_source_label.hide()
        title = getattr(self.window, "home_sync_title", None)
        if title is not None:
            title.setText("背包同步" if online and not native else "游戏数据同步")
        service = self.window._inventory_sync_service
        state = service.state if service is not None else None
        role_detail = getattr(self.window, "home_character_sync_detail", None)
        if role_detail is not None:
            error = getattr(state, "character_sync_error", None)
            role_detail.setVisible(bool(native and error))
            if native and error:
                role_detail.setText(error)
        battle = self.window.battle_report_controller.is_running()
        button = self.window.home_restart_sync_button
        button.setText("恢复自动同步" if settings.paused else
                       "重启同步" if settings.auto_sync_enabled else "开启自动同步")
        button.setToolTip(
            "先核对同步条件；组件已准备好且清理完成时恢复同步，无需重选工作模式。"
            if settings.paused else
            "停止当前同步连接并重新建立；用于同步异常或背包未更新。已保存数据不会删除。"
            if settings.auto_sync_enabled else
            "先显示环境检测，确认准备后进入游戏场景自动读取并保存数据。" if native else
            "先显示抓包条件检测，确认后登录游戏并自动保存数据。"
        )
        button.setEnabled(not self._stopping and (not battle or not settings.auto_sync_enabled))
        hint = "请先结束战报，再重启同步。" if battle and settings.auto_sync_enabled else ""
        self.window.home_sync_action_hint.setText(hint)
        self.window.home_sync_action_hint.setVisible(bool(hint))
        detail = None
        if not online:
            detail = "离线模式，使用已保存背包。"
        elif settings.paused:
            detail = "连接已暂停；点击“恢复自动同步”核对条件，无需重选工作模式。"
        elif not settings.auto_sync_enabled:
            detail = "自动同步已关闭，已保存背包仍可用于计算。"
        elif self._stopping or self._stop_failed or self._retry_cancelled:
            detail = self._detail or "正在收尾上次同步，已保存背包仍可用于计算。"
        elif native and battle:
            detail = "战报采集中，背包与角色刷新暂时等待，结束后自动恢复。"
        elif native and preparation == 'checking_game':
            detail = "正在检测游戏是否启动。已保存背包仍可用于计算。"
        elif native and preparation == 'waiting_game_exit':
            detail = "组件尚未完成部署或更新，请完全退出游戏，部署完成后再启动并进入游戏场景。"
        elif native and preparation == 'waiting_deployment':
            detail = "组件尚未完成部署或更新，请暂勿启动游戏；查看检测详情，完成部署后再启动并进入游戏场景。"
        elif native and preparation == 'waiting_game':
            detail = "等待启动游戏；请登录并进入游戏场景，等待背包与角色数据同步完成。"
        elif not native and self._process is None:
            detail = self._detail or "等待启动游戏；发现游戏后自动开始背包监听。"
        elif state is None or not service.is_running:
            detail = (state.message if state and state.phase == "error" else
                      "等待游戏内组件就绪；请进入游戏场景，等待同步完成。" if native else self._detail or "正在准备背包监听。")
        elif state.capturing and not state.source_snapshot_ready:
            detail = (
                state.message + "\n请进入游戏场景，等待同步完成；当前显示的仍是上次保存的背包。" if native else
                "抓包监听已就绪，请登录游戏以获取完整背包；当前显示的仍是上次保存的背包。"
            )
        if detail is not None:
            self.window.home_sync_detail.setText(detail)
        badge = getattr(self.window, "home_sync_badge", None)
        if badge is not None:
            if not online or not settings.auto_sync_enabled or settings.paused:
                title, tone = "同步已关闭", "neutral"
            elif self._stopping:
                title, tone = "收尾中", "active"
            elif self._stop_failed:
                title, tone = "同步异常", "error"
            elif self._retry_cancelled:
                title, tone = "等待重试", "warning"
            elif native and battle:
                title, tone = "等待战报结束", "warning"
            elif native and preparation == 'checking_game':
                title, tone = "检测游戏", "active"
            elif native and preparation == 'waiting_game_exit':
                title, tone = "等待退出游戏", "warning"
            elif native and preparation == 'waiting_deployment':
                title, tone = "等待部署", "warning"
            elif native and preparation == 'waiting_game':
                title, tone = "等待游戏", "warning"
            elif not native and self._process is None:
                title, tone = "等待游戏", "warning"
            elif state and state.phase == "error":
                title, tone = "同步异常", "error"
            elif native and preparation == 'waiting_component':
                title, tone = "等待组件", "warning"
            elif state and state.phase == "listening" and state.source_snapshot_ready:
                title, tone = "持续监听", "success"
            elif state and state.phase in {"collecting", "saving"}:
                title, tone = "同步中", "active"
            else:
                title, tone = "等待背包", "warning"
            if badge.text() != title:
                from src.ui.dashboard_widgets import set_status_badge
                set_status_badge(badge, title, tone)
            header = getattr(self.window, "status_lbl", None)
            if header is not None:
                header.setText(title)
                color = {"error": "#f85149", "success": "#3fb950", "neutral": "#8b949e"}.get(tone, "#d2991d")
                header.setStyleSheet(f"color:{color};font-size:12px")

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._stop_watcher()
        if self._dialog is not None:
            self._dialog.context_changed()
        self._stop_inventory()
