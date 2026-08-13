# 验证配装角色排序、定位与卡带数值加载。
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.features.inventory import equipment_display_view
from src.features.inventory import equipment_plan_optimizer
from src.features.inventory.equipment_master_detail_view import (
    sorted_equipment_role_states,
)
from src.features.inventory.equipment_plan_renderer import _allocation_lock_icon
from src.optimizer.contracts import ROLE_TOTAL_SCORE
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.ui.equipment_presentation import _equip_card
from src.ui.equipment_state_icons import warehouse_lock_icon


def test_loadout_roles_sort_by_score_descending_then_name() -> None:
    roles = sorted_equipment_role_states(
        {
            "低分": {ROLE_TOTAL_SCORE: 10},
            "乙": {ROLE_TOTAL_SCORE: 80},
            "甲": {ROLE_TOTAL_SCORE: 80},
            "无效": {ROLE_TOTAL_SCORE: float("nan")},
        }
    )

    assert [name for name, _state in roles] == ["乙", "甲", "低分", "无效"]


def test_loadout_lock_icon_is_the_shared_warehouse_artwork() -> None:
    app = QApplication.instance() or QApplication([])
    del app

    loadout = _allocation_lock_icon(True).pixmap(20, 20).toImage()
    warehouse = warehouse_lock_icon(True, size=20).pixmap(20, 20).toImage()

    assert loadout == warehouse
    assert loadout.pixelColor(10, 11).name().casefold() == "#e3b341"



def test_saved_tape_projection_uses_the_full_level_main_value() -> None:
    tape = equipment_plan_optimizer._sqlite_inventory_item_display(
        {
            "kind": "core",
            "uid_slot": 1,
            "uid_serial": 2,
            "item_id": "core_1",
            "suit_id": "suit_1",
            "quality": "orange",
            "main_stats": [{"property_id": "AtkUp", "value": 0.125, "percent": True}],
            "sub_stats": [],
        },
        {"suit_1": "测试空幕"},
    )

    assert tape["main_stats"] == "攻击力%"
    assert tape["main_value"] == 37.5
    assert tape["_role_main_stats"] == {"攻击力%": 12.5}


def test_saved_tape_card_displays_its_exact_main_value() -> None:
    app = QApplication.instance() or QApplication([])
    del app

    class Presentation:
        @staticmethod
        def _stat_w(*_args):
            return 1.0

        @staticmethod
        def _stat_c(*_args):
            return "#58a6ff"

    card = _equip_card(
        Presentation(),
        "测试空幕",
        "攻击力%",
        {},
        None,
        "nte-core-1-2",
        {},
        main_value=12.5,
        card_variant="inventory",
    )

    assert any(label.text() == "攻击力% 12.5%" for label in card.findChildren(QLabel))
    card.deleteLater()

def test_saved_plan_without_diff_does_not_project_whole_inventory(
    monkeypatch,
) -> None:
    def unexpected_projection(*_args, **_kwargs):
        raise AssertionError("inventory diff projection should stay lazy")

    monkeypatch.setattr(
        equipment_plan_optimizer,
        "_sqlite_inventory_item_display",
        unexpected_projection,
    )
    state = equipment_plan_optimizer._sqlite_plan_display_state(
        {
            "plan_id": 1,
            "source_snapshot_id": 2,
            "score": 0,
            "payload": {},
            "assignments": [],
            "allocation_locked": False,
        },
        object(),
        object(),
        inventory_by_snapshot={
            2: {
                (1, 1): {
                    "kind": "module",
                    "uid_slot": 1,
                    "uid_serial": 1,
                    "virtual": False,
                }
            }
        },
        shape_cells={},
        suit_names={},
        attribute_ids=set(),
    )

    assert state[ROLE_TOTAL_SCORE] == 0.0


def test_game_loader_reuses_preloaded_saved_states(monkeypatch, tmp_path) -> None:
    class FakeDao:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeProjectionService:
        def __init__(self, *_args):
            pass

        def project_current(self):
            return SimpleNamespace(supported=False, message="unsupported")

    monkeypatch.setattr(equipment_display_view, "UserDataDao", FakeDao)
    monkeypatch.setattr(equipment_display_view, "StaticGameDataDao", FakeDao)
    monkeypatch.setattr(
        "src.services.game_loadout_projection_service.GameLoadoutProjectionService",
        FakeProjectionService,
    )
    monkeypatch.setattr(
        equipment_display_view,
        "_load_sqlite_equipment_display_states",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("saved plans must come from the cache")
        ),
    )

    cached = {"角色": {ROLE_TOTAL_SCORE: 123.0}}
    result = equipment_display_view._load_game_equipment_display_states(
        tmp_path / "user.sqlite3",
        static_database_path=tmp_path / "static.sqlite3",
        saved_states=cached,
    )

    assert result["saved_states"] == cached


def test_game_asset_catalog_caches_resolved_paths(tmp_path: Path) -> None:
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"png")
    (tmp_path / "manifest.json").write_text(
        '{"characters":{"1":"icon.png"}}',
        encoding="utf-8",
    )
    catalog = GameUiAssetCatalog(tmp_path)
    catalog._resolve.cache_clear()

    assert catalog.character_icon(1) == icon.resolve()
    assert catalog.character_icon(1) == icon.resolve()
    assert catalog._resolve.cache_info().hits == 1


def test_calculation_result_forwards_the_tape_main_value_to_its_card() -> None:
    source = Path("src/ui/equipment_presentation.py").read_text(encoding="utf-8")

    assert 'main_value=getattr(tape, "main_value", None)' in source
