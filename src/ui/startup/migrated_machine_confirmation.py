# 仅本次显式只读检测完成后确认迁入机器路径，不在后台恢复旧自动权限。
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from PySide6.QtWidgets import QMessageBox


def confirm_migrated_machine(parent, policy, probe, *, text_catalog=None):
    if policy.machine_revalidation_required is not True:
        return
    if probe.game_path_valid is not True:
        QMessageBox.information(parent, resolve_text_catalog(text_catalog).render('ui.startup.confirm_migrated_machine.qmessagebox'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.startup.confirm_migrated_machine.qmessagebox_2')))
        return
    frozen = policy.settings
    answer = QMessageBox.question(
        parent, resolve_text_catalog(text_catalog).render('ui.startup.confirm_migrated_machine.answer'),
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.startup.confirm_migrated_machine.answer_2', game_executable=f'{frozen.game_executable}')),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer == QMessageBox.StandardButton.Yes and policy.settings == frozen:
        policy.confirm_machine_revalidation()
