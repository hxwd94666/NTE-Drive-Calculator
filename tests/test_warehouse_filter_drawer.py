# 验证仓库筛选抽屉的交互与筛选投影。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _projected_items(source: str) -> list[dict[str, object]]:
    from src.features.inventory.warehouse import warehouse_item_view

    rows = [
        {
            "kind": "core",
            "uid_slot": 1,
            "uid_serial": 11,
            "quality": "orange",
            "suit_id": "Suit6",
            "main_stats": [
                {"property_id": "CritBase", "value": 0.1, "percent": True}
            ],
            "sub_stats": [
                {"property_id": "AtkUp", "value": 0.1, "percent": True},
                {
                    "property_id": "CritDamageBase",
                    "value": 0.1,
                    "percent": True,
                },
            ],
            "equipped": True,
            "locked": True,
        },
        {
            "kind": "module",
            "uid_slot": 2,
            "uid_serial": 22,
            "quality": "purple",
            "geometry": "EquipmentGeometry_Hen3",
            "sub_stats": [
                {"property_id": "AtkUp", "value": 0.1, "percent": True}
            ],
        },
    ]
    return [warehouse_item_view(row, source=source) for row in rows]


def test_projection_keeps_official_filter_ids_for_packet_and_visual_snapshots() -> None:
    for source in ("nte_core", "gamepad"):
        core, module = _projected_items(source)
        assert core["suit_id"] == "Suit6"
        assert core["item_type_id"] == "core:Suit6"
        assert module["shape_id"] == "EquipmentGeometry_Hen3"
        assert module["item_type_id"] == "module:EquipmentGeometry_Hen3"
        assert core["main_stats"][0]["property_id"] == "CritBase"
        assert core["sub_stats"][0]["property_id"] == "AtkUp"


def test_stat_filter_has_identical_results_for_packet_and_visual_snapshots() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    spec = WarehouseFilterSpec(
        kind="core",
        qualities=frozenset(("gold",)),
        main_property_ids=frozenset(("CritBase",)),
        sub_property_ids=frozenset(("AtkUp", "CritDamageBase")),
        min_sub_stat_matches=2,
    )
    for source in ("nte_core", "gamepad"):
        result = filter_projected_warehouse_items(_projected_items(source), spec)
        assert [item["uid"] for item in result] == ["nte-core-1-11"]


def test_state_filter_uses_equipped_locked_discarded_and_other() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    equipped = WarehouseFilterSpec(statuses=frozenset(("equipped",)))
    other = WarehouseFilterSpec(statuses=frozenset(("other",)))
    assert len(filter_projected_warehouse_items(_projected_items("nte_core"), equipped)) == 1
    assert filter_projected_warehouse_items(_projected_items("gamepad"), equipped) == []
    assert len(filter_projected_warehouse_items(_projected_items("gamepad"), other)) == 2
    assert len(
        filter_projected_warehouse_items(
            _projected_items("gamepad"),
            WarehouseFilterSpec(kind="module", statuses=frozenset(("other",))),
        )
    ) == 1

    packet_items = _projected_items("nte_core")
    packet_items.append(
        {
            **packet_items[1],
            "uid": "discarded-drive",
            "locked": False,
            "discarded": True,
        }
    )
    locked = WarehouseFilterSpec(statuses=frozenset(("locked",)))
    discarded = WarehouseFilterSpec(statuses=frozenset(("discarded",)))
    assert [item["uid"] for item in filter_projected_warehouse_items(packet_items, locked)] == [
        "nte-core-1-11"
    ]
    assert [
        item["uid"] for item in filter_projected_warehouse_items(packet_items, discarded)
    ] == ["discarded-drive"]


def test_snapshot_presenter_routes_packet_and_visual_rows_into_the_same_filter(
    monkeypatch,
) -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )
    from src.features.inventory import warehouse_presenter

    class Service:
        source = "nte_core"

        def __init__(self, *_args, **_kwargs):
            pass

        def load_current_snapshot(self):
            return {
                "snapshot_id": 7,
                "source": self.source,
                "rows": [
                    {
                        "kind": "core",
                        "uid_slot": 9,
                        "uid_serial": 10,
                        "quality": "orange",
                        "suit_id": "Suit6",
                        "main_stats": [
                            {
                                "property_id": "CritBase",
                                "value": 0.1,
                                "percent": True,
                            }
                        ],
                    }
                ],
            }

    monkeypatch.setattr(warehouse_presenter, "WarehouseInventoryService", Service)
    spec = WarehouseFilterSpec(main_property_ids=frozenset(("CritBase",)))
    for source in ("nte_core", "gamepad"):
        Service.source = source
        snapshot = warehouse_presenter.load_warehouse_snapshot("unused.sqlite3")
        assert snapshot["source"] == source
        assert len(filter_projected_warehouse_items(snapshot["items"], spec)) == 1


