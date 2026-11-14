# 使用正式启动组合根和隔离空账号验证主窗口挂载与队伍毕业入口；不启动游戏、同步或更新下载。
import json
import os
from pathlib import Path

import pytest


NTE_TEST_TIER = "core"


def test_real_main_window_mounts_team_graduation_and_closes_all_owned_workers(tmp_path):
    if os.environ.get("NTE_TEAM_AXIS_GRADUATION_STARTUP_SMOKE") != "1":
        pytest.skip("explicit isolated full-window startup smoke required")
    from PySide6.QtCore import QCoreApplication, QEvent, QThread
    from tests.support.auto_sync_ui_fixture import application
    from tests.teams.matching.team_report_fixture import wait_for
    from src.domain.teams.models import TeamDraft
    from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
    from src.services.characters.character_shape_bonus_service import SHARED_DATABASE_ENV
    from src.services.characters.workshop_weight_template_service import WORKSHOP_WEIGHT_TEMPLATE_ENV
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.ui.startup.application_runtime import prepare_application_runtime
    from src.ui.app import MainWindow

    app = application()
    root = Path(__file__).resolve().parents[3]
    original_environment = {key: os.environ.get(key) for key in (SHARED_DATABASE_ENV, WORKSHOP_WEIGHT_TEMPLATE_ENV)}
    runtime = prepare_application_runtime(package_root=root, app_dir=root, data_root=tmp_path / "isolated-user")
    window = dialog = None
    try:
        runtime.context.account_settings.save("update", {"never_remind": True})
        # 本例明确不测试教程/更新，使用其正式偏好避免后台网络和无关模态窗口。
        for name in ("guide_seen.json", "quick_start_seen.json"):
            (runtime.context.account.user_config_dir / name).write_text('{"seen":true}', encoding="utf8")
        with UserDataDao(runtime.context.account.user_database_path) as dao:
            team = dao.save_team(TeamDraft("隔离启动合成队伍", (1003, 1072, 1004, 1008)))
        window = MainWindow(runtime)
        window.show()
        app.processEvents()
        assert window.isVisible() and window.work_mode_service.settings.mode.value == "offline"
        window._go("teams")  # 使用正式导航key，不依赖页号或重建控制器。
        wait_for(lambda: not window.team_controller.is_running(), timeout=60)
        window.team_controller.open_graduation(team.team_id)
        dialogs = [w for w in window.findChildren(TeamAxisGraduationDialog) if w.isVisible()]
        assert len(dialogs) == 1
        dialog = dialogs[0]
        wait_for(lambda: not window.team_controller.is_running(), timeout=60)
        assert "尚未" in dialog.headline.text() and not dialog.benchmark.isEnabled()
        assert window.team_controller.axis_graduation.history.team_id == team.team_id
        dialog.reject()
        wait_for(lambda: not dialog.isVisible(), timeout=60)
        window._go("equipment")
        window._go("teams")
        wait_for(lambda: not window.team_controller.is_running(), timeout=60)
        runtime.accept_release()
        window.close()
        wait_for(lambda: not window.isVisible(), timeout=60)
        assert not window.team_controller.is_running()
        assert all(not thread.isRunning() for thread in window.findChildren(QThread))
        print(json.dumps({"fixture_kind": "isolated_real_main_window_graduation_startup_v1",
            "real_runtime_and_main_window": True, "isolated_account_only": True,
            "offline_mode": True, "team_navigation_and_history_entry": True,
            "equipment_navigation_still_available": True, "physical_owned_threads_stopped": True,
            "uac_entrypoint_or_installer_not_tested": True}, ensure_ascii=False))
    finally:
        if dialog is not None and dialog.isVisible():
            dialog.reject()
        if window is not None:
            window.close()
            wait_for(lambda: not window.isVisible(), timeout=60)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        runtime.close()
        for key, value in original_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
