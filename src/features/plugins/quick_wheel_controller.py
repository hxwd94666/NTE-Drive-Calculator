# GUI 线程派发一次轮盘意图，后台单通道轮询，动作完成前保留编号并显示忙碌。
from concurrent.futures import ThreadPoolExecutor
import secrets
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal, Qt
from src.domain.localization.catalog import localized_text
from src.services.native_session.quick_wheel import QuickWheelConnectionChanged


class QuickWheelController(QObject):
    changed = Signal()
    _completed = Signal(object)

    def __init__(self, *, session, context_key, policy, battle_state, start_battle,
                 stop_battle, hud_settings, change_hud, performance_state, change_performance, restore_window, parent,
                 preferences=None, runtime_allowed=None):
        super().__init__(parent)
        self.session, self.context_key, self.policy = session, context_key, policy
        self.battle_state, self.start_battle, self.stop_battle = battle_state, start_battle, stop_battle
        self.hud_settings, self.change_hud, self.restore_window = hud_settings, change_hud, restore_window
        self.performance_state, self.change_performance = performance_state, change_performance
        self.preferences = preferences
        self.runtime_allowed = runtime_allowed or (lambda: self.policy.allowed('native_load'))
        self.enabled = False
        self.status = '未开启'
        if preferences is not None:
            try:
                self.enabled = (preferences.read() or {}).get('enabled') is True
                if self.enabled:
                    self.status = '等待游戏连接，恢复快捷轮盘'
            except (OSError, ValueError):
                self.status = '轮盘偏好读取失败；请重新选择开关'
        self.owned_count = 0
        self._owner = secrets.randbelow(2**53 - 1) + 1
        self._context = self.context_key()
        self._revision = 0
        self._retry_at = 0.0
        self._retry_delay = 1.0
        self._lease_active = False
        self._ack = self._seen = 0
        self._pending = ''
        self._closing = False
        self._account_suspended = False
        self._close_deadline = 0.0
        self._needs_disable = False
        self._future = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='calc-wheel')
        self._completed.connect(self._receive, Qt.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        if self.enabled:
            self._timer.start()

    def _renew_owner(self):
        # 菜单租约可以恢复，旧选择不能恢复；旧 owner 的悬而未决动作靠租约到期撤销。
        self._owner = secrets.randbelow(2**53 - 1) + 1
        self._ack = self._seen = 0
        self._pending = ''
        self._lease_active = False
        self.owned_count = 0

    def set_enabled(self, enabled):
        if self._closing:
            return
        if enabled:
            self.policy.require('native_load')
        if self.preferences is not None:
            # 显式选择先原子保存；保存失败保留当前有效开关，不假报成功。
            self.preferences.update({'enabled': bool(enabled)}, recover_corrupt=True)
        self._revision += 1
        self._context = self.context_key()
        if enabled:
            self._renew_owner()
        self._retry_at = 0.0
        self._retry_delay = 1.0
        self.enabled = bool(enabled)
        self._needs_disable = not enabled
        self.status = '正在启用' if enabled else '关闭待确认'
        self.changed.emit()
        self._timer.start()
        self._poll()

    def _settle_action(self):
        action = self._pending
        if not action:
            return
        state = self.battle_state()
        settled = (action == 'record_start' and state in (2, 4)
                   or action == 'record_stop' and state in (0, 4))
        settings, statuses = self.hud_settings()
        if action.startswith(('cooldown_', 'enemy_')):
            field = 'cooldown' if action.startswith('cooldown_') else 'enemy_bars'
            wanted = action.endswith('_on')
            settled = settings[field] == wanted and statuses[field] in ('运行中', '未启用')
            if any(word in statuses[field] for word in ('失败', '异常', '不支持', '未确认')):
                # ack 表示本次意图已处理，不把失败包装为开关成功，也不再次派发。
                self.status = statuses[field]
                settled = True
        if action in ('performance_on', 'performance_off'):
            performance = self.performance_state()
            wanted = action.endswith('_on')
            settled = (performance['overlay'] == wanted
                       and performance['overlay_state'] == ('visible' if wanted else 'off'))
            error = performance.get('preference_error')
            if error or performance['overlay_state'] in ('unsupported', 'rejected', 'unconfirmed'):
                self.status = error or '性能显示切换未确认，请在设置页查看状态'
                settled = True  # 本次已处理，但失败不伪装为成功，也不重发切换。
        if settled:
            self._ack, self._pending = self._seen, ''

    def _poll(self):
        if self._account_suspended:
            return
        if self._future is not None:
            return
        if not self._closing and monotonic() < self._retry_at:
            return
        if self._closing and monotonic() >= self._close_deadline:
            self._needs_disable = False
            self.status = '停用未确认，原生轮盘租约将失效'
            return
        if not self.enabled and not self._needs_disable:
            self._timer.stop()
            return
        if self._context != self.context_key():
            # 旧租约靠游戏端有界心跳失效收尾，绝不向新账号连接派发旧动作。
            self._revision += 1
            self._context = self.context_key()
            self._renew_owner()
            self._needs_disable = False
            self._retry_at = monotonic() + 5.1
            self.status = '账号已切换，等待恢复菜单'
            self.changed.emit()
            return
        active = self.enabled and self.runtime_allowed() and not self._closing
        if not active and self._lease_active:
            self._needs_disable = True
        if self.enabled and not active and not self._needs_disable:
            self.status = '等待当前模式允许或组件维护结束'
            self.changed.emit()
            return
        self._settle_action()
        settings, _ = self.hud_settings()
        payload = {'owner': self._owner, 'ack': self._ack, 'enabled': active,
                   'recording': self.battle_state(), 'cooldown': settings['cooldown'],
                   'enemy_bars': settings['enemy_bars'], 'performance': self.performance_state()['overlay'],
                   'busy': bool(self._pending)}
        context = self._context
        revision = self._revision
        self._lease_active = active or self._lease_active
        self._future = self._executor.submit(self.session.exchange_quick_wheel, payload, context)
        self._future.add_done_callback(lambda future: self._completed.emit((context, payload, future, revision)))

    def _receive(self, completed):
        context, payload, future, revision = completed
        self._future = None
        if revision != self._revision or context != self.context_key():
            # 迟到结果既不派发动作也不覆盖新偏好；下次轮询建立当前账号的新 owner。
            if self._closing:
                QTimer.singleShot(0, self, self._poll)
            return
        try:
            result = future.result()
        except Exception as error:
            if self._closing or not self.enabled:
                self._needs_disable = self._lease_active = False
                self._timer.stop()
                self.status = '停用未确认，原生轮盘租约将失效'
            else:
                self._renew_owner()
                self._needs_disable = False
                changed = isinstance(error, QuickWheelConnectionChanged)
                self._retry_at = monotonic() + (0 if changed else max(5.1, self._retry_delay))
                self._retry_delay = min(10.0, self._retry_delay * 2)
                self.status = ('连接已恢复，正在恢复菜单' if changed else localized_text(getattr(self, 'text_catalog', None), 'auto.features.plugins.quick_wheel_controller.receive.f0d4e1b58', '等待连接恢复：{error}', error=f'{error}'))
                self._timer.start()
            self.changed.emit()
            return
        self._retry_delay = 1.0
        self._retry_at = 0.0
        if result is None or not payload['enabled'] and result.get('ready') is False:
            self._needs_disable = self._lease_active = False
            if self.enabled:
                self._renew_owner()
            self.status = '等待当前模式允许' if self.enabled else '已关闭'
            if not self.enabled:
                self._timer.stop()
        elif not self.enabled or self._closing:
            self._needs_disable = True
        else:
            wooden = result.get('wooden', {})
            self.owned_count = wooden.get('ownedCount', 0)
            self.status = wooden.get('error') or ('已启用' if result.get('ready') else '等待可操作场景')
            action_id = result.get('action_id', 0)
            if type(action_id) is int and action_id > self._seen and self.runtime_allowed():
                # 先登记已见编号；动作抛错、弹窗或异步完成都不能重放同一选择。
                self._seen = action_id
                self._dispatch(result.get('action', ''))
        self.changed.emit()
        if self._closing:
            QTimer.singleShot(100, self, self._poll)

    def _dispatch(self, action):
        try:
            if action == 'open_calc':
                self.restore_window()
                self._ack = self._seen
            elif action in ('record_start', 'record_stop'):
                self._pending = action
                (self.start_battle if action == 'record_start' else self.stop_battle)()
                self._settle_action()
                if action == 'record_start' and self.battle_state() == 0:
                    self._ack, self._pending = self._seen, ''
            elif action in ('performance_on', 'performance_off'):
                self._pending = action
                self.change_performance(action.endswith('_on'))
                self._settle_action()
            elif action in ('cooldown_on', 'cooldown_off', 'enemy_on', 'enemy_off'):
                self._pending = action
                self.change_hud('cooldown' if action.startswith('cooldown_') else 'enemy_bars', action.endswith('_on'))
            else:
                self._ack = self._seen
        except Exception as error:
            self._ack, self._pending = self._seen, ''
            self.status = str(error)

    def begin_close(self):
        if self._closing:
            return
        self._closing = True
        self._revision += 1
        self._close_deadline = monotonic() + 6.0
        self._needs_disable = self._lease_active or self._future is not None or self._needs_disable
        self.enabled = False
        self._timer.stop()
        self._poll()

    def stop_for_account(self):
        # 在账号代次推进前撤销菜单动作资格；连接由共享原生会话 owner 收尾。
        self._account_suspended = True
        self._revision += 1
        self._timer.stop()
        self._renew_owner()
        self._needs_disable = False

    def rebuild_account(self, _account):
        self._context = self.context_key()
        self._account_suspended = False
        self._retry_at = monotonic() + 5.1  # 旧游戏端菜单租约有界失效。
        self.status = '账号已切换，等待恢复菜单' if self.enabled else '已关闭'
        self.changed.emit()

    def resume_account(self):
        self._account_suspended = False
        if self.enabled and not self._closing:
            self._timer.start()

    def is_running(self):
        return self._future is not None or self._needs_disable

    def close(self):
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