def test_catalog_counts_each_property_once_per_item() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    catalog = build_warehouse_filter_catalog(_projected_items("nte_core"))
    sub_counts = {option.value: option.count for option in catalog.sub_properties}
    assert sub_counts == {"AtkUp": 2, "CritDamageBase": 1}
    assert {option.value for option in catalog.item_types} == {
        "core:Suit6",
        "module:EquipmentGeometry_Hen3",
    }


def test_catalog_matches_game_property_shape_and_status_order() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    main_order = (
        "HPMaxUp",
        "AtkUp",
        "DefUp",
        "CritBase",
        "CritDamageBase",
        "MagBase",
        "UnbalIntensityBase",
        "HealUp",
        "DamageUpCosmosBase",
        "DamageUpNatureBase",
        "DamageUpIncantationBase",
        "DamageUpChaosBase",
        "DamageUpPsycheBase",
        "DamageUpLakshanaBase",
        "DamageUpPsychicallyBase",
    )
    sub_order = (
        "HPMaxUp",
        "AtkUp",
        "DefUp",
        "HPMaxAdd",
        "AtkAdd",
        "DefAdd",
        "CritBase",
        "CritDamageBase",
        "MagBase",
        "UnbalIntensityBase",
        "DamageUpGeneralBase",
    )
    shape_order = (
        "EquipmentGeometry_Hen2",
        "EquipmentGeometry_Shu2",
        "EquipmentGeometry_Hen3",
        "EquipmentGeometry_Shu3",
        "EquipmentGeometry_ZhiJiao1",
        "EquipmentGeometry_ZhiJiao2",
        "EquipmentGeometry_ZhiJiao3",
        "EquipmentGeometry_ZhiJiao4",
        "EquipmentGeometry_Hen4",
        "EquipmentGeometry_Shu4",
        "EquipmentGeometry_Z3",
        "EquipmentGeometry_Z4",
    )
    core_rows = [
        {
            "kind": "core",
            "item_type_id": "core:Suit1",
            "item_type_label": "套装",
            "quality": "gold",
            "main_stats": [
                {"property_id": value, "label": value} for value in reversed(main_order)
            ],
            "sub_stats": [
                {"property_id": value, "label": value} for value in reversed(sub_order)
            ],
        }
    ]
    module_rows = [
        {
            "kind": "module",
            "item_type_id": f"module:{value}",
            "item_type_label": value,
            "quality": "gold",
        }
        for value in reversed(shape_order)
    ]

    core_catalog = build_warehouse_filter_catalog(core_rows, kind="core")
    module_catalog = build_warehouse_filter_catalog(module_rows, kind="module")

    assert tuple(option.value for option in core_catalog.main_properties) == main_order
    assert tuple(option.value for option in core_catalog.sub_properties) == sub_order
    assert tuple(
        option.value.removeprefix("module:") for option in module_catalog.item_types
    ) == shape_order
    assert tuple((option.value, option.label) for option in core_catalog.statuses) == (
        ("equipped", "已装备"),
        ("locked", "已锁定"),
        ("discarded", "已弃置"),
        ("other", "其他"),
    )


def test_drawer_reset_is_draft_only_and_confirm_emits_selected_spec() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1200, 800)
    host.show()
    drawer = WarehouseFilterDrawer(host)
    original = WarehouseFilterSpec(qualities=frozenset(("gold",)))
    drawer.set_items(_projected_items("nte_core"), original)
    drawer.open_for(original)
    app.processEvents()

    assert drawer.isVisible()
    assert 440 <= drawer.width() <= 720
    assert drawer.width() < host.width()
    assert any(
        button.property("filterGroup") == "qualities"
        and button.property("filterValue") == "gold"
        and button.isChecked()
        for button in drawer.findChildren(QPushButton, "warehouseFilterChip")
    )

    emitted = []
    drawer.applied.connect(emitted.append)
    drawer.drive_tab.click()
    assert drawer.draft.kind == "module"
    drawer.findChild(QPushButton, "warehouseFilterReset").click()
    assert drawer.drive_tab.isChecked()
    assert drawer.draft == WarehouseFilterSpec()
    assert emitted == []
    drawer.findChild(QPushButton, "warehouseFilterApply").click()
    assert emitted == [WarehouseFilterSpec()]


