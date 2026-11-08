# 最近装配结果是当前账号会话的可丢弃展示快照，关闭弹窗后可主动查看，不触发重试。
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid

from src.ui.text_catalog import TextActionButton, resolve_text_catalog
from .assembly_surface import AssemblySurface, assembly_is_running
from .slot_selection_dialog import assembly_account_identity


@dataclass(frozen=True)
class AssemblyResultSnapshot:
    identity: tuple
    rows: tuple
    automatic: bool
    title: str
    message: str


def bind_assembly_result(window, surface, identity):
    def remember(title, message):
        if identity == assembly_account_identity(window):
            window._last_assembly_result = AssemblyResultSnapshot(
                identity, surface.progress_model.rows, surface.automatic, title, message
            )
            update_result_button(window)

    surface.result_listener = remember


def update_result_button(window):
    button = getattr(window, "_assembly_result_button", None)
    if button is not None and isValid(button):
        result = getattr(window, "_last_assembly_result", None)
        button.setEnabled(
            bool(result and result.identity == assembly_account_identity(window)) and not assembly_is_running(window)
        )


def open_assembly_result(window):
    if assembly_is_running(window):
        return
    result = getattr(window, "_last_assembly_result", None)
    text = resolve_text_catalog(getattr(window, "text_catalog", None)).render
    if result is None or result.identity != assembly_account_identity(window):
        QMessageBox.information(window, text("assembly.status.last_result"), text("assembly.status.no_result"))
        return
    # 只还原不可变展示事实；不复用运行中 owner、不读库、不创建任务或再次播放警告音。
    surface = AssemblySurface(window, catalog=getattr(window, "text_catalog", None))
    surface.setProperty("assemblyResultReplay", True)
    surface.setAttribute(Qt.WA_DeleteOnClose)
    surface.configure_targets(tuple(row.target for row in result.rows), automatic=result.automatic)
    surface.progress_model.rows = result.rows
    surface.show_result(result.title, result.message)


def clear_assembly_result(window):
    window._last_assembly_result = None
    for surface in window.findChildren(AssemblySurface):
        if surface.property("assemblyResultReplay"):
            surface.close()
    update_result_button(window)


def assembly_result_button(window):
    text = resolve_text_catalog(getattr(window, "text_catalog", None)).render
    button = TextActionButton(text("assembly.status.last_result"))
    button.clicked.connect(lambda: open_assembly_result(window))
    window._assembly_result_button = button
    update_result_button(window)
    return button
