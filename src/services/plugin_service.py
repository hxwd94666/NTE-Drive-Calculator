# 将全局插件偏好同步到既有原生会话，显示状态与战报采集独立。
from dataclasses import replace
from threading import RLock

from src.domain.plugin_settings import PluginSettings


class PluginService:
    def __init__(self, *, store, policy, session):
        self.store, self.policy, self.session = store, policy, session
        self._lock = RLock()
        self.status = "未启用"
        self.load_error = ""
        try:
            self.settings = store.load()
        except (ValueError, OSError):
            self.settings = PluginSettings()
            self.load_error = "插件设置读取失败，当前均已关闭；重新保存设置后恢复。"

    def update(self, **changes):
        if any(not isinstance(value, bool) for value in changes.values()):
            raise ValueError("插件开关必须为布尔值")
        with self._lock:
            updated = replace(self.settings, **changes)
            if updated.enabled and not self.policy.allowed("native_load"):
                # Keep editable display preferences in low risk; only enabling a plugin is gated.
                if any(changes.get(key) is True for key in ("cooldown", "enemy_bars")):
                    raise PermissionError("请先在设置中切换到中风险或开发模式，再开启插件。")
            self.store.save(updated)
            self.settings = updated
            self.load_error = ""
            self.status = "设置已保存，等待应用"

    def observe(self, probe):
        with self._lock:
            settings = self.settings
        allowed = self.policy.allowed("native_load") and not self.policy.settings.paused
        if not allowed or not settings.enabled:
            try:
                self.session.configure_hud(PluginSettings().payload(), connect=False)
                status = "未启用" if allowed else "当前模式不可启用" if not self.policy.allowed("native_load") else "连接已暂停"
            except Exception:
                status = "关闭显示未确认，等待连接恢复"
        elif not probe.game_running:
            status = "等待游戏"
        else:
            try:
                result = self.session.configure_hud(settings.payload(), connect=True)
                status = ("当前游戏版本不支持此显示组件" if result.get("rejected") else
                          "运行中" if result.get("installed") else "等待可操作场景")
            except Exception as error:
                status = str(error) or "插件连接失败，请重新检测组件"
        with self._lock:
            if settings == self.settings:
                self.status = status