def test_drawer_defaults_to_card_page_and_kind_pages_have_visual_type_options(
    monkeypatch,
) -> None:
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory import warehouse_filter_drawer
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    icon_calls = []

    def icon_for(kind):
        def create(official_id, quality):
            icon_calls.append((kind, official_id, quality))
            return QPixmap(8, 8)

        return create

    monkeypatch.setattr(warehouse_filter_drawer, "warehouse_core_pixmap", icon_for("core"))
    monkeypatch.setattr(warehouse_filter_drawer, "warehouse_shape_pixmap", icon_for("module"))

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1200, 360)
    host.show()
    drawer = WarehouseFilterDrawer(host)
    drawer.set_items(_projected_items("gamepad"), WarehouseFilterSpec())
    drawer.open_for(WarehouseFilterSpec())
    app.processEvents()

    assert drawer.draft.kind == "all"
    kind_tabs = drawer.findChildren(QPushButton, "warehouseFilterKindTab")
    assert [button.text() for button in kind_tabs if button.isVisibleTo(drawer)] == [
        "卡带",
        "驱动块",
    ]
    visual_buttons = [
        button
        for button in drawer.findChildren(QPushButton, "warehouseFilterVisualChip")
        if button.isVisibleTo(drawer)
    ]
    assert visual_buttons
    assert all(str(button.property("filterValue")).startswith("core:") for button in visual_buttons)
    assert all(not button.icon().isNull() for button in visual_buttons)
    assert all(button.height() == 56 for button in visual_buttons)
    assert all(button.iconSize().width() == 36 for button in visual_buttons)
    assert all("·" not in button.text() and "件" not in button.text() for button in visual_buttons)
    assert all(button.height() == 38 for button in kind_tabs)
    assert drawer.width() == 500
    section_titles = drawer.findChildren(QLabel, "warehouseFilterSectionTitle")
    assert section_titles
    assert "QLabel#warehouseFilterSectionTitle{color:#58a6ff;font-weight:700;}" in drawer.styleSheet()
    close_button = drawer.findChild(QToolButton, "warehouseFilterClose")
    assert close_button is not None
    assert 32 <= close_button.size().width() <= 36
    assert 32 <= close_button.size().height() <= 36
    assert icon_calls and all(quality == "Gold" for _kind, _id, quality in icon_calls)
    assert "主属性" in {
        label.text() for label in drawer.findChildren(QLabel) if label.isVisibleTo(drawer)
    }
    count_buttons = [
        button
        for button in drawer.findChildren(QPushButton, "warehouseFilterChip")
        if button.property("filterGroup") == "min_sub_stat_matches"
    ]
    assert count_buttons
    assert not any(button.isChecked() for button in count_buttons)
    zero_button = next(
        button for button in count_buttons if button.property("filterValue") == "0"
    )
    zero_button.click()
    assert zero_button.isChecked()
    assert drawer.draft.min_sub_stat_matches == 0
    assert not drawer.scroll.isAncestorOf(drawer.card_tab)
    assert drawer.scroll.verticalScrollBar().maximum() > 0
    drawer.scroll.verticalScrollBar().setValue(drawer.scroll.verticalScrollBar().maximum())

    next(button for button in kind_tabs if button.text() == "驱动块").click()
    app.processEvents()

    assert drawer.draft.kind == "module"
    assert drawer.scroll.verticalScrollBar().value() == 0
    visual_buttons = [
        button
        for button in drawer.findChildren(QPushButton, "warehouseFilterVisualChip")
        if button.isVisibleTo(drawer)
    ]
    assert visual_buttons
    assert all(str(button.property("filterValue")).startswith("module:") for button in visual_buttons)
    assert all(not button.icon().isNull() for button in visual_buttons)
    assert all(button.height() == 56 for button in visual_buttons)
    assert all(button.iconSize().width() == 36 for button in visual_buttons)
    assert all("·" not in button.text() and "件" not in button.text() for button in visual_buttons)
    assert all(quality == "Gold" for _kind, _id, quality in icon_calls)
    assert "主属性" not in {
        label.text() for label in drawer.findChildren(QLabel) if label.isVisibleTo(drawer)
    }
    drawer.scroll.verticalScrollBar().setValue(drawer.scroll.verticalScrollBar().maximum())
    drawer.card_tab.click()
    app.processEvents()
    assert drawer.draft.kind == "core"
    assert drawer.scroll.verticalScrollBar().value() == 0


