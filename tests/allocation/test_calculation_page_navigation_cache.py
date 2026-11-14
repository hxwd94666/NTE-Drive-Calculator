# 验证语义一致的计算目录不重建角色卡且角色优先级只在明确加载时恢复。
from types import SimpleNamespace

from src.ui.main_window.main_window_data_mixin import MainWindowDataMixin


def test_equal_catalog_preserves_engine_cards_and_priority():
    events = []
    selector = SimpleNamespace(load_roles=lambda *_args, **_kwargs: events.append("cards"),
                               load_startup_priority_config=lambda: events.append("priority"))
    window = MainWindowDataMixin()
    window.roles_db = {"role": {"character_id": 1, "weights": {"CritBase": 2}}}
    window.sets_db = {"set": {}}
    window.tape_main_stats = ["attack"]
    window.drive_sub_stats = ["crit"]
    window.weapons_db = {}
    window._allocation_icon_paths = {}
    window.stats_config = {}
    window._shape_areas = {}
    frozen = window.scoring_engine = object()
    window.scanning_controller = SimpleNamespace(role_selector=selector)
    window.equipment_presentation = SimpleNamespace(update_catalog=lambda **_kwargs: events.append("equipment"))
    window.identification_controller = SimpleNamespace(update_catalog=lambda **_kwargs: events.append("identify"))
    loaded = ({}, ["attack"], ["crit"], {}, window.roles_db, window.sets_db, {}, object(), {})
    window._apply_allocation_catalog(loaded, reload_priority=False, source_key="same")
    assert not events and window.scoring_engine is frozen
    assert window._allocation_catalog_loaded_key == "same"
    revised = {"role": {"character_id": 1, "weights": {"CritBase": 3}}}
    latest = object()
    window._apply_allocation_catalog(({}, ["attack"], ["crit"], {}, revised, window.sets_db, {}, latest, {}),
                                     reload_priority=False, source_key="changed")
    assert events == ["cards", "equipment", "identify"]
    assert window.scoring_engine is latest and window.roles_db == revised
    assert frozen is not latest


def test_explicit_account_load_restores_priority_even_when_catalog_fields_equal():
    events = []
    window = MainWindowDataMixin()
    window.scanning_controller = SimpleNamespace(role_selector=SimpleNamespace(
        load_roles=lambda *_args, **_kwargs: events.append("cards"),
        load_startup_priority_config=lambda: events.append("priority")))
    window.equipment_presentation = SimpleNamespace(update_catalog=lambda **_kwargs: events.append("equipment"))
    window.identification_controller = SimpleNamespace(update_catalog=lambda **_kwargs: events.append("identify"))
    window._apply_allocation_catalog(({}, [], [], {}, {}, {}, {}, object(), {}), reload_priority=True, source_key="new account")
    assert events == ["cards", "priority", "equipment", "identify"]
