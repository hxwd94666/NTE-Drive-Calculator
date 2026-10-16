# 仅本次显式只读检测完成后确认迁入机器路径，不在后台恢复旧自动权限。
from PySide6.QtWidgets import QMessageBox


def confirm_migrated_machine(parent, policy, probe):
    if policy.machine_revalidation_required is not True:
        return
    if probe.game_path_valid is not True:
        QMessageBox.information(parent, "迁入路径待确认", "本机游戏路径尚未验证。请在环境设置选择正确位置并重新检测；自动服务与组件清理仍暂停。")
        return
    frozen = policy.settings
    answer = QMessageBox.question(
        parent, "确认迁入的本机路径",
        f"本次只读检测已验证游戏位置：\n{frozen.game_executable}\n\n"
        "原部署记录保留，旧 Loader 工作区未迁入，仍需按环境检测结果重新部署。\n"
        "确认后恢复原工作模式及自动偏好，可能继续待处理组件清理、部署或同步。是否确认？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    )
    if answer == QMessageBox.StandardButton.Yes and policy.settings == frozen:
        policy.confirm_machine_revalidation()
