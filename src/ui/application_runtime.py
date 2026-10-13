# 显式组合启动路径、账号和全局偏好，供正式入口与隔离开发宿主共用。
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from src.app.constants import ACCOUNT_USER_FILES, CORE_CONFIG_FILES
from src.app.context import AppContext, ApplicationPaths
from src.app.shared_data_seed import seed_shared_database
from src.features.accounts.manager import AccountManager
from src.features.inventory.warehouse.cards import configure_warehouse_view_template_roots
from src.features.scanning.file_lifecycle import iter_image_files
from src.integrations.application_runtime_paths import resolve_application_roots, select_runtime_data_root
from src.services.characters.character_shape_bonus_service import SHARED_DATABASE_ENV
from src.services.preferences.global_theme_settings_service import GlobalThemeSettingsService
from src.services.preferences.language_settings_service import LanguageSettingsService
from src.integrations.preferences.json_store import LocalPreferencesStore
from src.integrations.preferences.locale_files import LocaleFiles
from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """组合根持有的运行期依赖；账号切换由同一个 context 推进代次。"""

    context: AppContext
    accounts: AccountManager
    theme_settings: GlobalThemeSettingsService
    language_settings: LanguageSettingsService


def prepare_application_runtime(
    *,
    package_root: Path | None = None,
    app_dir: Path | None = None,
    data_root: Path | None = None,
) -> ApplicationRuntime:
    """获准启动后准备一次；测试可注入隔离位置，不设置全局账号对象。"""
    resolved_package, resolved_app = resolve_application_roots()
    package_root = resolved_package if package_root is None else Path(package_root)
    app_dir = resolved_app if app_dir is None else Path(app_dir)
    data_root = select_runtime_data_root(app_dir) if data_root is None else Path(data_root)
    seed_shared_database(package_root / "data" / "app_shared.sqlite3", data_root)
    paths = ApplicationPaths.from_roots(
        root=package_root,
        app_dir=app_dir,
        data_root=data_root,
        bundled_config_dir=package_root / "config",
        asset_dir=package_root / "assets",
        app_icon_path=package_root / "assets" / "app_icon.ico",
    )
    # 尚存的环境路径消费者在显式启动时接线，后续按实际业务迁为直接注入。
    previous_environment = {name: os.environ.get(name) for name in (SHARED_DATABASE_ENV, WORKSHOP_WEIGHT_TEMPLATE_ENV)}
    os.environ[SHARED_DATABASE_ENV] = str(paths.shared_database_path)
    os.environ[WORKSHOP_WEIGHT_TEMPLATE_ENV] = str(paths.workshop_weight_template_file)
    try:
        accounts = AccountManager(paths.data_root, paths.bundled_config_dir, iter_image_files, CORE_CONFIG_FILES, ACCOUNT_USER_FILES)
        context = AppContext(paths, accounts.initialize())
        configure_warehouse_view_template_roots(
            paths.template_dir, paths.bundled_config_dir / "templates", asset_root=paths.game_ui_asset_root,
        )
        # 主题与语言共享单一字段合并 owner；目录在 Qt 窗口创建前完整校验并冻结。
        preferences = LocalPreferencesStore(paths.global_ui_preferences_file)
        language = LanguageSettingsService(preferences, LocaleFiles(paths.bundled_locale_dir, paths.locale_patch_dir))
        language.startup()
        return ApplicationRuntime(context, accounts,
                                  GlobalThemeSettingsService(paths.global_ui_preferences_file, store=preferences), language)
    except BaseException:
        # 启动失败不发布半套环境依赖；数据迁移自身的事务/备份仍由 DAO 负责。
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        raise