def test_drive_shape_options_render_in_explicit_ii_iii_iv_game_order(monkeypatch) -> None:
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory import warehouse_filter_drawer
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    shape_ids = [
        "EquipmentGeometry_Hen2",
        "EquipmentGeometry_Shu2",
        "EquipmentGeometry_Hen3",
        "EquipmentGeometry_Shu3",
        "EquipmentGeometry_ZhiJiao1",
        "EquipmentGeometry_ZhiJiao2",
        "EquipmentGeometry_ZhiJiao3",
        "EquipmentGeometry_ZhiJiao4",
        "EquipmentGeometry_Hen4",
        "EquipmentGeometry_Shu4",
        "EquipmentGeometry_Z3",
        "EquipmentGeometry_Z4",
    ]
    rows = [
        {
            "uid": f"drive-{index}",
            "kind": "module",
            "shape_id": shape_id,
            "item_type_id": f"module:{shape_id}",
            "item_type_label": "驱动形状",
            "quality": "gold",
        }
        for index, shape_id in enumerate(reversed(shape_ids))
    ]
    monkeypatch.setattr(
        warehouse_filter_drawer,
        "warehouse_shape_pixmap",
        lambda _shape_id, _quality: QPixmap(8, 8),
    )

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1200, 720)
    host.show()
    drawer = WarehouseFilterDrawer(host)
    module_spec = WarehouseFilterSpec(kind="module")
    drawer.set_items(rows, module_spec)
    drawer.open_for(module_spec)
    app.processEvents()

    section_titles = [
        label.text()
        for label in drawer.findChildren(QLabel, "warehouseFilterSectionTitle")
        if label.isVisibleTo(drawer)
    ]
    assert section_titles[:3] == ["II型驱动", "III型驱动", "IV型驱动"]
    visual_buttons = [
        button
        for button in drawer.findChildren(QPushButton, "warehouseFilterVisualChip")
        if button.isVisibleTo(drawer)
    ]
    assert [
        str(button.property("filterValue")).removeprefix("module:")
        for button in visual_buttons
    ] == shape_ids


def test_snapshot_refresh_normalizes_the_applied_spec_not_a_cancelled_draft() -> None:
    from PySide6.QtWidgets import QApplication, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    QApplication.instance() or QApplication([])
    host = QWidget()
    drawer = WarehouseFilterDrawer(host)
    applied = WarehouseFilterSpec(qualities=frozenset(("gold",)))
    drawer.set_items(_projected_items("nte_core"), applied)
    drawer.open_for(applied)
    drawer._draft = WarehouseFilterSpec(qualities=frozenset(("purple",)))

    normalized = drawer.set_items(_projected_items("nte_core"), applied)

    assert normalized.qualities == frozenset(("gold",))
    assert drawer.draft.qualities == frozenset(("gold",))


def test_filter_handles_two_thousand_visual_rows_without_source_specific_fields() -> None:
    from src.domain.warehouse_filter import (
        WarehouseFilterSpec,
        filter_projected_warehouse_items,
    )

    base = _projected_items("gamepad")[1]
    rows = [{**base, "uid": f"vision-module-{index}"} for index in range(2000)]
    result = filter_projected_warehouse_items(
        rows,
        WarehouseFilterSpec(
            kind="module",
            sub_property_ids=frozenset(("AtkUp",)),
            min_sub_stat_matches=1,
        ),
    )
    assert len(result) == 2000


