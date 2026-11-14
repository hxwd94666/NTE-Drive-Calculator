# 后台一次读取两个养成页面共用的角色目录和首个角色草稿，过期账号结果静默丢弃。
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from src.app.page_tasks import PageRequest, PageTaskLane
from src.services.progression.models import CultivationRole, CultivationSeed


@dataclass(frozen=True)
class CultivationStartupData:
    roles: tuple[CultivationRole, ...]
    seed: CultivationSeed | None


def read_initial_data(service):
    roles = tuple(service.list_roles())
    return CultivationStartupData(roles, service.load_seed(roles[0].character_id) if roles else None)


class CultivationStartupController(QObject):
    ready = Signal(object)
    failed = Signal()

    def __init__(self, service, identity, *, parent):
        super().__init__(parent)
        self._service, self._identity = service, identity
        self._initial_identity = identity()
        self._closed = False
        self._lane = PageTaskLane(self)

    def _current(self):
        try:
            return not self._closed and self._identity() == self._initial_identity
        except (OSError, RuntimeError):
            return False

    def start(self):
        if self._current():
            # worker 只持有普通 Service；账号 provider 和 QObject 仅在 GUI 线程访问。
            service = self._service
            self._lane.submit(PageRequest(('cultivation-startup', self._initial_identity),
                lambda: read_initial_data(service), self._receive, self._failed))

    def _receive(self, value):
        if self._current():
            self.ready.emit(value)

    def _failed(self, _message):
        if self._current():
            self.failed.emit()

    def close(self):
        self._closed = True
        self._lane.close()
