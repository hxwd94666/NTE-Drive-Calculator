# 单队准备持有一次性草稿事务；仅确认后应用选人和增强，取消或过期不破坏原预览。
from concurrent.futures import CancelledError
import sqlite3
from pathlib import Path

from PySide6.QtCore import QObject, QSignalBlocker, Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.domain.teams.calculation import TeamCalculationError, resolve_team_roles
from src.features.allocation.team_axis.team_axis_controls import apply_team_axis_selection, choose_team_axes
from src.services.teams.management import TeamSessionExpired
from src.ui.common.text_catalog import resolve_text_catalog


class TeamCalculationController(QObject):
    def __init__(self, owner, *, clear_preview, current_page, preparation_busy=lambda: False, parent=None):
        super().__init__(parent)
        self.owner, self._clear_preview, self._current_page = owner, clear_preview, current_page
        self._pending = None
        self._sequence = 0
        self._text = resolve_text_catalog(owner.text_catalog).render
        self._preparation_busy = preparation_busy

    def _notice(self, code, ids=()):
        text = (self._text('teams.calculate.catalog_missing', characters='、'.join(map(str, ids)))
                if code == 'catalog_missing' else self._text('teams.calculate.' + code))
        box = QMessageBox(QMessageBox.Warning, self._text('teams.calculate.title'), text,
                          QMessageBox.Ok, self.owner.dialog_parent)
        box.setTextFormat(Qt.PlainText)
        QApplication.beep()
        box.exec()
        box.deleteLater()

    def start(self, team, service):
        owner = self.owner
        if (self._pending is not None or owner.is_running() or owner._team_axis_dialog is not None
                or self._preparation_busy()):
            self._notice('busy')
            return
        owner.build_page()
        try:
            request = service.freeze(team, draft_revision=owner.role_selector.selection_revision)
        except TeamCalculationError as error:
            self._notice(error.code, error.character_ids)
            return
        except (TeamSessionExpired, CancelledError):
            return
        except (ValueError, RuntimeError, OSError, sqlite3.Error):
            self._notice('not_ready')
            return
        self._sequence += 1
        sequence = self._sequence
        self._pending = request
        self._axis_draft = owner.team_axis_selection, owner.team_axis_check.isChecked()
        self._scan_draft = owner.scan_group.checkedId()
        owner.navigate('execute')

        def abandoned():
            if sequence == self._sequence:
                self._pending = None

        def prepared():
            try:
                self._configure(request, service, sequence)
            except (TeamSessionExpired, CancelledError):
                pass
            except TeamCalculationError as error:
                if self._account_current(request):
                    self._notice(error.code, error.character_ids)
            except (ValueError, RuntimeError, OSError, sqlite3.Error):
                if self._account_current(request):
                    self._notice('not_ready')
            finally:
                abandoned()

        if owner._prepare_calculation is None:
            prepared()
        else:
            try:
                owner._prepare_calculation(prepared, abandoned=abandoned)
            except Exception:
                abandoned()
                raise

    def _account_current(self, request):
        context = self.owner.app_context
        return (context.generation == request.generation
                and context.account.active_account_id == request.account_id
                and context.account.user_database_path.resolve() == Path(request.database_path))

    def _checkpoint(self, request, service, sequence):
        if (sequence != self._sequence or self._pending is not request
                or not self._account_current(request) or self._current_page() != 'execute'):
            raise CancelledError
        if self.owner.is_running():
            raise TeamCalculationError('busy')
        if self.owner.role_selector.selection_revision != request.draft_revision:
            raise TeamCalculationError('draft_changed')
        if (self.owner.team_axis_selection is not self._axis_draft[0]
                or self.owner.team_axis_check.isChecked() != self._axis_draft[1]
                or self.owner.scan_group.checkedId() != self._scan_draft):
            raise TeamCalculationError('draft_changed')
        service.verify(request)

    def _configure(self, request, service, sequence):
        owner = self.owner
        self._checkpoint(request, service, sequence)
        resolve_team_roles(request.team.character_ids, owner.role_selector.all_roles)
        selection = choose_team_axes(owner, initial_team_id=request.team.team_id, required_team=request.team)
        if selection is None:
            return
        self._checkpoint(request, service, sequence)
        if len(selection.teams) != 1 or selection.teams[0].team != request.team:
            raise TeamCalculationError('team_changed')
        axis = selection.teams[0].axis
        if (axis.account_id != request.account_id or axis.generation != request.generation
                or axis.user_database_path.resolve() != Path(request.database_path)):
            raise CancelledError
        names = resolve_team_roles(request.team.character_ids, owner.role_selector.all_roles)
        if selection.teams[0].roles != names:
            raise TeamCalculationError('draft_changed')
        # 直到此处原选人、增强开关和旧预览完全不动；确认后的应用在同一 GUI 回合完成。
        selector = owner.role_selector
        inventory_mode = owner.scan_group.button(4)
        if inventory_mode is None:
            raise TeamCalculationError('not_ready')
        old = selector.priority_selection_snapshot()
        old_axis, enabled = owner.team_axis_selection, owner.team_axis_check.isChecked()
        with QSignalBlocker(selector):
            try:
                selector.replace_priority_selection(request.team.character_ids)
                apply_team_axis_selection(owner, selection)
            except Exception:
                selector.restore_priority_selection(old)
                owner.team_axis_selection = old_axis
                with QSignalBlocker(owner.team_axis_check):
                    owner.team_axis_check.setChecked(enabled)
                owner.team_axis_configure.setEnabled(enabled)
                owner.team_axis_search.setEnabled(enabled)
                owner.team_axis_search_label.setEnabled(enabled)
                raise
        # 与战报页轴计算入口一致，提交后使用已有库存，不把“开始计算”变成游戏扫描。
        inventory_mode.setChecked(True)
        self._clear_preview()
        selector.orderChanged.emit()

    def cancel(self):
        self._sequence += 1
        self._pending = None
