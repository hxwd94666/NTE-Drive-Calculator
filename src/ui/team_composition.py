# 在组合根冻结队伍服务的账号路径与代次，并注册页面草稿的账号切换生命周期。
from src.app.context import CallbackAccountLifecycle
from src.features.teams.controller import TeamController
from src.services.teams.management import TeamService
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.integrations.bundled_resources import bundled_config_dir
from src.services.teams.overview import TeamOverviewService
from src.ui.axis_allocation_composition import build_team_report_matching_service


def build_team_controller(app_context, parent, *, configure_enhancement=None, text_catalog=None):
    def service_provider():
        account = app_context.account
        generation = app_context.generation
        return TeamService(
            account_id=account.active_account_id, database_path=account.user_database_path,
            static_database_path=app_context.paths.static_database_path,
            asset_root=app_context.paths.game_ui_asset_root,
            context_is_current=lambda: app_context.generation == generation and app_context.account == account,
        )

    def overview_provider():
        account, generation = app_context.account, app_context.generation
        return TeamOverviewService(account_id=account.active_account_id, generation=generation,
            # 配装来源和增强关联使用完整主库；公共图片由角色目录独立消费。
            database_path=account.user_database_path, static_database_path=app_context.paths.static_database_path,
            context_is_current=lambda: app_context.generation == generation and app_context.account == account)

    def axis_graduation_provider():
        account, generation = app_context.account, app_context.generation
        inputs = TeamAxisCurrentInputService(account_id=account.active_account_id, generation=generation,
            database_path=account.user_database_path, static_database_path=app_context.paths.static_database_path,
            context_is_current=lambda: app_context.generation == generation and app_context.account == account)
        return TeamAxisGraduationService(current_inputs=inputs,
            semantics_path=bundled_config_dir() / "gameplay_effect_semantics.json",
            scoring_rules_path=app_context.paths.bundled_config_dir / "stats.json")

    controller = TeamController(service_provider=service_provider, overview_service_provider=overview_provider,
        axis_graduation_service_provider=axis_graduation_provider,
        matching_service_provider=lambda: build_team_report_matching_service(app_context),
        configure_enhancement=configure_enhancement, parent=parent, catalog=text_catalog)
    unregister = app_context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=controller.is_running, stop=controller.reset_account,
        rebuild=lambda account: controller.reset_account(), start=lambda: None,
    ))
    controller.destroyed.connect(lambda: unregister())
    return controller
