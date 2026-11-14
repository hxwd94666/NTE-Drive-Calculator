# 首次平台启动明确选择程序目录外的数据位置，取消不初始化账号。
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from pathlib import Path

from src.integrations.runtime.application_runtime_paths import resolve_application_roots
from src.integrations.distribution.data_roots import external_selection_is_first, select_external_data_root
from src.integrations.distribution.metadata import DistributionError, load_distribution
from src.integrations.distribution.release_files import verify_release


def confirm_legacy_account_identity_repair(root: Path, count: int, *, text_catalog=None) -> bool:
    """旧版导入曾保留源库 default 标识；修复须由用户确认，默认取消并保留原数据。"""
    from PySide6.QtWidgets import QApplication, QMessageBox
    from src.ui.startup.gui_startup import prepare_process_dpi_awareness
    prepare_process_dpi_awareness()
    app = QApplication.instance() or QApplication([])
    confirm_legacy_account_identity_repair.application = app
    QApplication.beep()
    answer = QMessageBox.warning(
        None, resolve_text_catalog(text_catalog).render('ui.startup.confirm_legacy_account_identity_repair.answer'),
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.startup.confirm_legacy_account_identity_repair.answer_2', count=f'{count}', root=f'{root}')),
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    return answer == QMessageBox.StandardButton.Ok


def choose_first_external_root(*, text_catalog=None) -> bool:
    _package, app_dir = resolve_application_roots()
    distribution = load_distribution(app_dir)
    if distribution.data_policy != "external":
        return True
    if not external_selection_is_first(distribution):
        return True
    verify_release(app_dir, distribution)
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from src.ui.startup.gui_startup import prepare_process_dpi_awareness
    prepare_process_dpi_awareness()
    app = QApplication.instance() or QApplication([])
    answer = QMessageBox.question(
        None, resolve_text_catalog(text_catalog).render('ui.startup.choose_first_external_root.answer'),
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.startup.choose_first_external_root.answer_2')),
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer != QMessageBox.StandardButton.Ok:
        return False
    chosen = QFileDialog.getExistingDirectory(None, resolve_text_catalog(text_catalog).render('ui.startup.choose_first_external_root.chosen'))
    if not chosen:
        return False
    root = Path(chosen).absolute()
    if root.resolve() == app_dir.resolve() or app_dir.resolve() in root.resolve().parents:
        raise DistributionError("数据位置须在程序目录之外；请重新启动并选择独立文件夹。")
    select_external_data_root(distribution, root)
    # QApplication 必须一直存活到正式 GUI 接管；instance 本身不持有 Python 强引用。
    choose_first_external_root.application = app
    return True
