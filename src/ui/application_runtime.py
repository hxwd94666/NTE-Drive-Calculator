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
from src.integrations.distribution.metadata import Distribution, DistributionError, load_distribution
from src.integrations.distribution.lock import DataRootLease
from src.integrations.distribution.data_roots import establish_portable_identity
from src.integrations.distribution.data_lifecycle import prepare_data_upgrade
from src.integrations.distribution.release_files import verify_release
from src.integrations.distribution.retired_files import reconcile_release
from src.integrations.distribution.startup_commit import commit_runtime_acceptance, recover_interrupted_acceptance
from src.services.runtime_data_upgrade import apply_runtime_data_upgrades
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
    distribution: Distribution = Distribution()
    lease: DataRootLease | None = None
    recovery_point: Path | None = None
    verified_release: object = None

    def accept_release(self) -> None:
        """窗口完成初始化并显示后才接受发行及数据迁移，不把准备完成冒充可运行。"""
        if self.lease is not None:
            root = self.context.paths.data_root
            commit_runtime_acceptance(root, self.verified_release, self.recovery_point, self.lease)

    def close(self) -> None:
        if self.lease is not None:
            self.lease.close()

    def __enter__(self) -> ApplicationRuntime:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def prepare_application_runtime(
    *,
    package_root: Path | None = None,
    app_dir: Path | None = None,
    data_root: Path | None = None,
    distribution: Distribution | None = None,
) -> ApplicationRuntime:
    """获准启动后准备一次；测试可注入隔离位置，不设置全局账号对象。"""
    resolved_package, resolved_app = resolve_application_roots()
    package_root = resolved_package if package_root is None else Path(package_root)
    app_dir = resolved_app if app_dir is None else Path(app_dir)
    distribution = load_distribution(app_dir) if distribution is None else distribution
    verified = verify_release(app_dir, distribution)
    data_root = (select_runtime_data_root(app_dir, distribution=distribution)
                 if data_root is None else Path(data_root)).absolute()
    lease = DataRootLease(data_root)
    try:
        recover_interrupted_acceptance(data_root, lease)
        if (data_root / "maintenance" / "pending-import.json").exists():
            raise DistributionError("上次迁入尚未完成；请先使用 --resume-data-import 恢复迁入。")
        reconcile_release(app_dir, data_root / "maintenance", verified)
        establish_portable_identity(data_root, distribution, lease)
        recovery = prepare_data_upgrade(data_root, package_root, distribution, lease)
        apply_runtime_data_upgrades(data_root, package_root, recovery, lease)
        seed_shared_database(package_root / "data" / "app_shared.sqlite3", data_root)
        return _prepare_runtime(package_root, app_dir, data_root, distribution, lease, recovery, verified)
    except BaseException:
        lease.close()
        raise


def _prepare_runtime(package_root, app_dir, data_root, distribution, lease, recovery, verified):
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
                                  GlobalThemeSettingsService(paths.global_ui_preferences_file, store=preferences), language,
                                  distribution, lease, recovery, verified)
    except BaseException:
        # 启动失败不发布半套环境依赖；数据迁移自身的事务/备份仍由 DAO 负责。
        for name, value in previous_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        raise
