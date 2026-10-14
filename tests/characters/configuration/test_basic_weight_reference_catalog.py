# 验证正式校验后显式选择 reference 输入，基础权重读取、保存与重置保持同源。
from __future__ import annotations

from pathlib import Path

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.features.configuration.controller import BasicWeightController
from src.features.configuration.dependencies import freeze_basic_weight_dependencies
from src.integrations.role_catalog_release import resolve_role_catalog
from src.storage.sqlite.user_data_dao import UserDataDao

NTE_TEST_TIER = "core"


ROOT = Path(__file__).resolve().parents[3]


def test_reference_roles_load_save_and_reset_basic_weights(tmp_path: Path) -> None:
    reference = resolve_role_catalog(ROOT / "data" / "game_static.sqlite3")
    assert reference is not None and reference.scope == "reference"
    paths = ApplicationPaths.from_roots(
        root=ROOT,
        app_dir=ROOT,
        data_root=ROOT,
        bundled_config_dir=ROOT / "config",
        asset_dir=ROOT / "assets",
        app_icon_path=ROOT / "assets" / "app_icon.ico",
        static_database_path=reference.database_path,
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
    dependencies = freeze_basic_weight_dependencies(AppContext(paths, account))
    # 公开工厂明确消费已校验目录；此用例不要求默认组合根优先或自动后备 reference。
    assert dependencies.static_database_path == reference.database_path

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
