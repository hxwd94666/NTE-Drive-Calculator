# 在后台执行显式组件更新，界面只解释前置条件和已确认的操作结果。
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QMessageBox

from src.integrations.native_capture_process import native_game_pid
from src.integrations.operation_guard import bind_execution_guard


class _UpdateWorker(QThread):
    result_ready = Signal(object)

    def __init__(self, work, parent):
        super().__init__(parent)
        self.work = work
        self.result = None

    def run(self):
        try:
            self.result = self.work()
        except Exception as error:
            self.result = error
        self.result_ready.emit(self.result)


class NativePluginUpdateController(QObject):
    changed = Signal()

    def __init__(self, *, context, policy, session, loader, generation, maintenance, parent):
        super().__init__(parent)
        self.context, self.policy, self.session = context, policy, session
        self.loader, self.generation = loader, generation
        self.worker = None
        self.maintenance = maintenance
        self._stopping = False
        self._closing = False
        self._result_handled = False

    @property
    def running(self):
        return self.worker is not None or self.maintenance.uncertain

    def is_running(self):
        # 未消费 finished 前仍持有线程；维护结果未知则只由 running 保留业务门禁。
        return self.worker is not None

    def request(self):
        if self._closing or self.running:
            return
        owner = self.parent()
        try:
            self.policy.require('native_load')
            pid = native_game_pid()
            if pid is None:
                raise RuntimeError('游戏尚未启动，当前没有运行中的插件可热更新。\n'
                                   '要在启动游戏前更新组件，请点击“部署原生组件”；'
                                   '要热更新，请先启动游戏后再点击本按钮。')
            settings = self.policy.settings
            if self.policy.deployment_record.get('loading_method') == 'loader':
                workspace = self.loader.native_workspace_record
                if workspace is None:
                    raise RuntimeError('缺少 Loader 运行目录记录。')
                directory = workspace.directory
            else:
                directory = Path(settings.game_executable).parent
            if not directory.is_absolute():
                raise RuntimeError('游戏组件目录尚未确认。')
        except Exception as error:
            QMessageBox.warning(owner, '更新本方插件', str(error))
            return
        if QMessageBox.question(owner, '更新本方插件',
            'Calc 和游戏可以保持打开。\n将暂时暂停同步、HUD 和性能显示，更新后按当前开关重新连接。'
            '\n战报录制或游戏写操作进行中时不会强行更新；D3D 宿主变化仍需退出游戏。',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        worker = _UpdateWorker(None, self)
        guard = bind_execution_guard(self.policy.require, should_stop=worker.isInterruptionRequested,
                                     generation=self.generation)
        root = self.context.paths.root
        try:
            self.maintenance.begin()
        except Exception as error:
            worker.deleteLater()
            QMessageBox.warning(owner, '更新本方插件', str(error))
            return

        def work():
            return self.maintenance.run(application_root=root, directory=directory,
                                        game_pid=pid, operation_guard=guard)

        worker.work = work
        self.worker = worker
        self._stopping = False
        self._result_handled = False
        self._target_directory = directory
        self._request_generation = self.generation()
        worker.result_ready.connect(self._result)
        worker.finished.connect(self._finished)
        worker.start()
        self.changed.emit()

    def _result(self, result):
        if self._result_handled:
            return
        self._result_handled = True
        current = self.generation() == self._request_generation and not self._stopping
        try:
            if not isinstance(result, Exception) and result.managed_files:
                record = dict(self.policy.deployment_record)
                path_key = 'native_workspace_root' if record.get('loading_method') == 'loader' else 'workspace_path'
                recorded_directory = Path(record.get(path_key) or record.get('game_executable', '')).resolve()
                if recorded_directory.name.lower() == 'htgame.exe':
                    recorded_directory = recorded_directory.parent
                if recorded_directory != self._target_directory.resolve():
                    raise RuntimeError('插件已更新，但部署目录记录已改变；请核对组件后恢复功能。')
                key = 'native_workspace_files' if record.get('loading_method') == 'loader' else 'managed_files'
                record[key] = {**record.get(key, {}), **result.managed_files}
                record['deployment_layout'] = result.layout
                self.policy.update_deployment(record)
        except Exception as error:
            self.maintenance.uncertain = True
            result = error
        self.maintenance.finish(restore=current)
        if not current:
            return
        if isinstance(result, Exception):
            detail = str(result)
            if self.maintenance.uncertain:
                detail += '\n更新或登记未确认，相关原生功能保持暂停；不要重复更新，请核对恢复。'
            QMessageBox.warning(self.parent(), '更新本方插件', detail)
        else:
            QMessageBox.information(self.parent(), '更新本方插件', result.detail +
                                    '\n已恢复功能调度，正在按当前开关重新连接；各功能就绪请查看对应状态。')

    def _finished(self):
        worker = self.worker
        if worker is not None:
            # 已接受更新仍登记一次；停止只撤销重连与弹窗，不撤销外部已生效事实。
            self._result(worker.result)
            self.worker = None
            worker.deleteLater()
        self.changed.emit()

    def stop(self):
        # GUI 只请求取消，线程及其父对象由窗口/账号物理退场门禁继续持有。
        self._stopping = True
        if self.worker is not None:
            self.worker.requestInterruption()

    def begin_close(self):
        self._closing = True
        self.stop()

    def stop_for_account(self):
        self.stop()
        if self.is_running():
            raise RuntimeError('插件更新正在收尾，请等待结束后再切换账号。')
