# 验证独立图鉴角色进入基础权重页并保持账号保存与重置语义。
from __future__ import annotations

from pathlib import Path

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.features.configuration.controller import BasicWeightController
from src.features.configuration.dependencies import BasicWeightDependencies
from src.storage.sqlite.user_data_dao import UserDataDao


ROOT = Path(__file__).resolve().parents[1]


def test_reference_roles_load_save_and_reset_basic_weights(tmp_path: Path) -> None:
    paths = ApplicationPaths.from_roots(
        root=ROOT,
        app_dir=ROOT,
        data_root=ROOT,
        bundled_config_dir=ROOT / "config",
        asset_dir=ROOT / "assets",
        app_icon_path=ROOT / "assets" / "app_icon.ico",
    )
    account = AccountContext(
        active_account_id="basic-weight-reference",
        active_account_name="fixture",
        account_data_root=tmp_path,
        user_database_path=tmp_path / "user.sqlite3",
        user_config_dir=tmp_path / "config",
        screenshot_dir=tmp_path / "screenshots",
        log_dir=tmp_path / "logs",
    )
    with UserDataDao(account.user_database_path, account_id=account.active_account_id):
        pass
    dependencies = BasicWeightDependencies.from_app_context(AppContext(paths, account))
    assert dependencies.static_database_path == paths.role_catalog.database_path

    controller = BasicWeightController(dependencies)
    roles = controller.load_form_data()["roles"]
    by_id = {int(row["character_id"]): row for row in roles.values()}
    assert {1042, 1057}.issubset(by_id)
    assert by_id[1042]["weights"]
    assert by_id[1057]["weights"]

    changed = {**by_id[1042], "weights": {**by_id[1042]["weights"], "CritBase": 2.0}}
    controller.save_changes({"新角色": changed}, {1042}, set())
    with UserDataDao(account.user_database_path) as dao:
        saved = dao.get_character_weight_preferences(1042)
    assert saved["source_kind"] == "account"
    assert saved["property_weights"]["CritBase"] == 2.0

    restored = controller.reset_weights((1042,))
    assert 1042 in restored
    with UserDataDao(account.user_database_path) as dao:
        reset = dao.get_character_weight_preferences(1042)
    assert reset["source_kind"] == "default"
