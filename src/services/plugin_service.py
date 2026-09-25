# 将全局插件偏好同步到既有原生会话，显示状态与战报采集独立。
from dataclasses import replace
from threading import RLock

from src.domain.plugin_settings import PluginSettings


_PLUGIN_FIELDS = {
    "cooldown": frozenset({"cooldown", "ready_cue"}),
    "enemy_bars": frozenset({"enemy_bars", "hp", "unbalance"}),
}


class PluginService:
    def __init__(self, *, store, policy, session):
        self.store, self.policy, self.session = store, policy, session
        self._lock = RLock()
        self._apply_lock = RLock()
        self.status = "未启用"
        self._statuses = {key: "未启用" for key in _PLUGIN_FIELDS}
        self.load_error = ""
        try:
            self.settings = store.load()
        except (ValueError, OSError):
            self.settings = PluginSettings()
            self.load_error = "插件设置读取失败，当前均已关闭；重新保存设置后恢复。"

    @staticmethod
    def _plugin_enabled(settings: PluginSettings, key: str) -> bool:
        return bool(settings.cooldown) if key == "cooldown" else bool(
            settings.enemy_bars and (settings.hp or settings.unbalance)
        )

    def _refresh_aggregate_status(self, settings: PluginSettings) -> None:
        enabled = [
            self._statuses[key] for key in _PLUGIN_FIELDS
            if self._plugin_enabled(settings, key)
        ]
        values = enabled or list(self._statuses.values())
        self.status = values[0] if values and len(set(values)) == 1 else "部分插件状态不同"

    def status_for(self, key: str) -> str:
        if key not in _PLUGIN_FIELDS:
            raise KeyError(key)
        with self._lock:
            return self._statuses[key]

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
            changed = set(changes)
            for key, fields in _PLUGIN_FIELDS.items():
                if changed & fields:
                    self._statuses[key] = (
                        "设置已保存，等待应用"
                        if self._plugin_enabled(updated, key) else "关闭显示待确认"
                    )
            self._refresh_aggregate_status(updated)

    def observe(self, probe):
        with self._apply_lock:
            self._apply(game_running=probe.game_running, connect=True)

    def apply_current(self):
        """Apply UI changes on the existing connection without a full environment probe."""
        with self._apply_lock:
            self._apply(game_running=None, connect=False)

    def _apply(self, *, game_running, connect):
        self.apply_mode_policy()
        with self._lock:
            settings = self.settings
        allowed = self.policy.allowed("native_load") and not self.policy.settings.paused
        statuses = {key: "未启用" for key in _PLUGIN_FIELDS}
        if not allowed or not settings.enabled:
            try:
                self.session.configure_hud(PluginSettings().payload(), connect=False)
                status = "未启用" if allowed else "当前模式不可启用" if not self.policy.allowed("native_load") else "连接已暂停"
            except Exception:
                status = "关闭显示未确认，等待连接恢复"
            for key in statuses:
                if (status == "关闭显示未确认，等待连接恢复" or self._plugin_enabled(settings, key)
                        or not self.policy.allowed("native_load")):
                    statuses[key] = status
        elif game_running is False:
            status = "等待游戏"
            for key in statuses:
                if self._plugin_enabled(settings, key):
                    statuses[key] = status
        else:
            try:
                result = self.session.configure_hud(settings.payload(), connect=connect)
                status = ("当前游戏版本不支持此显示组件" if result.get("rejected") else
                          "运行中" if result.get("installed") else "等待可操作场景")
            except Exception as error:
                status = str(error) or "插件连接失败，请重新检测组件"
                for key in statuses:
                    if not self._plugin_enabled(settings, key):
                        statuses[key] = "关闭显示未确认，等待连接恢复"
            for key in statuses:
                if self._plugin_enabled(settings, key):
                    statuses[key] = status
        with self._lock:
            if settings == self.settings:
                self._statuses = statuses
                self._refresh_aggregate_status(settings)
        if not settings.enabled and not self.policy.allowed("native_sync", automatic=True):
            self.session.close_if_idle()

    def apply_mode_policy(self):
        """Revoke saved switches on downgrade, while retaining display options."""
        with self._lock:
            if self.policy.allowed("native_load"):
                return
            if not (self.settings.cooldown or self.settings.enemy_bars):
                return
            self.settings = replace(self.settings, cooldown=False, enemy_bars=False)
            self._statuses = {key: "当前模式不可启用" for key in _PLUGIN_FIELDS}
            self._refresh_aggregate_status(self.settings)
            try:
                self.store.save(self.settings)
            except OSError:
                self.load_error = "插件已关闭，但关闭状态保存失败，请检查配置目录权限。"
                raise
            self.load_error = ""
