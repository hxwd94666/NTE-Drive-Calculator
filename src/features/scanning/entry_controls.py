# 提供扫描页面的本地规则编辑入口。
from src.features.scanning.dependencies import current_scanning_dependencies as _current_scanning_dependencies
from src.features.scanning.post_action_dialog import show_scan_post_action_dialog
from src.domain.work_mode import WorkMode
from src.features.input_operation_entry import confirm_operation_recommendation


def open_scan_post_action_manager(self):
    if self.work_mode_provider() == WorkMode.MEDIUM:
        if confirm_operation_recommendation(
            self.dialog_parent, title="弃置锁定管理提示",
            message="当前已是中风险工作模式，建议使用仓库右上角的管理。是否前往使用？",
            action_text="前往",
        ):
            self.navigate("warehouse")
            return  # 接受建议只导航，不同时打开扫描管理。
        # 取消/关闭仅拒绝导航建议，继续原管理入口；不改变执行阶段的权限与取消门禁。
    dependencies = _current_scanning_dependencies(self)
    show_scan_post_action_dialog(
        self.dialog_parent,
        dependencies.user_config_dir,
        dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        static_database_path=dependencies.static_database_path,
        asset_root=dependencies.game_ui_asset_root,
    )
