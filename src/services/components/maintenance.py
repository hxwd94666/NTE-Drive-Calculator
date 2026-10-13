# 协调原生功能暂停、连接排空与插件更新，未知结果保留维护门禁。
from src.integrations.native_host_control import NativeHostOutcomeUnknown
from src.integrations.components.native_files import update_native_plugins
from src.observability import OperationContext, log_event


def active_feature_update_hint(owner):
    return {
        'battle_report': '战报正在录制或收尾，请在战报页停止录制并等待保存完成，再更新插件。',
        'automatic_equipment_apply': '自动装配正在操作游戏界面，请等待装配结束或停止装配，再更新插件。',
        'rewind_execution': '倒带正在执行游戏操作，请等待本次执行结束或停止倒带，再更新插件。',
        'scanning': '背包扫描正在进行，请等待扫描结束或停止扫描，再更新插件。',
        'character_profile_sync': '角色养成同步正在进行，请等待同步结束，再更新插件。',
    }.get(owner, '检测到尚未结束的游戏操作，但未识别具体功能；请查看正在执行的任务，暂未更新插件。')


class NativePluginMaintenance:
    """组合根以公开 begin/finish 管理租约，worker 的 run 负责有界排空。"""

    def __init__(self, *, session, sync, performance, check_blockers, reconnect):
        self.session, self.sync, self.performance = session, sync, performance
        self.check_blockers, self.reconnect = check_blockers, reconnect
        self.lease = None
        self.uncertain = False

    def begin(self):
        self.check_blockers()
        self.lease = self.session.reserve_plugin_maintenance()
        self.context = OperationContext.create('native_plugin_update')
        log_event('INFO', 'native_plugin_update.pausing', '插件更新：暂停原生功能', self.context)
        try:
            self.performance.suspend_for_plugin_update()
            self.sync.suspend_for_plugin_update()
        except Exception:
            self.finish(restore=True)
            raise

    def run(self, *, application_root, directory, game_pid, operation_guard,
            update=update_native_plugins):
        def check():
            operation_guard('native_load')

        check()
        if not self.performance.wait_plugin_update_idle():
            raise RuntimeError('性能采样尚未完成收尾，未执行插件更新。')
        self.lease.disconnect(check=check)
        log_event('INFO', 'native_plugin_update.disconnected', '插件更新：采集连接已释放', self.context)
        check()
        try:
            result = update(application_root=application_root, directory=directory,
                            game_pid=game_pid, operation_guard=operation_guard)
            log_event('INFO', 'native_plugin_update.result', '插件更新：宿主结果已核对',
                      self.context, state=result.state)
            return result
        # 未知结果仍占用维护租约；不重发 mutation，也不恢复迟到 HUD 或同步。
        except NativeHostOutcomeUnknown:
            self.uncertain = True
            log_event('WARNING', 'native_plugin_update.unknown', '插件更新结果未知，保持维护暂停', self.context)
            raise

    def finish(self, *, restore):
        # 只有结果已知才能释放租约；恢复遵循本次代次和调用方的 restore 决定。
        if self.lease is None or self.uncertain:
            return False
        self.lease.close()
        self.lease = None
        self.performance.resume_after_plugin_update(restore=restore)
        self.sync.resume_after_plugin_update(restore=restore)
        if restore:
            self.reconnect()
        log_event('INFO', 'native_plugin_update.released', '插件维护窗口已结束', self.context,
                  reconnect_requested=restore)
        return True
