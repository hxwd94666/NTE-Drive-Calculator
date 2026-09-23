# 提供扫描页面的本地规则编辑入口。
from src.features.scanning.dependencies import current_scanning_dependencies as _current_scanning_dependencies
from src.features.scanning.post_action_dialog import show_scan_post_action_dialog


def open_scan_post_action_manager(self):
    dependencies = _current_scanning_dependencies(self)
    show_scan_post_action_dialog(
        self.dialog_parent,
        dependencies.user_config_dir,
        dependencies.config_dir,
        user_database_path=dependencies.user_database_path,
        static_database_path=dependencies.static_database_path,
        asset_root=dependencies.game_ui_asset_root,
    )
