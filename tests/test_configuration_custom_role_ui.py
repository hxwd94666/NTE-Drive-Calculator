# 测试自建角色配置界面。
"""Qt regression coverage for the account-created role board editor."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.features.weighted_allocation import weighted_shell
from src.features.weighted_allocation.weighted_static_catalog import (
    get_weighted_static_catalog,
)
from src.services.custom_character_service import create_custom_character
from src.storage.sqlite.user_data_dao import UserDataDao


def test_weight_focus_commit_without_a_value_change_is_not_dirty() -> None:
    from src.features.configuration.page import save_role_weight_value

    data = {
        "测试角色": {
            "character_id": 1_500_000_002,
            "weights": {"AtkUp": 0.5},
        }
    }
    window = SimpleNamespace(
        _current_config_name="account_weights",
        _config_dirty_character_ids=set(),
        _config_dirty=False,
    )

    # QDoubleSpinBox emits editingFinished when focus leaves the editor, even
    # when the value itself is unchanged.
    save_role_weight_value(
        window, "测试角色", "AtkUp", 0.5, data, config_dir=None,
    )

    assert data["测试角色"]["weights"] == {"AtkUp": 0.5}
    assert window._config_dirty_character_ids == set()
    assert not window._config_dirty


def test_custom_role_board_prevents_a_twenty_first_enabled_cell() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

    from src.features.configuration.page import _add_custom_board

    QApplication.instance() or QApplication([])
    container = QWidget()
    layout = QVBoxLayout(container)
    role_data = {
        "character_id": 1_500_000_000,
        "board_cells": [
            {
                "row": row,
                "column": column,
                "is_enabled": row <= 4,
                "is_locked": False,
            }
            for row in range(1, 6)
            for column in range(1, 6)
        ],
    }
    window = SimpleNamespace(_config_dirty_board_ids=set(), _config_dirty=False)

    _add_custom_board(window, "自建角色", role_data, layout)

    by_position = {
        (int(button.property("boardRow")), int(button.property("boardColumn"))): button
        for button in container.findChildren(QPushButton)
    }
    by_position[(5, 1)].click()

    assert sum(cell["is_enabled"] for cell in role_data["board_cells"]) == 20
    assert not role_data["board_cells"][20]["is_enabled"]
    assert not window._config_dirty
    count_label = container.findChild(QWidget, "customRoleBoardEnabledCount")
    assert count_label is not None
    assert count_label.text() == "已启用 20/20"


def test_custom_role_board_unselected_cells_follow_the_light_theme() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

    from src.app.theme import apply_app_theme, refresh_inline_theme_styles
    from src.features.configuration.page import _add_custom_board

    app = QApplication.instance() or QApplication([])
    apply_app_theme(app, "black")
    container = QWidget()
    layout = QVBoxLayout(container)
    role_data = {
        "character_id": 1_500_000_001,
        "board_cells": [
            {
                "row": row,
                "column": column,
                "is_enabled": row <= 4,
                "is_locked": False,
            }
            for row in range(1, 6)
            for column in range(1, 6)
        ],
    }
    window = SimpleNamespace(_config_dirty_board_ids=set(), _config_dirty=False)

    _add_custom_board(window, "自建角色", role_data, layout)
    apply_app_theme(app, "light")
    refresh_inline_theme_styles(container, app)
    unselected = next(
        button
        for button in container.findChildren(QPushButton)
        if int(button.property("boardRow")) == 5
        and int(button.property("boardColumn")) == 1
    )

    assert "background:#f6f8fa" in unselected.styleSheet()
    assert "#080a0d" not in unselected.styleSheet()
    apply_app_theme(app, "black")


def test_custom_role_is_loaded_into_calculation_role_selector() -> None:
    class Selector:
        def __init__(self) -> None:
            self.loaded_roles = {}

        def load_roles(self, roles, *_args, **_kwargs) -> None:
            self.loaded_roles = dict(roles)

        @staticmethod
        def findChildren(_type):
            return []

    class Status:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value

        def text(self) -> str:
            return self.value

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "user.sqlite3"
        with UserDataDao(database, account_id="selector"):
            pass
        custom = create_custom_character(database, "计算自建角色")
        game_ui_asset_root = Path(__file__).resolve().parents[1] / "assets" / "game_ui"
        suit_id = str(get_weighted_static_catalog(game_ui_asset_root).suits[0]["suit_id"])
        with UserDataDao(database) as user_dao:
            user_dao.save_custom_character_target_suit_id(
                int(custom["character_id"]), suit_id
            )
        selector = Selector()
        window = SimpleNamespace(
            weighted_role_selector=selector,
            weighted_status_label=Status(),
            _weighted_persistence_database_path=database,
            _weighted_preference_overrides={},
        )
        dependencies = SimpleNamespace(
            user_database_path=database,
            game_ui_asset_root=game_ui_asset_root,
        )
        original_dependencies = weighted_shell.weighted_allocation_dependencies
        try:
            weighted_shell.weighted_allocation_dependencies = lambda _window: dependencies
            weighted_shell.refresh_weighted_allocation_page(window)
        finally:
            weighted_shell.weighted_allocation_dependencies = original_dependencies

    assert "计算自建角色" in selector.loaded_roles
    assert selector.loaded_roles["计算自建角色"]["default_set"]