def test_catalog_keeps_counts_for_two_thousand_rows() -> None:
    from src.domain.warehouse_filter import build_warehouse_filter_catalog

    core, _module = _projected_items("gamepad")
    rows = [{**core, "uid": f"vision-core-{index}"} for index in range(2000)]

    catalog = build_warehouse_filter_catalog(rows, kind="core")

    assert catalog.item_types[0].count == 2000
    assert catalog.main_properties[0].count == 2000
    assert {option.value: option.count for option in catalog.statuses}["other"] == 2000


def test_long_main_stat_label_is_complete_and_compact() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    from src.domain.warehouse_filter import WarehouseFilterSpec
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    host.resize(1200, 800)
    host.show()
    core, _module = _projected_items("gamepad")
    long_label = "光属性异能伤害增强"
    core = {
        **core,
        "main_stats": [
            {
                "property_id": "DamageUpCosmosBase",
                "label": long_label,
                "value": 0.1,
                "percent": True,
            }
        ],
    }
    drawer = WarehouseFilterDrawer(host)
    drawer.set_items((core,), WarehouseFilterSpec())
    drawer.open_for(WarehouseFilterSpec())
    app.processEvents()

    button = next(
        child
        for child in drawer.findChildren(QPushButton, "warehouseFilterChip")
        if child.property("filterGroup") == "main_property_ids"
    )
    assert button.text() == f"{long_label}  1"
    assert button.height() == 42
    assert button.fontMetrics().horizontalAdvance(button.text()) < button.width() - 20

    narrow_host = QWidget()
    narrow_host.resize(480, 800)
    narrow_drawer = WarehouseFilterDrawer(narrow_host)
    assert narrow_drawer._panel_width() == 480


def test_warehouse_page_replaces_inline_filter_combos_with_one_drawer_button() -> None:
    from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

    from src.features.inventory.warehouse_controller import _page_warehouse
    from src.features.inventory.warehouse_filter_drawer import WarehouseFilterDrawer

    class Host:
        def _open_warehouse_state_manager(self):
            pass

        def _save_warehouse_state_changes(self):
            pass

        def _apply_warehouse_filters(self):
            pass

        def _set_warehouse_selected_state(self, _state):
            pass

        def _on_warehouse_selection_changed(self):
            pass

        def _toggle_warehouse_item_state(self, *_args):
            pass

        def _show_warehouse_item_identification(self, *_args):
            pass

    QApplication.instance() or QApplication([])
    host = Host()
    page = _page_warehouse(host)

    assert page.findChild(QPushButton, "warehouseFilterOpen") is not None
    assert page.findChild(WarehouseFilterDrawer, "warehouseFilterDrawer") is not None
    assert page.findChildren(QComboBox) == []
    assert page.findChild(WarehouseFilterDrawer, "warehouseFilterDrawer").draft.kind == "all"
    assert host._warehouse_filter_spec.kind == "all"


def test_warehouse_header_moves_selection_actions_after_count_and_hides_snapshot_id() -> None:
    from PySide6.QtWidgets import QApplication

    from src.features.inventory.warehouse_controller import (
        _apply_warehouse_filters,
        _page_warehouse,
    )

    class Host:
        def _open_warehouse_state_manager(self):
            pass

        def _save_warehouse_state_changes(self):
            pass

        def _apply_warehouse_filters(self):
            pass

        def _set_warehouse_selected_state(self, _state):
            pass

        def _on_warehouse_selection_changed(self):
            pass

        def _toggle_warehouse_item_state(self, *_args):
            pass

        def _show_warehouse_item_identification(self, *_args):
            pass

    QApplication.instance() or QApplication([])
    host = Host()
    page = _page_warehouse(host)
    host._warehouse_all_items = _projected_items("nte_core")
    host._warehouse_snapshot_id = 109
    _apply_warehouse_filters(host)

    title_row = page.layout().itemAt(0).layout()
    title_widgets = [
        title_row.itemAt(index).widget()
        for index in range(title_row.count())
        if title_row.itemAt(index).widget() is not None
    ]
    assert title_widgets.index(host.warehouse_summary) < title_widgets.index(
        host.warehouse_selection_label
    )
    assert host.warehouse_normal_btn in title_widgets
    assert host.warehouse_lock_btn in title_widgets
    assert host.warehouse_discard_btn in title_widgets
    assert host.warehouse_summary.text() == "显示 2 / 2 件"
    assert "快照" not in host.warehouse_summary.text()
