# 首次平台启动明确选择程序目录外的数据位置，取消不初始化账号。
from pathlib import Path

from src.integrations.application_runtime_paths import resolve_application_roots
from src.integrations.distribution.data_roots import external_selection_is_first, select_external_data_root
from src.integrations.distribution.metadata import DistributionError, load_distribution
from src.integrations.distribution.release_files import verify_release


def choose_first_external_root() -> bool:
    _package, app_dir = resolve_application_roots()
    distribution = load_distribution(app_dir)
    if distribution.data_policy != "external":
        return True
    if not external_selection_is_first(distribution):
        return True
    verify_release(app_dir, distribution)
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from src.ui.gui_startup import prepare_process_dpi_awareness
    prepare_process_dpi_awareness()
    app = QApplication.instance() or QApplication([])
    answer = QMessageBox.question(
        None, "选择数据保存位置",
        "平台更新可能清空程序目录。请选择程序目录之外的固定空文件夹保存账号与设置。\n"
        "已有数据请取消并恢复原位置记录，或用维护命令显式迁入；不自动创建替代账号。",
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Ok:
        return False
    chosen = QFileDialog.getExistingDirectory(None, "选择程序目录外的数据文件夹")
    if not chosen:
        return False
    root = Path(chosen).absolute()
    if root.resolve() == app_dir.resolve() or app_dir.resolve() in root.resolve().parents:
        raise DistributionError("数据位置须在程序目录之外；请重新启动并选择独立文件夹。")
    select_external_data_root(distribution, root)
    # QApplication 必须一直存活到正式 GUI 接管；instance 本身不持有 Python 强引用。
    choose_first_external_root.application = app
    return True
