# 将设置页性能需求与账号生命周期注入后台监控，界面不执行 RPC 或日志写入。
from PySide6.QtCore import QObject, QTimer, Signal

from src.services.performance.monitor import PerformanceMonitor
from src.services.performance.trace import PerformanceTrace
from src.integrations.performance.preferences import PerformancePreferences


class PerformanceController(QObject):
    changed = Signal()

    def __init__(self, *, context, policy, read_status, control=None, frames=None, trace_control=None, parent=None):
        super().__init__(parent)
        self.context, self.policy = context, policy
        self.store = PerformancePreferences(context.paths.config_dir / "performance.json")
        self.preferences = self.store.load()
        self.monitor = PerformanceMonitor(read_status, control=control, frames=frames)
        self.trace = PerformanceTrace(trace_control)
        self._maintenance = False
        self.monitor.set_linked(self.preferences["linked"])
        self.preference_error = ""
        self._closed = False
        self._raw = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self.rebuild(context.account)
        self._timer.start()

    @property
    def log_dir(self):
        return self.context.account.log_dir

    def rebuild(self, account):
        self._raw = bool(self.context.account_settings.load("sync").get("raw_capture_enabled"))
        self.refresh()
        self.monitor.set_enabled(self.preferences["overlay"])

    def refresh(self):
        """以账号代次和当前权限配置观测；不启动同步、战报或插件。"""
        if self._closed:
            return
        self.monitor.configure(key=(self.context.account.active_account_id, self.context.generation),
                               log_dir=self.log_dir, raw=self._raw,
                               allowed=self.policy.allowed("native_load") and not self.policy.settings.paused)
        self.trace.configure((self.context.account.active_account_id, self.context.generation),
                             self.policy.allowed('native_load') and not self.policy.settings.paused and not self._maintenance,
                             self.log_dir)
        self.changed.emit()

    def capture_changed(self, settings):
        if settings is not None:
            self._raw = bool(settings.get("raw_capture_enabled"))
            self.refresh()

    def set_enabled(self, enabled):
        if enabled and (not self.policy.allowed("native_load") or self.policy.settings.paused):
            self.preference_error = "当前模式或暂停状态不允许显示游戏内性能"
            self.changed.emit()
            return
        if not self._save("overlay", enabled):
            return
        self.monitor.set_enabled(enabled)
        self.changed.emit()

    def set_linked(self, enabled):
        if not self._save("linked", enabled):
            return
        self.monitor.set_linked(enabled)
        self.changed.emit()

    def snapshot(self):
        return {**self.monitor.snapshot(), "preference_error": self.preference_error, 'trace': self.trace.snapshot()}

    def start_trace(self, services):
        self.refresh()
        self.trace.start(services)
        self.changed.emit()

    def stop_trace(self):
        self.trace.stop()
        self.changed.emit()

    def _save(self, key, value):
        """先保存本机偏好，失败保留旧有效值与明确错误状态。"""
        updated = {**self.preferences, key: bool(value)}
        try:
            self.store.save(updated)
        except OSError:
            self.preference_error = "性能设置保存失败；请检查配置目录权限"
            self.changed.emit()
            return False
        self.preferences = updated
        self.preference_error = ""
        return True

    def stop(self):
        self.trace.stop()
        self.monitor.stop()
        self.changed.emit()

    def suspend_for_plugin_update(self):
        self._maintenance = True
        self.trace.configure((self.context.account.active_account_id, self.context.generation), False, self.log_dir)
        self.monitor.set_maintenance(True)
        self.changed.emit()

    def wait_plugin_update_idle(self, timeout=5):
        return self.trace.wait(timeout) and self.monitor.wait_maintenance_idle(timeout)

    def resume_after_plugin_update(self, *, restore=True):
        self._maintenance = False
        if restore:
            self.refresh()
        else:
            self.monitor.stop()
        self.monitor.set_maintenance(False)
        self.changed.emit()

    def close(self):
        if not self._closed:
            self._closed = True
            self._timer.stop()
            self.trace.close()
            self.monitor.close()
