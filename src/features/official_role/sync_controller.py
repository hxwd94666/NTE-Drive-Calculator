# 在角色页同步已证实的原生养成字段，并冻结账号、代次和取消边界。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass, field
import threading

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from src.features.official_role.dependencies import OfficialRoleDependencies
from src.integrations.operation_guard import require_operation
from src.services.official_role_profile_service import OfficialRoleProfileService
from src.services.inventory_capture_wait import InventorySyncCancelled


@dataclass(eq=False)
class _SyncJob:
    dependencies: OfficialRoleDependencies
    generation: object
    cancelled: threading.Event = field(default_factory=threading.Event)


class CharacterProfileSyncController(QObject):
    completed = Signal(object, object, object)

    def __init__(
        self, *, parent, dependencies_factory, operation_generation,
        read_profiles, operation_guard, operation_entry, operation_unavailable,
        hotkey_manager, refresh,
        profile_service_factory=OfficialRoleProfileService,
        thread_factory=threading.Thread, connection_paused=None,
        sync_ready=None,
    ) -> None:
        super().__init__(parent)
        self._dependencies_factory = dependencies_factory
        self._operation_generation = operation_generation
        self._read_profiles = read_profiles
        self._operation_guard = operation_guard
        self._operation_entry = operation_entry
        self._operation_unavailable = operation_unavailable
        self._hotkeys = hotkey_manager
        self._refresh = refresh
        self._service_factory = profile_service_factory
        self._thread_factory = thread_factory
        self._connection_paused = connection_paused
        self._sync_ready = sync_ready
        self._job: _SyncJob | None = None
        self._thread = None
        self._closed = False
        self._button = None
        self._result_text = None
        self._edit_controls = ()
        self._enabled_states = ()
        self.completed.connect(self._finish)

    def attach_controls(self, button, edit_controls=(), *, result_text=None) -> None:
        self._button = button
        self._result_text = result_text
        self._edit_controls = tuple(edit_controls)
        button.setText('同步状态')
        button.setToolTip('从游戏同步已确认的等级、突破、技能、好感度和弧盘；未知字段保留原值。')
        button.clicked.connect(self.start)

    def is_running(self) -> bool:
        return self._job is not None

    def request_stop(self) -> None:
        self._show_result('')
        job = self._job
        if job is not None:
            job.cancelled.set()

    def close(self) -> None:
        self._closed = True
        self.request_stop()
        self._hotkeys.stop(owner='character_profile_sync')

    def _show_result(self, message: str) -> None:
        if self._result_text is not None:
            self._result_text.setPlainText(message)
            self._result_text.setVisible(bool(message))
        if self._button is not None:
            self._button.setToolTip(message)

    def _check(self, job: _SyncJob) -> None:
        if (self._closed or job.cancelled.is_set() or self._job is not job
                or self._dependencies_factory() != job.dependencies
                or self._operation_generation() != job.generation):
            raise CancelledError('角色状态同步已取消')
        require_operation(self._operation_guard, 'native_sync')
        if (job.cancelled.is_set() or self._operation_generation() != job.generation
                or self._dependencies_factory() != job.dependencies):
            raise CancelledError('角色状态同步上下文已改变')

    def _set_busy(self, busy: bool) -> None:
        if self._button is not None:
            self._button.setText('取消同步' if busy else '同步状态')
        if busy:
            self._enabled_states = tuple(control.isEnabled() for control in self._edit_controls)
            for control in self._edit_controls:
                control.setEnabled(False)
        else:
            for control, enabled in zip(self._edit_controls, self._enabled_states):
                control.setEnabled(enabled)
            self._enabled_states = ()

    @Slot()
    def start(self) -> None:
        if self._closed:
            return
        if self._job is not None:
            self.request_stop()
            return
        if not self._operation_entry('native_sync', '同步状态'):
            return
        if self._connection_paused is not None and self._connection_paused():
            self._operation_unavailable(
                '同步状态',
                '游戏连接已暂停，程序暂时无法读取角色状态。\n\n'
                '请先在设置中重新确认工作模式，再到工作台开启“自动同步”，登录并进入游戏场景。',
                'detection',
            )
            return
        if self._sync_ready is not None and not self._sync_ready():
            self._operation_unavailable(
                '同步状态',
                '当前没有正在运行的游戏数据同步，程序暂时无法读取角色状态。\n\n'
                '请先：\n'
                '1. 在工作台开启“自动同步”；\n'
                '2. 登录并进入游戏场景；\n'
                '3. 等待工作台显示“同步中”，再返回点击“同步状态”。',
                'home',
            )
            return
        if self._hotkeys.active_owner is not None:
            QMessageBox.information(
                self.parent(), '同步状态',
                '另一个游戏操作正在运行，请先结束该操作，再同步角色状态。',
            )
            return
        job = _SyncJob(self._dependencies_factory(), self._operation_generation())
        self._job = job
        try:
            self._check(job)
            self._show_result('')
            self._set_busy(True)
            self._hotkeys.start(owner='character_profile_sync', on_stop=self.request_stop)
            self._thread = self._thread_factory(
                target=lambda: self._read(job), name='character-profile-sync', daemon=True,
            )
            self._thread.start()
        except Exception as error:
            self._finish(job, None, error)

    def _read(self, job: _SyncJob) -> None:
        payload, error = None, None
        try:
            self._check(job)
            payload = self._read_profiles(check=lambda: self._check(job))
            self._check(job)
        except Exception as exc:
            error = exc
        try:
            self.completed.emit(job, payload, error)
        except RuntimeError:
            pass  # Closing the owner may destroy its Qt signal while the read is finishing.

    @Slot(object, object, object)
    def _finish(self, job, payload, error) -> None:
        if self._job is not job:
            return
        try:
            try:
                self._check(job)
            except Exception:
                return  # Cancelled or superseded results never write or notify a new account.
            if error is not None:
                if not isinstance(error, (CancelledError, InventorySyncCancelled)):
                    code = getattr(error, 'domain_code', '')
                    target = 'deployment' if code in {'NATIVE_CAPABILITY_MISSING', 'NATIVE_MAPPING_UNSUPPORTED'} else 'detection'
                    self._operation_unavailable('同步状态', str(error), target)
                return
            if not isinstance(payload, dict) or not isinstance(payload.get('profiles'), list):
                raise ValueError('游戏组件未返回完整的角色状态列表。')
            try:
                service = self._service_factory(
                    job.dependencies.user_database_path,
                    static_database_path=job.dependencies.static_database_path,
                )
                result = service.patch_native_profiles(payload['profiles'], check=lambda: self._check(job))
            except (CancelledError, InventorySyncCancelled):
                return
            except Exception as exc:
                QMessageBox.warning(self.parent(), '角色状态未保存', str(exc))
                return
            self._check(job)
            if result.saved_count:
                self._refresh()
            self._show_result(result.message)
        except (CancelledError, InventorySyncCancelled):
            pass
        except Exception as exc:
            self._operation_unavailable('同步状态', str(exc), 'detection')
        finally:
            self._job = None
            self._thread = None
            self._hotkeys.stop(owner='character_profile_sync')
            if not self._closed:
                self._set_busy(False)
